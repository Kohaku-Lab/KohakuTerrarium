# Google Antigravity OAuth 接入可行性调查

调查日期：2026-09-23。范围：源码、官方文档与本地离线核验；未实施接入，未登录 Google，未调用模型。

本项目基线：`ea6624daab5c9783cd88e4aeaa5aea7754e21604`。
oh-my-pi 基线：[ef6d8b2d0c2af26417c633619d8dcce1cc61a226](https://github.com/can1357/oh-my-pi/commit/ef6d8b2d0c2af26417c633619d8dcce1cc61a226)，提交时间为 2026-09-23 02:12:40 UTC。以下上游源码链接均固定到该提交。

**调查结论**

- 现状是：本项目没有 Antigravity provider；已有 Codex OAuth 和可注入的 LLMProvider 接口。oh-my-pi 有完整的 Antigravity 认证及 Cloud Code Assist 请求实现，可作为技术参考。
- 关键约束是：Google 当前 Antigravity 附加条款第 6 条明确将第三方软件通过 Antigravity OAuth 访问服务列为违约，并说明可能暂停或终止 Antigravity 和／或 Gemini CLI 账户。这不是仅凭社区传闻推测的风险。[官方条款](https://antigravity.google/terms)
- 我之前不知道但现在知道的是：这项接入包含 OAuth、项目发现／开通、模型发现、消息转换和 SSE 流处理；本项目现有 Gemini 配置无法只替换 token 就承担这些职责。上游当前声明式认证规则也未启用 PKCE。
- 基于以上，我的判断是：**架构上可接入，源码可参考；按项目接受第三方订阅接入、由用户主动选择并承担使用风险的定位，可以将 Antigravity 实现为可选 provider。** ToS 限制应明确记录，不作为工程可行性或项目提供能力的一票否决条件。真实账户可用性仍未验证，正式支持范围应由功能完整性、凭据处理、测试结果与维护成本决定。

结论修订：本次讨论进一步明确了产品取舍。此前以 ToS 限制直接推导“不建议正式支持”，混淆了服务使用限制与项目功能准入标准。本次保留查证事实，修正产品建议；没有因此宣称 Google 已允许该用法，也没有宣称“用户自担风险”必然使项目提供者免责。

**本项目的实际接入条件**

| 已核实的事实 | 对接入的影响 | 本地依据 |
| --- | --- | --- |
| 内置 Gemini 使用 `backend_type="openai"`，地址为 `https://generativelanguage.googleapis.com/v1beta/openai/`，凭据为 `GEMINI_API_KEY` | 这是公开 Gemini API，协议及凭据用途均不能直接替代 Antigravity 路径 | [backends.py](C:/Users/slapa/workspace/KohakuTerrarium/src/kohakuterrarium/llm/backends.py:108) |
| provider 创建按 `backend_type` 显式分支；校验允许 `openai`、`anthropic`、`codex`、`grok-subscription` | 仅添加 YAML preset 不会建立新传输协议；正式配置接入需要扩展工厂和两个校验入口 | [bootstrap/llm.py](C:/Users/slapa/workspace/KohakuTerrarium/src/kohakuterrarium/bootstrap/llm.py:138)、[backends.py](C:/Users/slapa/workspace/KohakuTerrarium/src/kohakuterrarium/llm/backends.py:230)、[llm_backends.py](C:/Users/slapa/workspace/KohakuTerrarium/src/kohakuterrarium/studio/identity/llm_backends.py:31) |
| `llm=` 接受已有 provider 对象 | 可先在独立模块验证 provider，再决定是否注册到 CLI／Studio；无需先修改 Terrarium 图运行时 | [coerce_llm_provider](C:/Users/slapa/workspace/KohakuTerrarium/src/kohakuterrarium/bootstrap/llm.py:84) |
| BaseLLMProvider 提供文本流、工具调用、用量和 assistant 扩展字段接口 | 请求和响应转换有明确承载位置 | [base.py](C:/Users/slapa/workspace/KohakuTerrarium/src/kohakuterrarium/llm/base.py:95) |
| `Message.extra_fields` 可序列化和反序列化；controller 会取 provider 的 `last_assistant_extra_fields` | 可承载原始 Google parts／thought signatures；完整 session 保存、恢复和切换模型仍要另测 | [message.py](C:/Users/slapa/workspace/KohakuTerrarium/src/kohakuterrarium/llm/message.py:182)、[controller.py](C:/Users/slapa/workspace/KohakuTerrarium/src/kohakuterrarium/core/controller.py:451) |
| Codex 已有缓存、刷新、CLI 和 Studio 封装；登录分派仅为默认 Codex endpoint 选择 OAuth | 可以参考分层方式，但不是现成的通用 OAuth 注册器 | [codex_auth.py](C:/Users/slapa/workspace/KohakuTerrarium/src/kohakuterrarium/llm/codex_auth.py:73)、[cli/auth.py](C:/Users/slapa/workspace/KohakuTerrarium/src/kohakuterrarium/cli/auth.py:8) |

架构判断：新增 provider 可以沿现有 LLM 边界实现；是否完全无需 controller／持久化调整，取决于签名、工具调用和会话恢复测试结果，当前不能预先保证。

**oh-my-pi 实际做了什么**

1. **授权码登录。** 认证规则声明 Antigravity 专用的内置 client ID／client secret，Google 授权及 token endpoint，默认回调 `http://127.0.0.1:51121/oauth-callback`，以及 `offline`、`consent` 参数。Scope 除用户邮箱／资料外，还包括 `cloud-platform`、`cclog`、`experimentsandconfigs`。这不是只获取用户身份的 Google 登录。[认证规则](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/catalog/src/compat/rules/auth/google-antigravity.kdl)

2. **PKCE 需要按当前版本判断。** 当前 Antigravity 规则没有 `pkce` 开关；编译器默认 `pkce: false`、`state: "hex"`，通用流程仅在开关为真时生成 challenge／verifier。因此不能把当前实现描述为“已使用 PKCE”。是否加入 PKCE、使用独立注册客户端能否获目标服务许可，均不在此次验证结果内。[编译器](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/catalog/scripts/compat-compiler/compile-auth.ts#L417)、[授权码流程](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/src/registry/engine/oauth-code.ts)

3. **凭据刷新与项目发现。** 登录后要求获得 refresh token，调用 `loadCodeAssist` 获取 `cloudaicompanionProject`；特定账户状态下调用 `onboardUser` 并轮询操作。刷新时使用 refresh-token grant，并保留已有 projectId。认证规则将凭据到期时间提前 5 分钟；请求层另有到期检查。登录成功不等于项目开通或模型访问成功。[项目发现与开通](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/src/registry/oauth/google-antigravity.ts)、[刷新流程](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/src/registry/engine/refresh.ts)

4. **专用模型协议。** 请求发送到 Cloud Code Assist 的 `/v1internal:streamGenerateContent?alt=sse`；外层含 project、model、requestId、requestType 等字段，内层是 Google contents／parts／generationConfig。当前实现使用 daily endpoint，并有 sandbox endpoint 的故障切换逻辑。它不走 OpenAI Chat Completions 或 Anthropic Messages 协议，即便选的是 Claude 模型。[请求构建与 SSE provider](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/src/providers/google-gemini-cli.ts#L927)

5. **持续的协议兼容维护。** 上游处理 functionCall／functionResponse、thought signatures、thinking 配置、工具 schema、用量、首响应超时和空流重试；还构造 Antigravity 客户端 User-Agent，并获取客户端版本。模型列表另经 `fetchAvailableModels` 发现。因此 README 列出的模型名称不能视为所有账户都可用的保证。[消息转换](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/src/providers/google-shared.ts)、[客户端标识](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/catalog/src/wire/gemini-headers.ts)、[模型发现](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/catalog/src/discovery/antigravity.ts)

历史核对：2026-09-22 的相关提交仍在修正 Google 传输不支持的参数，说明移植后仍需跟进兼容变化。[相关提交](https://github.com/can1357/oh-my-pi/commit/96a5a29bafed8d23dd57f137bfea7fc8f7aabb9e)

上述上游代码及其 mocked-fetch 单元测试提供了实现参考，未在本次调查中运行上游测试，更不构成对 Google 当前服务可用性的实测。[上游 OAuth 测试](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/test/google-antigravity-oauth.test.ts)

**服务条款与代码许可证必须分别判断**

Google 当前 FAQ 明确说明第三方 coding agent 使用 Antigravity 登录违反其服务条款，并建议第三方工具使用 Vertex 或 AI Studio API key。[官方 FAQ](https://antigravity.google/docs/faq#why-cant-i-use-third-party-software-eg-claude-code-openclaw-opencode-with-my-antigravity-login)

附加条款开头还说明，指定企业订阅／企业 API key 场景受各自适用条款管理。这不等于这些场景自动授权第三方复用消费级 Antigravity OAuth；此次没有核验任何企业合同或取得 Google 对本项目的授权。[条款适用范围](https://antigravity.google/terms)

已核查 oh-my-pi 的 `packages/ai` 和 `packages/catalog` 均为 MIT 许可。移植实质代码时应保留对应版权和许可声明；代码许可不授予 Google 服务访问权。[AI 包许可证](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/LICENSE)、[Catalog 包许可证](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/catalog/LICENSE)

**与现有 Grok 接入保持一致的评估标准**

当前 `grok-subscription` 已复用本地 Grok CLI／OpenCode 的 access token；Grok CLI 凭据需要刷新时，KT 运行原 CLI 的 `grok models`，随后重新读取 access token。KT 本身不消费或写入第三方 refresh token。这说明项目已经存在复用订阅凭据的功能先例；其实现方式与新增完整的 Google OAuth 流程仍有区别。[grok_auth.py](C:/Users/slapa/workspace/KohakuTerrarium/src/kohakuterrarium/llm/grok_auth.py:1)、[凭据选择与刷新](C:/Users/slapa/workspace/KohakuTerrarium/src/kohakuterrarium/llm/grok_auth.py:75)

已查阅当前 xAI 消费者条款与 AUP。AUP 包含未经授权自动访问、绕过限制等约束；仅凭源码和这些一般条款，尚不能确定每一种 Grok 凭据来源及用法的授权状态。因此不把“现有 Grok 接入已经被证实违反 ToS”写成此次调查事实。采用一致的功能准入标准，并不依赖先证明这一法律判断。[xAI 条款](https://x.ai/legal/terms-of-service)、[xAI AUP](https://x.ai/legal/acceptable-use-policy)

据此，Antigravity 的产品建议是：作为独立、由用户主动配置的 provider 提供；简要说明其第三方接入性质、账户风险及上游兼容性限制；按工程质量验收实现。用户使用风险、上游授权状态和项目提供者责任分别记录，不用一条“自担风险”声明替代全部判断，也不增加无依据的重复确认流程。

**基于结论的接入范围评估**

以下是后续决策依据，不是已实施或已验证的功能。

| 工作层 | 如果继续研究移植，需要补齐什么 |
| --- | --- |
| 认证 | 独立的 Antigravity 凭据模型、回调和 state 校验、token 缓存与刷新、projectId、开通／资格错误；并发刷新和缓存写入需要明确协调 |
| LLM provider | 新增专用 provider 与格式转换模块，适配 chat／chat_complete、工具结果、签名、thinking、SSE、用量、取消与错误分类 |
| 配置和模型 | backend 工厂、校验集合、presets／模型发现、凭据就绪判断；不覆盖现有 `gemini` provider |
| CLI／Studio | 独立登录、状态、退出接口；Codex 的 device-code 登录界面不能直接当作 Google 授权码流程使用 |
| Web／远程节点 | 管理权限、浏览器与回调所在主机、凭据归属及传输、节点身份缓存；本地回调可用不能证明远程部署可用 |
| 发布质量 | 负例测试、现有集成工作流扩展、必要的本地 e2e、文档、许可证声明和第三方接入限制说明 |

远程范围有具体依据：当前 Laboratory 的身份缓存和传输包含 Codex 专用操作，前端登录判定也明确识别 Codex／Grok；新增第三类订阅认证需要单独接线。[identity_cache.py](C:/Users/slapa/workspace/KohakuTerrarium/src/kohakuterrarium/laboratory/identity_cache.py:100)、[studio_identity.py](C:/Users/slapa/workspace/KohakuTerrarium/src/kohakuterrarium/laboratory/adapters/studio_identity.py:70)、[SettingsPage.vue](C:/Users/slapa/workspace/KohakuTerrarium/src/kohakuterrarium-frontend/src/components/settings/SettingsPage.vue:675)

分层时应继续保持现有边界：LLM 服务凭据归 LLM／Studio identity；Web 管理入口通过 `api/auth/` 控制权限。不要让底层 provider 引用 Web 用户认证模块。[CLAUDE.md 的认证边界](C:/Users/slapa/workspace/KohakuTerrarium/CLAUDE.md:644)

工作量判断：这是中等以上的跨模块 provider 接入，完整 CLI／Web／远程支持明显大于一个登录函数。缺少真实账户协议验证，不给出看似精确的工期承诺。

若继续实施，技术验证宜先限定为单账户、本地 provider 注入，验证登录→刷新→模型调用→工具往返→会话恢复，再扩展到 Studio 和远程节点。按上述产品定位，ToS 调查结果作为已知限制保留，不再设为开始工程实现前必须解决的条件。不要以一次纯文本响应作为完整接入的验收依据。

**目前可用的官方路径**

- 目标若是“让本项目使用 Gemini”：现有 `gemini` backend 已指向官方 OpenAI 兼容入口，可以沿 API-key 方式使用；模型 ID、配额及计费应以对应 API 项目为准。[Google OpenAI 兼容文档](https://ai.google.dev/gemini-api/docs/openai)
- 目标若是“通过 Google OAuth 授权调用模型”：Google 提供公开 Gemini API 的 OAuth／ADC 文档，需要自己的 Cloud 项目及应用配置。这是不同的接入目标，不能视为 Antigravity 订阅额度的替代入口。[Gemini OAuth 文档](https://ai.google.dev/gemini-api/docs/oauth)
- 当前官方也有 Antigravity Python SDK；其快速开始使用 Gemini API key，并支持 Vertex 配置。文档将其描述为带工具执行、上下文管理和会话生命周期的 agent runtime。因此，将它接入本项目更接近集成另一个 agent runtime，而非直接替换 LLM provider；这是基于文档的架构推断，未做 SDK 实测。[官方 SDK](https://antigravity.google/docs/sdk/overview/)

**本地验证结果与未验证项**

使用仓库 `.venv/Scripts/python.exe`，以仓库内独立临时配置目录设置 `KT_CONFIG_DIR`，执行了离线探针。最终退出码为 0，输出如下：

```json
{
  "antigravity_builtin": false,
  "backend_validation": "Unsupported backend_type: google-antigravity",
  "gemini_transport": "openai",
  "message_extra_fields_roundtrip": true,
  "provider_injection": true,
  "network_requests": 0
}
```

探针调用的是 `_built_in_providers()`、`validate_backend_type()`、`Message.from_dict(...).to_dict()` 和 `coerce_llm_provider()`；签名仅为合成样例，provider 注入仅验证现有对象被接受，没有发起生成请求。初次默认日志路径受沙箱限制，随后改为仓库内隔离配置；没有更改用户凭据配置。

仍未验证：真实 OAuth callback／refresh、账户资格与项目开通、当前可用模型、真实限流与封禁行为、Google thought signature 的完整多轮往返、会话恢复、切换模型、多 creature 并发刷新、远程登录。未运行完整测试套件，也未编写生产接入代码。

后续若做实现，验收应至少覆盖取消／超时／state 不匹配、缺少 refresh token／projectId、刷新响应未返回新 refresh token、明确的 401／403／429／5xx 分类、SSE 已输出后的失败、并行工具返回、签名保存与恢复、凭据脱敏，以及远程节点凭据归属。普通错误重试不应转化为绕过明确的资格或授权拒绝。

**工作审视记录**

原定目标：调查是否能参考 oh-my-pi 接入 Antigravity，并提供足以支持项目决策的依据。

- [x] 已完成：源码、官方资料及本地扩展点核验；本轮补查 Grok 实现并修订产品建议。
- [ ] 未完成：真实账户调用及生产接入；本轮仍是调查结论修订，未执行这些工作。

| 严重程度 | 具体问题 | 根本原因 | 改进 |
| --- | --- | --- | --- |
| 必须改正 | 本文原结论以 Google ToS 为主要依据直接建议不正式支持 | 把服务使用限制当成未与用户对齐的功能准入标准 | 分开记录技术事实、项目取舍与责任边界，并按已明确的产品定位修订建议 |

做得好的地方：固定上游源码版本，引用一手条款，并明确区分离线验证与真实服务可用性。

下次重点关注：同类订阅 provider 采用一致的评价标准；把实现完整性、凭据生命周期、兼容性和维护成本作为工程评估重点，保留已知使用限制。
