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
- Web 设置中的 `google-antigravity` 行提供同样的状态、刷新与模型发现操作，遵循管理员权限配置。

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
Pro 切换 `-low/-high`，对应 `thinkingBudget=1001/10001`。直接填写明确档位的
模型 ID 也可使用；ID 与 effort 冲突会在读取凭据前报错。

agy 1.2.9 对两个 Claude 都拒绝 `--effort`，Pro 则拒绝 medium；KT 保持相同能力边界。
Claude 使用模型目录默认的 1,024 thinking budget，不套用 Anthropic 直连 API 的
自适应 effort。profile 输出上限可手动调低，但不得超过模型上限或小于等于数字 thinking budget。

原 `gemini-3-flash` 预设继续兼容旧配置，保留 120,000/8,192 的运行限制，不新增
推理档位。也可复制预设填写发现接口返回的 ID；未知模型不会猜测 effort 支持情况。

## 凭据与会话

仅检查 Windows 凭据项 `gemini:antigravity` 和已知 agy 后备文件。
若两处同时存在凭据则明确报错，请先在 agy 中解决来源冲突。
只接受 consumer Bearer 凭据；access token 仅驻留内存，不进入日志。
KT 通过有超时限制的 agy 子进程刷新，跨进程锁及进程内共享任务避免重复刷新。

请求固定发往 `daily-cloudcode-pa.googleapis.com`，使用此次元数据探针的 agy 1.2.9
请求头配置，不跟随重定向。项目发现与凭据代次绑定；账号切换期间会重新核验。
收到任何文本、思考、工具调用或签名后，不自动重发该推理。

会话保存原始签名片段，同时绑定实际模型 ID、项目与当前消息内容。
同模型的工具往返、事件回放与恢复会话已通过离线完整 agent 工作流验证。
编辑消息后旧片段失效；跨模型可以保留普通文本，无法安全复用的工具签名历史
会提示新建或压缩会话。Flash 3.6 和 Pro 改变 effort 会改变实际模型 ID，带工具签名的
会话应先压缩或新建。Flash 3.7/3.8 共用 tiered 路由，以 thinkingLevel 切换档位，
保持相同的历史绑定；对应同一实际 ID 的系列名与明确档位 ID 可相互复用历史。切换到 OpenAI 时不会外发 Google 内部状态字段。

## 当前边界

暂不支持远程节点、多账号、macOS/Linux 凭据源、自定义 API 地址、媒体生成及
任意 extra_body 覆盖。支持内联图片，
其他不支持的内容或工具 schema 会明确报错。发现列表不保证每个模型支持所有模态。

此前在线探针已验证 agy 续期、项目与模型发现、Gemini 3 Flash 带签名工具往返、
Claude Sonnet 4.6 文本。本次实现验证使用离线 HTTP 响应及真实 Terrarium 工具执行、
持久化与恢复，没有追加在线推理。Claude 工具及思考组合尚未实测。

该可选接入仍受账号服务条款与限制约束，由使用者决定是否启用。
详见[开发方案](../dev/research/google-antigravity-oauth-development-plan-2026-09-23.md)
及[探针记录](../dev/research/antigravity-agy-probe-results-2026-09-23.md)。

模型更新已用离线请求验证全部 Gemini 档位、Claude 默认值、无效参数、KT variation
解析、Web 目录和真实 agent 的工具执行与恢复；没有追加在线推理。
详见[模型元数据与路由依据](../dev/research/antigravity-agy-models-2026-09-24.md)。

后续两次明确授权的在线对照确认：Flash 3.8 的 `-high` 返回 404，改用 `-tiered`
并保持 HIGH thinkingLevel 返回 200 / STOP。Flash 3.7 按同样只提供 tiered 的发现
目录修正，尚未额外在线验证。


单次 `max_tokens` 覆盖（包括压缩摘要）不修改已保存的 profile，保持该次总输出上限。
若数字思考预算无法容纳，则仅该次请求取输出上限的一半，并遵守 Gemini Pro 128、
Claude 1,024 的最低思考预算；输出上限不足以超过最低预算时明确拒绝。
常规请求仍使用配置的 effort 预算，未知生成参数继续报错。
工具结果会回传服务端提供的函数调用 ID；上游未提供 ID 时，KT 生成的 ID 只用于
内部配对，不作为服务端 ID 外发。
