# 安全策略

## 报告渠道

请使用 **GitHub 私有漏洞报告**（仓库 Security 页 → Report a vulnerability）：

<https://github.com/pj1433139082-hue/fan-efficiency-taierzhuang/security/advisories/new>

不要用公开 issue 报安全问题——公开 issue 会立刻通知所有关注者，也会暴露复现细节。

## 本仓在保护什么

本仓是 **脱敏公开版**，不含现场数据、内网地址与平台凭据。值得报的问题主要是：

1. **脱敏回归**：仓库里出现内网域名（`*.<集团域名>`）、厂站编码 `PS<5位>-<2位>`、负责人账号、真实点位清单或现场数据；
2. **凭据泄漏**：任何 token、密码、S3/MLflow 凭据字面量；
3. **产物完整性**：`release-bundle/service` 与 `src/model.py` 对同一输入给出不同结果，或 `SHA256SUMS` 与文件不符。

CI 里已有一步「脱敏自检」会扫内网域名与厂站码；它漏掉的东西正是最值得报的。

## 不在范围内

- 集团 MLflow 平台本身（本仓不含平台，也不含访问凭据）；
- 现场采集设备的网络与账号；
- 第三方依赖（mlflow / pandas / numpy）自身的漏洞——请上报各自上游。

## 处理方式

维护者会在看到报告后先确认收到，再给出修复计划；在修复发布前，请把细节保留在私有 advisory 里。
