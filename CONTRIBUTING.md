# 贡献指南

## 先理解这个仓库的形态

本仓是 **导出快照**：内容由内部框架仓 `learn-model` 的 `scripts/export-model-repo.py --public` 生成。
除下面这些「本地资产」外，其它文件在下一次同步时会被覆盖或删除：

| 不会被同步覆盖 | 说明 |
|---|---|
| `README.md` | 根说明，人工维护 |
| `.gitignore` / `.gitattributes` | 仓库配置 |
| `.github/` | issue 表单、PR 模板、CI 工作流 |
| `SECURITY.md` / `CONTRIBUTING.md` / `LICENSE` | 社区健康文件 |

所以要改模型代码、部署脚本或模型文档，**不要直接在本仓改**——改动会被覆盖。请向模型负责人提交到源仓；
本仓欢迎的是：issue、文档纠错、CI/仓库配置改进，以及本地资产（上表）的补全。

## 本地跑起来

```bash
# 1) 模型行为断言（23 个 unittest，纯 pandas，无需 mlflow）
cd models/_drafts/fan-efficiency-taierzhuang
python -m pip install pandas
python -m unittest discover -s tests

# 2) 用 MLflow pyfunc 加载打包产物（需要 mlflow 2.22.2 + Python 3.11）
cd release-bundle/service
python -c "import mlflow.pyfunc as p; m=p.load_model('.'); print(m.predict({'inlet_flow':[46.1],'outlet_pressure':[46.2],'motor_power':[42.2],'inlet_temperature':[25.5],'elevation':[38.5]}))"
```

预期：第 1 步 `OK`；第 2 步输出 `eta_excel≈0.7177`、`quality=VALID`。数字对不上就发 issue。

## 提交 PR

1. 开一个分支，改完推上来；
2. 标题写清范围（`docs:` / `ci:` / `fix:` 前缀加分）；
3. 描述里用 `Refs #N` 关联 issue——`Closes #N` 会在合并时直接关掉它，只在确实要关闭时使用；
4. 确认 CI 的 `checks` 通过：模型断言、脱敏自检两步都必须是绿的。

## 提交信息

沿用仓库历史风格：`feat(scope): 说明` / `fix(scope): 说明` / `docs: 说明`，正文说清「为什么」。
