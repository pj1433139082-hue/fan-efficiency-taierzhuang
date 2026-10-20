#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把已构建好的 release-bundle/service 上传成平台 MLflow 的一个 Run。

上传前必须开 Hillstone VPN；另外 VPN 网卡 MTU 要压到 1400，否则 TLS 握手会静默超时
（详见 docs/10-流程层/23-CPU-IoT发布通道契约边界.md 的「零、网络前置条件」）。

两条上传通道：
- `http`（默认）：走 MLflow 服务端的制品代理 `/api/2.0/mlflow-artifacts/...`，
  不需要 boto3、不需要 MinIO 账号。本平台实测可用。
- `s3`：客户端直传 S3/MinIO，需要 boto3 + AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY +
  MLFLOW_S3_ENDPOINT_URL（公司环境才提供这些凭据）。

用法：

    python upload_bundle_mlflow.py --experiment MLModel-2555_V001 --owner heshengjie --apply

只想看计划就不加 --apply。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import quote

MODEL_ID = "fan-efficiency-taierzhuang"
MODEL_VERSION = "0.1.0"
SERVICE_ARTIFACT = "service"
DEFAULT_BUNDLE = "models/_drafts/fan-efficiency-taierzhuang/release-bundle/service"


def tree_digest(root: Path) -> str:
    rows = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root).as_posix()
        if "__pycache__" in relative:
            continue
        rows.append(f"{relative} {hashlib.sha256(path.read_bytes()).hexdigest()}")
    return hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()


def bundle_files(root: Path) -> list[tuple[str, Path]]:
    pairs = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        relative = path.relative_to(root).as_posix()
        if "__pycache__" in relative:
            continue
        pairs.append((relative, path))
    return pairs


class HttpTransport:
    """MLflow 制品代理：不需要 S3 凭据。"""

    def __init__(self, tracking_uri: str) -> None:
        import requests

        self._requests = requests
        self.base = tracking_uri.rstrip("/")
        self.session = requests.Session()
        self.session.trust_env = False  # 内网直连，别走系统代理

    def _post(self, path: str, payload: dict) -> dict:
        response = self.session.post(f"{self.base}{path}", json=payload, timeout=60)
        if response.status_code >= 400:
            raise SystemExit(f"{path} -> {response.status_code}: {response.text[:300]}")
        return response.json() if response.content else {}

    def ensure_experiment(self, name: str) -> str:
        found = self.session.get(
            f"{self.base}/api/2.0/mlflow/experiments/get-by-name",
            params={"experiment_name": name},
            timeout=60,
        )
        if found.status_code == 200:
            return str(found.json()["experiment"]["experiment_id"])
        created = self._post("/api/2.0/mlflow/experiments/create", {"name": name})
        return str(created["experiment_id"])

    def create_run(self, experiment_id: str, run_name: str, tags: dict[str, str]) -> str:
        payload = {
            "experiment_id": experiment_id,
            "run_name": run_name,
            "tags": [{"key": k, "value": v} for k, v in tags.items()],
        }
        return str(self._post("/api/2.0/mlflow/runs/create", payload)["run"]["info"]["run_id"])

    def upload_file(self, run_id: str, relative: str, path: Path) -> None:
        target = (
            f"{self.base}/api/2.0/mlflow-artifacts/artifacts/{run_id}"
            f"/artifacts/{SERVICE_ARTIFACT}/{quote(relative, safe='/')}"
        )
        response = self.session.put(
            target,
            data=path.read_bytes(),
            headers={"Content-Type": "application/octet-stream"},
            timeout=120,
        )
        if response.status_code >= 400:
            raise SystemExit(f"上传 {relative} 失败 {response.status_code}: {response.text[:200]}")

    def list_artifacts(self, run_id: str, relative: str = "") -> list[str]:
        query = quote(relative, safe="")
        response = self.session.get(
            f"{self.base}/api/2.0/mlflow-artifacts/artifacts/{run_id}/artifacts/{query}",
            timeout=60,
        )
        if response.status_code >= 400:
            return []
        return [item.get("path", "") for item in response.json().get("files", [])]

    def finish(self, run_id: str, status: str) -> None:
        self._post("/api/2.0/mlflow/runs/update", {"run_id": run_id, "status": status})

    def delete_run(self, run_id: str) -> None:
        try:
            self._post("/api/2.0/mlflow/runs/delete", {"run_id": run_id})
        except SystemExit:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="上传 model-lab 构建好的 MLflow service 包")
    parser.add_argument("--bundle", default=DEFAULT_BUNDLE)
    parser.add_argument("--experiment", required=True, help="平台绑定页上的精确实验名，例如 MLModel-2555_V001")
    parser.add_argument("--owner", required=True, help="发布作者标识/账号标签（不是登录凭据）")
    parser.add_argument("--tracking-uri", default="")
    parser.add_argument("--transport", choices=["http", "s3"], default="http")
    parser.add_argument("--apply", action="store_true", help="真正上传；省略时只打印计划")
    parser.add_argument("--delete-run", default="", help="先删除指定 run_id（清理失败残留）")
    args = parser.parse_args(argv)

    import os

    tracking_uri = args.tracking_uri or os.environ.get("MLFLOW_TRACKING_URI", "")
    root = Path(args.bundle).resolve()
    if not (root / "MLmodel").is_file():
        print(f"bundle 不完整，缺少 MLmodel: {root}", file=sys.stderr)
        return 2
    files = bundle_files(root)
    digest = tree_digest(root)

    print(f"tracking_uri : {tracking_uri or '<未设置 MLFLOW_TRACKING_URI>'}")
    print(f"experiment   : {args.experiment}")
    print(f"owner        : {args.owner}")
    print(f"transport    : {args.transport}")
    print(f"bundle       : {root}")
    print(f"files        : {len(files)}")
    print(f"bundle_digest: {digest}")
    for relative, path in files:
        print(f"  {path.stat().st_size:>8}  {relative}")

    if not args.apply:
        print("\n预览模式：未上传。加 --apply 才真正建 Run 并上传。")
        return 0
    if not tracking_uri:
        print("缺少 MLFLOW_TRACKING_URI 或 --tracking-uri。", file=sys.stderr)
        return 2

    tags = {
        "username": args.owner,
        "python_env_desc": "Python 3.11 Linux x86_64",
        "model_type": "pyfunc",
        "deploy_tool_version": "model-lab-fixed-bundle-v1",
        "model_lab_model_id": MODEL_ID,
        "model_lab_model_version": MODEL_VERSION,
        "model_lab_bundle_sha256": digest,
    }

    if args.transport == "s3":
        import mlflow
        from mlflow.tracking import MlflowClient

        mlflow.set_tracking_uri(tracking_uri)
        client = MlflowClient(tracking_uri=tracking_uri)
        if args.delete_run:
            client.delete_run(args.delete_run)
        experiment = client.get_experiment_by_name(args.experiment)
        experiment_id = experiment.experiment_id if experiment else client.create_experiment(args.experiment)
        run_id = client.create_run(
            experiment_id, tags=tags, run_name=f"{MODEL_ID}_{MODEL_VERSION}"
        ).info.run_id
        print(f"run_id       : {run_id}")
        try:
            mlflow.log_artifacts(str(root), artifact_path=SERVICE_ARTIFACT, run_id=run_id)
        except Exception as error:  # noqa: BLE001
            client.set_terminated(run_id, "FAILED")
            print(f"上传失败，Run 已标记 FAILED: {error}", file=sys.stderr)
            return 1
        client.set_terminated(run_id, "FINISHED")
    else:
        transport = HttpTransport(tracking_uri)
        if args.delete_run:
            transport.delete_run(args.delete_run)
            print(f"已删除旧 run: {args.delete_run}")
        experiment_id = transport.ensure_experiment(args.experiment)
        print(f"experiment_id: {experiment_id}")
        run_id = transport.create_run(experiment_id, f"{MODEL_ID}_{MODEL_VERSION}", tags)
        print(f"run_id       : {run_id}")
        try:
            for relative, path in files:
                transport.upload_file(run_id, relative, path)
                print(f"  uploaded {relative}")
        except SystemExit as error:
            transport.finish(run_id, "FAILED")
            print(f"上传失败，Run 已标记 FAILED: {error}", file=sys.stderr)
            return 1
        transport.finish(run_id, "FINISHED")
        listed = transport.list_artifacts(run_id, SERVICE_ARTIFACT)
        print(f"服务端 service/ 顶层条目: {listed}")

    print("\n上传完成。回填给模型工作区：")
    print(f"  run_id={run_id}")
    print(f"  bundle_sha256={digest}")
    print(f"  页面: {tracking_uri}/#/experiments/{experiment_id}/runs/{run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
