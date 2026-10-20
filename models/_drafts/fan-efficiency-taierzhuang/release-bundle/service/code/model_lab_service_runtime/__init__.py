"""Small, self-contained MLflow runtime for one explicitly supplied model source.

Only this package and the model's ``src/model.py`` are copied into a service
bundle. Loading a bundle executes that model source, as MLflow Python models do.
"""

from __future__ import annotations

import hashlib
import importlib
import math
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
import re
import runpy
from zoneinfo import ZoneInfo

import cloudpickle
import mlflow.pyfunc
import pandas as pd


# The company IoT gateway answers one call with at most a few thousand samples,
# so a single call cannot cover a whole target day at a fine interval. Six hours
# is the widest window the company allows and stays inside that sample budget at
# the finest supported interval, so longer declared windows are fetched as
# adjacent chunks and merged before selection.
_POINT_CHUNK = timedelta(hours=6)

# Time strategies whose start field may be absent from the platform request. The
# runtime then resolves the window itself: the latest complete period for
# ``latest-complete``, and the current local day for ``target-date-to-now``. The
# company deployment template treats target_date as an optional field that falls
# back to the system date, so an online model must not require it.
_OPTIONAL_START_STRATEGIES = frozenset({"latest-complete", "target-date-to-now"})


class SourceModel(mlflow.pyfunc.PythonModel):
    """Load a single model source, verify its digest, then call ``predict``."""

    # MLflow runs ``load_context`` on the in-memory instance before it writes
    # ``python_model.pkl``, so these attributes would otherwise be persisted. Their
    # values are callables taken from the model source and pickled by value together
    # with that source's module globals, which include build-host paths: a bundle
    # built on Linux then carries a ``PosixPath`` and cannot be loaded on Windows
    # (``NotImplementedError: cannot instantiate 'PosixPath' on your system``).
    # ``load_context`` rebuilds all of it on every load, so none of it is worth
    # persisting and a bundle stays loadable on any operating system.
    _REBUILT_ON_LOAD = ("_predictor", "_feature_preparer")

    def __getstate__(self) -> dict[str, object]:
        state = dict(self.__dict__)
        for name in self._REBUILT_ON_LOAD:
            state.pop(name, None)
        return state

    def load_context(self, context: mlflow.pyfunc.PythonModelContext) -> None:
        with open(context.artifacts["logic_bundle"], "rb") as stream:
            logic = cloudpickle.load(stream)
        with open(context.artifacts["custom_sources"], "rb") as stream:
            sources = cloudpickle.load(stream)
        source = Path(context.artifacts["model_file"])
        source_bytes = source.read_bytes()
        if not isinstance(logic, dict) or not isinstance(sources, dict):
            raise ValueError("invalid model-lab logic or source bundle")
        if (
            hashlib.sha256(source_bytes).hexdigest() != logic.get("source_sha256")
            or sources.get("model") != source_bytes.decode("utf-8")
        ):
            raise ValueError("model source differs from the recorded bundle")
        inputs = logic.get("inputs")
        outputs = logic.get("outputs")
        if (
            not isinstance(inputs, list)
            or not inputs
            or not all(isinstance(name, str) for name in inputs)
            or not isinstance(outputs, list)
            or not outputs
            or not all(isinstance(name, str) for name in outputs)
        ):
            raise ValueError("invalid model-lab input or output contract")
        namespace = runpy.run_path(str(source), run_name="model_lab_packaged_model")
        predictor = namespace.get("predict")
        if not callable(predictor):
            raise ValueError("model source must expose predict(inputs)")
        self._predictor = predictor
        self._feature_preparer = namespace.get("prepare_features")
        self._inputs = tuple(inputs)
        self._outputs = tuple(outputs)
        serving = logic.get("serving")
        if serving is not None:
            if not isinstance(serving, dict):
                raise ValueError("invalid packaged point-serving contract")
            serving.setdefault("feature_adapter", "latest")
            if serving["feature_adapter"] not in {"latest", "source-history-v1"}:
                raise ValueError("invalid packaged point-serving feature adapter")
            if serving["feature_adapter"] == "source-history-v1" and not callable(self._feature_preparer):
                raise ValueError("source-history-v1 requires prepare_features(raw, request)")
            if "bindings" in serving:
                required = {
                    "factory_field", "start_field", "rolling_window", "interval",
                    "factory_code", "bindings", "time_strategy", "timezone",
                    "iot_timestamp_timezone", "control_fields", "evaluation_fields", "feature_adapter",
                }
                if serving.get("time_strategy") == "latest-complete":
                    required.add("time_period")
                    if "time_lag" in serving:
                        required.add("time_lag")
                bindings = serving.get("bindings")
                if (
                    set(serving) != required
                    or not all(isinstance(serving[name], str) and serving[name] for name in required - {"bindings", "control_fields", "evaluation_fields"})
                    or not isinstance(bindings, list)
                    or not bindings
                ):
                    raise ValueError("invalid packaged multi-point contract")
                binding_names: list[str] = []
                for binding in bindings:
                    if (
                        not isinstance(binding, dict)
                        or set(binding) != {"input", "codes", "aggregation"}
                        or not isinstance(binding["input"], str)
                        or not binding["input"]
                        or not isinstance(binding["codes"], list)
                        or not binding["codes"]
                        or any(not isinstance(code, str) or not code for code in binding["codes"])
                        or not isinstance(binding["aggregation"], str)
                        or (len(binding["codes"]) == 1 and binding["aggregation"] != "single")
                        or (len(binding["codes"]) > 1 and binding["aggregation"] not in {"sum", "mean"})
                    ):
                        raise ValueError("invalid packaged multi-point binding")
                    binding_names.append(binding["input"])
                if len(binding_names) != len(set(binding_names)):
                    raise ValueError("duplicate packaged point field")
                if serving["feature_adapter"] == "latest" and tuple(binding_names + list(serving["control_fields"])) != self._inputs:
                    raise ValueError("latest point fields do not match algorithm inputs")
            else:
                required = {
                    "factory_field", "point_field", "start_field",
                    "rolling_window", "interval", "factory_code", "point_code",
                    "time_strategy", "timezone", "iot_timestamp_timezone",
                    "control_fields", "evaluation_fields", "feature_adapter",
                }
                if serving.get("time_strategy") == "latest-complete":
                    required.add("time_period")
                    if "time_lag" in serving:
                        required.add("time_lag")
                if set(serving) != required or not all(
                    isinstance(serving[name], str) and serving[name]
                    for name in required - {"control_fields", "evaluation_fields"}
                ):
                    raise ValueError("invalid packaged point-serving contract")
            if any(
                not isinstance(serving[name], (list, tuple))
                or any(not isinstance(item, str) or not item for item in serving[name])
                for name in ("control_fields", "evaluation_fields")
            ):
                raise ValueError("invalid packaged field roles")
            if serving["time_strategy"] not in {"explicit-start", "target-date-day", "target-date-to-now", "latest-complete"}:
                raise ValueError("invalid packaged time strategy")
            if serving["time_strategy"] == "latest-complete":
                match = re.fullmatch(r"([1-9][0-9]*)min", serving["time_period"])
                if serving["feature_adapter"] != "source-history-v1" or match is None or 1440 % int(match.group(1)):
                    raise ValueError("invalid packaged complete-period strategy")
                if "time_lag" in serving:
                    lag_match = re.fullmatch(r"([1-9][0-9]*)min", serving["time_lag"]) if isinstance(serving["time_lag"], str) else None
                    if lag_match is None or int(lag_match.group(1)) % int(match.group(1)):
                        raise ValueError("invalid packaged availability lag")
            for name in ("timezone", "iot_timestamp_timezone"):
                ZoneInfo(serving[name])
        self._serving = serving

    def predict(
        self,
        context: mlflow.pyfunc.PythonModelContext,
        model_input: pd.DataFrame,
        params: dict | None = None,
    ) -> pd.DataFrame:
        if params:
            raise ValueError("this model does not accept prediction parameters")
        if not isinstance(model_input, pd.DataFrame):
            raise TypeError("prediction input must be a pandas DataFrame")
        if self._serving is None:
            missing = [name for name in self._inputs if name not in model_input.columns]
            if missing:
                raise ValueError(f"missing model input columns: {missing}")
            features = model_input.loc[:, self._inputs].copy()
        else:
            serving = self._serving
            multi = "bindings" in serving
            binding_items = (
                serving["bindings"]
                if multi
                else [{"input": serving["point_field"], "codes": [serving["point_code"]], "aggregation": "single"}]
            )
            required = {
                serving["factory_field"],
            } | {binding["input"] for binding in binding_items} | set(serving["control_fields"])
            if serving["time_strategy"] not in _OPTIONAL_START_STRATEGIES:
                required.add(serving["start_field"])
            allowed = required | set(serving["evaluation_fields"]) | {serving["start_field"]}
            if len(model_input) != 1 or not required.issubset(model_input.columns) or not set(model_input.columns).issubset(allowed):
                raise ValueError("point-mode expects exactly one record and its declared fields")
            row = model_input.iloc[0]
            factory = row[serving["factory_field"]]
            if factory != serving["factory_code"]:
                raise ValueError(f"factory {factory!r} is not in this model binding")
            point_map: dict[str, str] = {}
            for binding in binding_items:
                name = binding["input"]
                codes = binding["codes"]
                provided = row[name]
                if (
                    not isinstance(provided, (list, tuple))
                    or len(provided) != len(codes)
                    or any(not isinstance(code, str) for code in provided)
                    or set(provided) != set(codes)
                ):
                    raise ValueError(f"{name} must be the approved point-code array")
                point_map.update({code: code for code in codes})
            start = row.get(serving["start_field"])
            local_zone = ZoneInfo(serving["timezone"])
            if serving["time_strategy"] == "latest-complete":
                if start is None or (isinstance(start, float) and math.isnan(start)) or (isinstance(start, str) and not start.strip()):
                    as_of = datetime.now(timezone.utc).astimezone(local_zone)
                elif isinstance(start, str):
                    if re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})?", start) is None:
                        raise ValueError(f"{serving['start_field']} must be a timestamp with optional UTC offset")
                    try:
                        as_of = datetime.fromisoformat(start.replace("Z", "+00:00"))
                    except ValueError as error:
                        raise ValueError(f"{serving['start_field']} is not a valid timestamp") from error
                    if as_of.tzinfo is None:
                        alternate = as_of.replace(tzinfo=local_zone, fold=1)
                        as_of = as_of.replace(tzinfo=local_zone, fold=0)
                        if alternate.utcoffset() != as_of.utcoffset() or as_of.astimezone(timezone.utc).astimezone(local_zone).replace(tzinfo=None) != as_of.replace(tzinfo=None):
                            raise ValueError(f"{serving['start_field']} is ambiguous or nonexistent in {local_zone}")
                    as_of = as_of.astimezone(local_zone)
                else:
                    raise ValueError(f"{serving['start_field']} must be a timestamp string when supplied")
                period_minutes = int(serving["time_period"][:-3])
                lag_minutes = int(serving.get("time_lag", "0min")[:-3])
                available_at = (as_of.astimezone(timezone.utc) - timedelta(minutes=lag_minutes)).astimezone(local_zone)
                minute_of_day = available_at.hour * 60 + available_at.minute
                day_start = datetime.combine(available_at.date(), time.min, local_zone)
                window_end = day_start + timedelta(minutes=minute_of_day - minute_of_day % period_minutes)
                if window_end.astimezone(timezone.utc).astimezone(local_zone) != window_end:
                    raise ValueError("complete-period boundary is ambiguous or nonexistent")
                window_match = re.fullmatch(r"([1-9][0-9]*)(min|h|d)", serving["rolling_window"])
                if window_match is None:
                    raise ValueError("invalid packaged rolling window")
                amount, unit = window_match.groups()
                duration = timedelta(**{{"min": "minutes", "h": "hours", "d": "days"}[unit]: int(amount)})
                window_start = (window_end.astimezone(timezone.utc) - duration).astimezone(local_zone)
            elif serving["time_strategy"] in {"target-date-day", "target-date-to-now"}:
                open_day = serving["time_strategy"] == "target-date-to-now"
                blank = start is None or (isinstance(start, float) and math.isnan(start)) or (
                    isinstance(start, str) and not start.strip()
                )
                if blank:
                    if not open_day:
                        raise ValueError(f"{serving['start_field']} must be a string")
                    # The platform template treats the target date as optional and
                    # falls back to the system date, so an omitted field means the
                    # current local day rather than a rejected request.
                    requested_date = datetime.now(timezone.utc).astimezone(local_zone).date()
                else:
                    if not isinstance(start, str):
                        raise ValueError(f"{serving['start_field']} must be a string")
                    try:
                        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", start) is None:
                            raise ValueError("expected YYYY-MM-DD")
                        requested_date = date.fromisoformat(start)
                    except ValueError as error:
                        raise ValueError(f"{serving['start_field']} must use YYYY-MM-DD") from error
                window_start = datetime.combine(requested_date, time.min, local_zone)
                window_end = datetime.combine(requested_date + timedelta(days=1), time.min, local_zone)
                if not open_day and window_end.astimezone(timezone.utc) - window_start.astimezone(timezone.utc) != timedelta(days=1):
                    raise ValueError("target-date-day is unsupported for a local day that is not exactly 24 hours")
                if open_day:
                    # An in-progress local day is queried up to the current moment;
                    # a day that already closed keeps its full 24-hour window.
                    now_local = datetime.now(timezone.utc).astimezone(local_zone)
                    if window_start > now_local:
                        raise ValueError(f"{serving['start_field']} must not be in the future")
                    window_end = min(window_end, now_local)
                    if window_end <= window_start:
                        raise ValueError("target-date-to-now requires a positive local day window")
            else:
                if not isinstance(start, str):
                    raise ValueError(f"{serving['start_field']} must be a string")
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})?", start) is None:
                    raise ValueError(f"{serving['start_field']} must include date and time with optional UTC offset")
                try:
                    parsed = datetime.fromisoformat(start.replace("Z", "+00:00"))
                except ValueError as error:
                    raise ValueError(f"{serving['start_field']} is not a valid timestamp") from error
                if parsed.tzinfo is None:
                    alternate = parsed.replace(tzinfo=local_zone, fold=1)
                    parsed = parsed.replace(tzinfo=local_zone, fold=0)
                    if alternate.utcoffset() != parsed.utcoffset() or parsed.astimezone(timezone.utc).astimezone(local_zone).replace(tzinfo=None) != parsed.replace(tzinfo=None):
                        raise ValueError(f"{serving['start_field']} is ambiguous or nonexistent in {local_zone}")
                window_start = parsed.astimezone(local_zone)
                window_match = re.fullmatch(r"([1-9][0-9]*)(min|h|d)", serving["rolling_window"])
                if window_match is None:
                    raise ValueError("invalid packaged rolling window")
                amount, unit = window_match.groups()
                duration = timedelta(**{{"min": "minutes", "h": "hours", "d": "days"}[unit]: int(amount)})
                window_end = (window_start.astimezone(timezone.utc) + duration).astimezone(local_zone)
            query_start = window_start.strftime("%Y-%m-%d %H:%M:%S")
            window_start_utc = window_start.astimezone(timezone.utc)
            window_end_utc = window_end.astimezone(timezone.utc)
            try:
                provider = importlib.import_module("mlops_deploy_iot_armor2_311")
                fetch = provider.fetch_iot_data
            except (ImportError, AttributeError) as error:
                raise RuntimeError("company IoT fetch_iot_data provider is unavailable") from error
            chunk_windows: list[tuple[datetime, datetime]] = []
            if serving["feature_adapter"] == "source-history-v1":
                if window_end_utc - window_start_utc > timedelta(days=7):
                    raise ValueError("history adapter window exceeds the 7-day safety limit")
                if window_start.utcoffset() != window_end.utcoffset():
                    raise ValueError("history adapter cannot query across a timezone offset transition")
                frames: list[pd.DataFrame] = []
                chunk_start = window_start_utc
                while chunk_start < window_end_utc:
                    chunk_end = min(chunk_start + timedelta(hours=4), window_end_utc)
                    chunk_minutes = int((chunk_end - chunk_start).total_seconds() / 60)
                    if chunk_minutes < 1:
                        raise ValueError("history adapter cannot query a sub-minute window")
                    part = fetch(
                        factory_code=factory,
                        points=point_map,
                        start_time=chunk_start.astimezone(local_zone).strftime("%Y-%m-%d %H:%M:%S"),
                        rolling_window=f"{chunk_minutes}min",
                        interval=serving["interval"],
                    )
                    if not isinstance(part, pd.DataFrame) or part.empty:
                        raise ValueError("IoT returned no usable data for a required history chunk")
                    frames.append(part)
                    chunk_windows.append((chunk_start, chunk_end))
                    chunk_start = chunk_end
                raw = pd.concat(frames, ignore_index=True)
            else:
                if window_end_utc - window_start_utc <= _POINT_CHUNK:
                    raw = fetch(
                        factory_code=factory,
                        points=point_map,
                        start_time=query_start,
                        rolling_window=serving["rolling_window"],
                        interval=serving["interval"],
                    )
                else:
                    frames = []
                    chunk_start = window_start_utc
                    while chunk_start < window_end_utc:
                        chunk_end = min(chunk_start + _POINT_CHUNK, window_end_utc)
                        chunk_minutes = int((chunk_end - chunk_start).total_seconds() / 60)
                        if chunk_minutes < 1:
                            raise ValueError("point mode cannot query a sub-minute window")
                        part = fetch(
                            factory_code=factory,
                            points=point_map,
                            start_time=chunk_start.astimezone(local_zone).strftime("%Y-%m-%d %H:%M:%S"),
                            rolling_window=f"{chunk_minutes}min",
                            interval=serving["interval"],
                        )
                        if not isinstance(part, pd.DataFrame) or part.empty:
                            raise ValueError("IoT returned no usable data for a required point chunk")
                        frames.append(part)
                        chunk_windows.append((chunk_start, chunk_end))
                        chunk_start = chunk_end
                    raw = pd.concat(frames, ignore_index=True)
            if not isinstance(raw, pd.DataFrame) or raw.empty:
                raise ValueError("IoT returned no usable data")
            if "data_time" not in raw.columns:
                raise ValueError("IoT response lacks data_time")
            parsed_times: list[datetime] = []
            source_zone = ZoneInfo(serving["iot_timestamp_timezone"])
            for value in raw["data_time"]:
                if not isinstance(value, (str, datetime, pd.Timestamp)):
                    raise ValueError("IoT data_time is not a timestamp")
                try:
                    timestamp = pd.Timestamp(value)
                    if pd.isna(timestamp):
                        raise ValueError("missing time")
                    if timestamp.tzinfo is None:
                        timestamp = timestamp.tz_localize(source_zone, ambiguous="raise", nonexistent="raise")
                    parsed_times.append(timestamp.tz_convert(local_zone).to_pydatetime())
                except (TypeError, ValueError) as error:
                    raise ValueError("IoT data_time is invalid or ambiguous") from error
            adapter_label = "history" if serving["feature_adapter"] == "source-history-v1" else "point"
            for chunk_start, chunk_end in chunk_windows:
                observed = [
                    stamp.astimezone(timezone.utc)
                    for stamp in parsed_times
                    if chunk_start <= stamp.astimezone(timezone.utc) < chunk_end
                ]
                if not observed:
                    raise ValueError(f"IoT returned no usable data in a required {adapter_label} chunk")
                interval_match = re.fullmatch(r"([1-9][0-9]*)(s|min|h)", serving["interval"])
                if interval_match is None:
                    raise ValueError("invalid packaged sampling interval")
                interval_amount, interval_unit = interval_match.groups()
                sample_interval = timedelta(**{
                    {"s": "seconds", "min": "minutes", "h": "hours"}[interval_unit]: int(interval_amount)
                })
                tolerance = sample_interval * 2
                if min(observed) > chunk_start + tolerance or max(observed) < chunk_end - tolerance:
                    raise ValueError(f"IoT required {adapter_label} chunk lacks edge coverage")
            in_window = [window_start_utc <= stamp.astimezone(timezone.utc) < window_end_utc for stamp in parsed_times]
            usable = raw.loc[in_window].copy()
            if usable.empty:
                raise ValueError("IoT returned no usable data in the declared time window")
            usable["_model_lab_utc_time"] = [stamp.astimezone(timezone.utc) for stamp, keep in zip(parsed_times, in_window) if keep]
            usable = usable.sort_values("_model_lab_utc_time")
            if serving["feature_adapter"] == "source-history-v1" or len(chunk_windows) > 1:
                usable = usable.drop_duplicates(subset=["_model_lab_utc_time"], keep="last")
            if serving["feature_adapter"] == "source-history-v1":
                for binding in binding_items:
                    for code in binding["codes"]:
                        if code not in usable.columns:
                            raise ValueError(f"IoT returned no usable data for {code}")
                request = model_input.copy()
                # Validate the requested period, never one inferred from stale rows.
                request.attrs["model_lab_window_start_utc"] = pd.Timestamp(window_start_utc)
                request.attrs["model_lab_window_end_utc"] = pd.Timestamp(window_end_utc)
                features = self._feature_preparer(usable.copy(), request)
                if not isinstance(features, pd.DataFrame) or len(features) != 1 or tuple(features.columns) != self._inputs:
                    raise ValueError("history adapter must return one row with exactly the declared numeric features")
                for name in self._inputs:
                    try:
                        values = pd.to_numeric(features[name], errors="raise")
                    except (TypeError, ValueError) as error:
                        raise ValueError(f"history feature {name} is not numeric") from error
                    if not all(math.isfinite(float(value)) for value in values):
                        raise ValueError(f"history feature {name} is not finite")
                    features[name] = values.astype("float64")
                result = self._predictor(features)
                if not isinstance(result, pd.DataFrame):
                    raise TypeError("model predict must return a pandas DataFrame")
                if tuple(result.columns) != self._outputs or len(result) != len(model_input):
                    raise ValueError("model output does not match the declared contract")
                return result
            feature_values: dict[str, list[float]] = {}
            for binding in binding_items:
                readings: list[float] = []
                absent: list[str] = []
                for code in binding["codes"]:
                    if code not in usable.columns:
                        raise ValueError(f"IoT returned no usable data for {code}")
                    column = usable[code]
                    try:
                        numeric = pd.to_numeric(column, errors="coerce")
                    except (TypeError, ValueError) as error:
                        raise ValueError(f"IoT value for {code} is not numeric") from error
                    if (column.notna() & numeric.isna()).any():
                        raise ValueError(f"IoT value for {code} is not numeric")
                    # Points are sampled independently, so one point can end
                    # earlier than another; the latest available reading of each
                    # point is what is current for this window. A reading that is
                    # present but not finite is a data-quality artefact rather
                    # than a measurement, so it counts as unavailable and is
                    # skipped instead of failing the whole request.
                    values = numeric.dropna()
                    available = [float(item) for item in values if math.isfinite(float(item))]
                    if not available:
                        absent.append(code)
                        continue
                    readings.append(available[-1])
                aggregation = binding["aggregation"]
                if not readings:
                    raise ValueError(f"IoT returned no usable reading for {binding['input']}")
                if aggregation == "mean":
                    feature_values[binding["input"]] = [sum(readings) / len(readings)]
                elif absent:
                    raise ValueError(f"IoT returned no usable reading for {binding['input']}")
                else:
                    feature_values[binding["input"]] = [sum(readings)]
            for name in serving["control_fields"]:
                value = row[name]
                if isinstance(value, bool):
                    raise ValueError(f"control field {name} must be a finite number")
                try:
                    numeric = float(value)
                except (TypeError, ValueError) as error:
                    raise ValueError(f"control field {name} must be a finite number") from error
                if not math.isfinite(numeric):
                    raise ValueError(f"control field {name} must be a finite number")
                feature_values[name] = [numeric]
            features = pd.DataFrame(feature_values)
        result = self._predictor(features)
        if not isinstance(result, pd.DataFrame):
            raise TypeError("model predict must return a pandas DataFrame")
        if tuple(result.columns) != self._outputs or len(result) != len(model_input):
            raise ValueError("model output does not match the declared contract")
        return result


__all__ = ["SourceModel"]
