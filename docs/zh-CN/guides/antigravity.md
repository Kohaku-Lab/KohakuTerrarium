# 复用本地 agy 登录接入 Antigravity

首版支持 **Windows 本机 CLI＋Web、单账号**。先通过官方 `agy` 登录。
KT 不实现 OAuth 登录，不保存 refresh token，也不会替用户退出 Google 账号。

```powershell
kt login google-antigravity
kt config antigravity status
kt config antigravity refresh
kt config antigravity models
kt run ./my-creature --llm google-antigravity/gemini-3.8-flash@reasoning=medium
```

- `status` 仅查看本地凭据状态，不联网。
- `refresh` 在必要时运行 `agy --output-format json models`，由 agy 自己续期。
- `models` 联网获取账号可用模型，不产生推理。
- Web 设置的「OAuth 登录」区块中，Google Antigravity 仅提供「检查状态」，遵循管理员权限配置。
- 已过期且 agy 可续期的凭据显示「待自动刷新」，下次请求时自动尝试续期；Web 不提供手动续期按钮。
- 提供者页不展示服务端模型列表；预置模型与自定义模型统一在「自定义模型」页管理。

## 内置模型与 reasoning effort

以下 token 限制对齐 2026-09-24 查询到的 agy 1.2.9 模型目录，不是账号额度，
也不保证未来服务端始终保持相同限制。

| `google-antigravity/` 下的预设 | 上下文 | 输出 | reasoning 档位 |
| --- | ---: | ---: | --- |
| `gemini-3.6-flash` | 1,048,576 | 65,536 | low、medium、high |
| `gemini-3.7-flash` | 1,048,576 | 65,536 | low、medium、high |
| `gemini-3.8-flash` | 1,048,576 | 65,536 | low、medium、high |
| `gemini-3.1-pro` | 1,048,576 | 65,535 | low、high |
| `claude-sonnet-4-6` | 250,000 | 64,000 | 固定 Thinking |
| `claude-opus-4-6-thinking` | 250,000 | 64,000 | 固定 Thinking |

CLI 和 Web 共用 KT 现有模型选择器与 reasoning variation。可以选择档位，或在模型
标识后加 `@reasoning=low`、`@reasoning=medium`、`@reasoning=high`。
KT 的 Gemini 预设默认 high，不自动改变用户的默认模型。

Flash 3.6 切换实际模型 ID 的 `-low/-medium/-high`。Flash 3.7/3.8 固定使用
发现目录中的 `-tiered` 路由；所有 Flash 均以 `thinkingLevel` 传递所选档位。
Pro low 使用 `gemini-3.1-pro-low`，high 使用 `gemini-pro-agent`，对应
`thinkingBudget=1001/10001`。显式 `gemini-3.1-pro-high` 选择器也映射到 agent 路由；
ID 与 effort 冲突会在读取凭据前报错。

agy 1.2.9 对两个 Claude 都拒绝 `--effort`，Pro 则拒绝 medium；KT 保持相同能力边界。
Claude 使用模型目录默认的 1,024 thinking budget，不套用 Anthropic 直连 API 的
自适应 effort。profile 输出上限可手动调低，但不得超过模型上限或小于等于数字 thinking budget。

旧 `gemini-3-flash` 已从 Antigravity 内置预设移除；使用它的配置请改选上表中的
当前 Flash 预设。仍可填写自定义模型 ID，未知模型不会猜测 effort 支持情况。

## 凭据与会话

仅检查 Windows 凭据项 `gemini:antigravity` 和已知 agy 后备文件。
若两处同时存在凭据则明确报错，请先在 agy 中解决来源冲突。
只接受 consumer Bearer 凭据；access token 仅驻留内存，不进入日志。
KT 通过有超时限制的 agy 子进程刷新，跨进程锁及进程内共享任务避免重复刷新。

请求固定发往 `daily-cloudcode-pa.googleapis.com`，使用此次元数据探针的 agy 1.2.9
请求头配置，不跟随重定向。项目发现与凭据代次绑定；账号切换期间会重新核验。
收到任何文本、思考、工具调用或签名后，不自动重发该推理。

会话保存原始签名片段，同时绑定实际模型 ID、项目与当前消息内容。
Claude 回传时会组装流式文本／思考块及末尾签名，去掉空占位；Gemini 保留原始片段。
同模型的工具往返、事件回放与恢复会话已通过离线完整 agent 工作流验证。
编辑消息后旧片段失效；跨模型可以保留普通文本，无法安全复用的工具签名历史
会提示新建或压缩会话。Flash 3.6 和 Pro 改变 effort 会改变实际模型 ID，带工具签名的
会话应先压缩或新建。Flash 3.7/3.8 共用 tiered 路由，以 thinkingLevel 切换档位，
保持相同的历史绑定；对应同一实际 ID 的系列名与明确档位 ID 可相互复用历史。切换到 OpenAI 时不会外发 Google 内部状态字段。

## 当前边界

暂不支持远程节点、多账号、macOS/Linux 凭据源、自定义 API 地址、媒体生成及
任意 extra_body 覆盖。支持内联图片，
其他不支持的内容或工具 schema 会明确报错。发现列表不保证每个模型支持所有模态。

2026-09-24 已完成全部 13 个模型／档位组合的在线矩阵：文本、echo 工具调用、
带签名历史的工具结果回传。首轮通过 10 个组合；修复 Pro high 路由与 Claude 流式历史
组装后，其余 3 个组合全部重测通过。采用真实 KT provider、固定短提示词，普通输出上限
2,048，Pro high 11,025，无自动重试。详见[在线矩阵与脱敏证据](../dev/research/antigravity-online-matrix-2026-09-24.md)。

离线回归覆盖无效参数、KT variation、Web 目录，以及真实 Terrarium 的工具执行、
持久化、恢复和压缩。在线矩阵未测试满上下文、最大长度输出、图片、长会话或并发负载；
上表容量仍以[模型目录元数据](../dev/research/antigravity-agy-models-2026-09-24.md)为依据。

该可选接入仍受账号服务条款与限制约束，由使用者决定是否启用。
详见[开发方案](../dev/research/google-antigravity-oauth-development-plan-2026-09-23.md)。

单次 `max_tokens` 覆盖（包括压缩摘要）不修改已保存的 profile，保持该次总输出上限。
若数字思考预算无法容纳，则仅该次请求取输出上限的一半，并遵守 Gemini Pro 128、
Claude 1,024 的最低思考预算；输出上限不足以超过最低预算时明确拒绝。
常规请求仍使用配置的 effort 预算，未知生成参数继续报错。
工具结果会回传服务端提供的函数调用 ID；上游未提供 ID 时，KT 生成的 ID 只用于
内部配对，不作为服务端 ID 外发。
