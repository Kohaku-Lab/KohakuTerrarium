# Antigravity 在线模型与 reasoning 矩阵

日期：2026-09-24。分支：`codex/antigravity-agy`。初始代码：`ab310c15`；复验采用本报告同次提交的修复。

## 结果

六个系列、13 个模型／档位组合均已在线通过文本、工具调用、带签名历史的工具结果回传。
其中 10 个组合在首轮通过；Pro high、Sonnet 和 Opus 在修复后重新完成全部三阶段。
这不是一次全绿的首次测试，也不是 13 组合全部在修复后重跑：修复仅改变 Pro high 路由和 Claude 历史编码。

| KT 模型 | 档位 | 实际 wire ID | 文本 | echo 调用 | 结果回传 |
| --- | --- | --- | --- | --- | --- |
| `gemini-3.6-flash` | low | `gemini-3.6-flash-low` | 通过 | 通过 | 通过 |
| `gemini-3.6-flash` | medium | `gemini-3.6-flash-medium` | 通过 | 通过 | 通过 |
| `gemini-3.6-flash` | high | `gemini-3.6-flash-high` | 通过 | 通过 | 通过 |
| `gemini-3.7-flash` | low | `gemini-3.7-flash-tiered` | 通过 | 通过 | 通过 |
| `gemini-3.7-flash` | medium | `gemini-3.7-flash-tiered` | 通过 | 通过 | 通过 |
| `gemini-3.7-flash` | high | `gemini-3.7-flash-tiered` | 通过 | 通过 | 通过 |
| `gemini-3.8-flash` | low | `gemini-3.8-flash-tiered` | 通过 | 通过 | 通过 |
| `gemini-3.8-flash` | medium | `gemini-3.8-flash-tiered` | 通过 | 通过 | 通过 |
| `gemini-3.8-flash` | high | `gemini-3.8-flash-tiered` | 通过 | 通过 | 通过 |
| `gemini-3.1-pro` | low | `gemini-3.1-pro-low` | 通过 | 通过 | 通过 |
| `gemini-3.1-pro` | high | `gemini-pro-agent` | 通过 | 通过 | 通过 |
| `claude-sonnet-4-6` | fixed | `claude-sonnet-4-6` | 通过 | 通过 | 通过 |
| `claude-opus-4-6-thinking` | fixed | `claude-opus-4-6-thinking` | 通过 | 通过 | 通过 |

`fixed` 为 agy 的固定 Thinking，budget 为 1,024，不提供额外 effort 档位。
Flash 使用对应的 LOW/MEDIUM/HIGH `thinkingLevel`；Pro low/high 使用 1,001/10,001 budget。
文本及工具结果阶段要求输出精确匹配 `AGY_MATRIX_OK`、finish 为 stop 且没有工具调用。
工具调用阶段要求恰好一个 `probe_echo`、参数 value 精确匹配、finish 为 tool_calls。
HTTP 200 本身不算通过。

## 矩阵发现并修复的问题

1. **Pro high 实际路由错误。** 目录中的 `gemini-3.1-pro-high` 返回 HTTP 400 / INVALID_ARGUMENT。
   保持提示词、预算 10,001 和输出上限 11,025 不变，仅切换到 `gemini-pro-agent` 即返回 200 / stop。
   KT 的系列 high 与显式 `gemini-3.1-pro-high` 选择器现在都映射到 `gemini-pro-agent`；low 保持原路由。
   这一结果与 [oh-my-pi 固定版本的 collapse 规则](https://github.com/can1357/oh-my-pi/blob/62bc57be1b03ef0802a33cf7f5f530e534527531/packages/catalog/src/compat/rules/taxonomy/_collapse.kdl)一致。
2. **Claude 增量片段不能直接作为完整历史回传。** 两个 Claude 文本和调用成功，结果回传却返回 400。
   Sonnet 脱敏错误为 `messages.1.content.0.text.text: Field required`。
   流中有空文本占位、分段思考文本以及最后空文本块上的签名。现在仅对 Claude 在编码时组装文本块、
   将末尾签名附到对应完整思考块，去掉空占位和未签名思考；不跨已完成签名或函数块合并。
   会话仍保存原始数据，Gemini 原始签名片段不变。缺少文本的孤立思考签名明确拒绝。
   两个 Claude 修复后均完成真实工具往返，证明不只是把第一个 400 错误遮住。

此次问题也说明此前 mock 请求通过，只能证明本地参数构造一致，不能证明服务端接受目录中的模型名或原始流片段。
新增回归先在旧实现得到 6 个失败，再验证修复。

## 调用范围与账目

| 批次 | 实际推理请求 | 说明 |
| --- | ---: | --- |
| 首轮矩阵 | 37 | 10 组合全部通过；Pro high 首步失败；两个 Claude 第三步失败 |
| Pro high 对照 | 2 | 原路由失败，agent 路由成功；用完首批 39 次授权 |
| Claude 定位 | 2 | Sonnet 调用与结果回传，取得脱敏错误和结构统计 |
| 修复后复验 | 9 | Pro high、Sonnet、Opus 各 3 阶段全部通过 |
| 合计 | 50 | 首批 39 次＋补充授权使用 11/12 次；剩余 1 次未使用 |

最早一次探针因没有沿用生产客户端的环境代理而在项目发现阶段连接失败，推理次数为 0；
修正探针后才开始上表统计。失败批次没有自动重试。

仅复用本机 agy access token，以 Bearer 发往 `https://daily-cloudcode-pa.googleapis.com`；
项目发现之外只发送固定提示词与无副作用 echo。输出上限普通组合 2,048，Pro high 11,025，
避免降低待验证的 thinking budget。没有上传仓库内容或实际用户会话。
报告不保存 token、项目 ID、原始签名、工具调用 ID 或生成内容。
[脱敏阶段结果与请求计数](antigravity-online-matrix-2026-09-24.json)保留状态、路由、参数及数值指标。

## 可复现工具与验证边界

脚本：`scripts/probe_antigravity_matrix.py`。默认仅显示计划；显式 `--run --output <已有目录中的文件>` 才联网。
可用 `--case <model>:<effort或fixed>` 和 `--stages text|tools|all` 缩小范围。
脚本固定目标主机，逐阶段限制一次推理，按矩阵大小限制总数；沿用生产 HTTP 客户端的代理配置。
后续运行仍需根据使用者的请求范围和额度授权执行，不应由 CI 自动联网。

在线验证使用真实 KT provider 的 SSE 解析和历史编码，包含全部可选 effort；
并非启动 CLI/Web 逐个点选，也没有在线测试满上下文、最大长度输出、图片、长会话或并发稳定性。
上下文／最大输出限制依然来自 agy 目录，不应把这次小型推理当作极限容量验证。
CLI/Web variation、会话持久化、恢复和压缩由离线真实 Terrarium 工作流及既有界面测试覆盖。

## 离线验证与差异审计

- 受影响回归共 3,349 项：首轮 3,348 通过，新增 Claude 压缩 fixture 把其实际 3,906 输出预算误写为 Gemini 的 4,096；修正测试预期后，LLM 集成 5 项全部通过。
- 覆盖 LLM 单元、bootstrap LLM、模型切换、压缩、LLM/core 集成、文件大小和依赖门禁，以及新旧探针单元测试。
- 全仓 Ruff 通过；Black 检查 1,670 个文件通过；差异空白检查通过。修正 fixture 后再次检查该文件的 Ruff/Black。
- 已审阅生产差异：Pro 只变更 high 路由；Claude 只在已绑定历史的编码阶段组装，保留原始持久化数据；Gemini 历史编码不变。
- 既有 Pydantic `__fields_set__` 弃用告警未在本次处理；本次无前端源码变更，未重复执行前端构建。
