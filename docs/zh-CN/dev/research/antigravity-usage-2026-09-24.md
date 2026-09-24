# Antigravity 账户额度接入验证

2026-09-24，经用户同意，复用本机 agy access token，向固定 Google 目的地进行只读查询；未调用模型推理。凭据和项目 ID 不进入日志或报告。

## 现场结果

`POST https://daily-cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary` 返回 HTTP 200。请求体包含 `project`，响应有 `groups[].buckets[]`：

- Gemini Models：`gemini-5h`、`gemini-weekly`。
- Claude and GPT models：`3p-5h`、`3p-weekly`，属于共享额度。
- 每个 bucket 都包含显式 `window`、`remainingFraction`、`resetTime`。

完成服务实现后再次只读验证，得到 `status: ok`、两组四个窗口。百分比按 `(1 - remainingFraction) * 100` 计算，重置时间来自响应；不保存账户实际用量快照。

## 实现边界

账户页沿用现有加载、过期快照和进度条组件。进入账户页或点击重新整理时查询；没有后台轮询、模型请求、登录流程或手动凭据刷新按钮。过期凭据沿用 agy 自动续期机制。

当前仅支持本机 Windows agy 账户；远程节点返回 unsupported，前端不读取主机凭据。额度路由要求管理员权限。身份切换期间的响应丢弃，不输出 token、项目 ID、原始错误或响应。

首版只使用已实测的汇总接口，不回退旧模型目录：旧数据无法可靠推断双窗口或共享关系。未知比例保持未知，未知窗口不由重置时间推断；缺失和禁用的数据不伪造为满额。

参考：[oh-my-pi 额度实现](https://github.com/can1357/oh-my-pi/blob/b6b3430b38f620394f209b998bbbce04a32580b8/packages/ai/src/usage/google-antigravity.ts)、[官方额度展示](https://antigravity.google/docs/models)。
