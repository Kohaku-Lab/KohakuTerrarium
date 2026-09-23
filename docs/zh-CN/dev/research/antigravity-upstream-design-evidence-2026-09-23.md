# Antigravity 上游实现：开发设计证据

日期：2026-09-23。目标：为 KohakuTerrarium 首批**本地 CLI＋Web、单账号**接入提供协议与测试边界。远程节点、多账号、账户轮换不在首批范围。

上游所有引用固定为 oh-my-pi `ef6d8b2d0c2af26417c633619d8dcce1cc61a226`。已逐文件读取该提交源码并核对行号；搜索仅用于找路径，不将默认分支搜索摘要当作固定提交证据。未运行上游测试、未读取用户凭据、未登录或调用 Google 服务。本文不重复 ToS 调研。

## 调查结论

- 现状是：上游已有完整授权码、项目开通、凭据协调、Google 格式流式推理实现，但同时承载大量多账号与其他 provider 的行为。
- 关键约束是：同一账号也可能由 CLI/Web 两个进程及多个 creature 并发使用；签名、工具对应关系和流输出提交边界必须进入首批设计。
- 我之前不知道但现在知道的是：上游手动粘贴路径允许省略 state，不能直接当作 KT Web 的安全回调契约；请求的模型兼容差异也远不止名称映射。
- 基于以上，我的判断是：移植一组有限的 Antigravity 专用模块；保留可靠凭据更新、完整 callback URL 校验和原始响应 parts；不用引入上游通用认证 DSL、多账号池或远程 broker。

## 1. 认证、持久化与刷新

| 固定提交事实 | KT 设计含义 |
| --- | --- |
| 授权码配置使用 `offline`、`consent`，回调为 `http://127.0.0.1:51121/oauth-callback`；到期映射扣除 300 秒。[规则 L7–25](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/catalog/src/compat/rules/auth/google-antigravity.kdl#L7) | 独立 credentials；OAuth 客户端值不要进日志/诊断文档。本版本规则未启用 PKCE，不应声称已验证 PKCE 可用。 |
| token 映射校验 access token 与数值到期时间；刷新响应省略 refresh token 时保留原值。[common L128–185](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/src/registry/engine/common.ts#L128) | `access_token, refresh_token, expires_at, project_id, email?, revision` 为足够的首批凭据字段。保存真实 expiry，只在 freshness 判断处扣一次安全窗口，避免照搬多层 skew。 |
| 刷新要求已有 projectId；Google project hook 在 refresh 时只保留旧 projectId，不重新开通；初次登录缺 refresh token 直接失败。[refresh L50–96](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/src/registry/engine/refresh.ts#L50)、[hook L302–330](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/src/registry/oauth/google-antigravity.ts#L302) | 登录只有在 refresh token 和有效 projectId 都齐全后才提交。刷新不得清空 project/email；缺 projectId 应提示重新登录或显式修复，不能临时编造项目。 |
| `AuthStorage` 有按 credential ID 的进程内单飞，共享刷新任务不绑定某个等待者的取消信号。[L5622–5641](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/src/auth-storage.ts#L5622) | `get_access_token()` 由服务统一协调；取消一个 creature 的等待，不应取消其他 creature 正在共享的刷新。刷新自身仍有有界超时。 |
| 跨进程先重读凭据、拿租约、再重读；租约 15 秒，每 5 秒续租，刷新超时 10 秒；完成以旧数据＋有效租约做 CAS，CAS 失败就重读。[L735–739](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/src/auth-storage.ts#L735)、[L2668–2905](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/src/auth-storage.ts#L2668) | 需要跨进程锁/租约与 revision fence；仅 `asyncio.Lock` 不足。实现可以小于上游：单账号记录＋操作锁＋原子替换或小型 SQLite。锁内再次检查到期；失败不得覆盖较新的登录。 |
| SQLite 存 JSON 文本 `data`；CAS 更新/禁用均要求行数据仍匹配，租约版还校验 owner 与 expiry；尝试 chmod 0600，Windows 失败会忽略。[存储 L413–441](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/src/auth/sqlite-credential-store.ts#L413)、[L480–546](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/src/auth/sqlite-credential-store.ts#L480) | 不应把上游 SQLite 描述为加密凭据库。KT 的 Windows 保护应按实际存储方案校验，不能声称 chmod 保证 ACL；备份、日志、Web 状态响应不返回 token。 |

建议最小认证服务语义：`begin_login()` → transaction；`submit_callback(transaction_id, full_url)`；`cancel_login(transaction_id)`；`status()`；`logout()`；`get_access_token(rejected_revision=None)`。这是一组职责建议，不要求上游同名 API。transaction 的 state、redirect URI、deadline、终态由服务器持有；Web 只能提交 URL/查询脱敏状态，不能提交任意 token/project 覆盖凭据。

刷新写入、退出和重新登录必须共享一致的互斥/版本协议：退出立即使旧 revision 失效；已经在网络中返回的旧刷新不能复活账户。强制刷新应携带请求失败时观察到的 revision，若别的进程已刷新则直接采用新值。`invalid_grant` 仅禁用自己尝试的旧 revision；网络/5xx 不应清空 refresh token。上游相同原则见 [L2810–2838](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/src/auth-storage.ts#L2810)。

## 2. Windows loopback 与 Web 手动回调

上游先监听再发布授权 URL；state 来自 16 字节随机数；总等待上限 300 秒，正常/失败/取消都在 `finally` 停止监听。[callback L215–331](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/src/registry/oauth/callback-server.ts#L215)

- Antigravity 规则明确使用 `127.0.0.1`。因此首批直接 IPv4 loopback 即可，避免不必要地引入上游为 `localhost` 做的双栈监听和 Bun 专属错误处理。[L449–526](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/src/registry/oauth/callback-server.ts#L449)
- 上游默认端口忙时可退随机端口；显式 redirect URI 或禁用 fallback 时失败。[L363–397](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/src/registry/oauth/callback-server.ts#L363) **KT 首批建议固定 51121，端口占用时进入明确的手动 URL 模式或返回可操作错误**；Google 对本客户端任意 loopback 端口是否接受尚未实测，不能把上游通用默认当作服务保证。手动模式须在开始授权前确定 redirect URI。
- 自动 callback 验证 path、code、state；带匹配 state 的 `error=access_denied` 结束登录；错 state 的伪造 error 不终止当前登录。[L541–590](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/src/registry/oauth/callback-server.ts#L541)
- **上游手动路径的实质差异**：支持 URL、query、裸 code 和 `code#state`；只在 state 存在时比对，URL 解析没有核验 scheme/host/port/path。[L630–684](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/src/registry/oauth/callback-server.ts#L630) KT Web 应仅接收完整 callback URL，校验本次实际 redirect 的 scheme/host/port/path 与必填 state，再处理 code 或授权拒绝。此为设计强化，不是上游既有保证。
- 完整 callback URL 不是普通状态文案：含一次性 code，不能写入日志、会话内容或 URL analytics。成功自动回调与手动提交竞速时，只允许一次 token exchange；Web 取消必须真正取消网络/监听，并禁止晚到的响应提交凭据。
- 首批“Web 本地”应明确浏览器和运行 KT 服务在同一台机器。SSH/容器/远程浏览器的 loopback 不是服务主机；完整 URL 手动提交是一个输入通道，不自动意味着已实现远程凭据归属和远程登录支持。

## 3. project 发现与开通不可简化成固定字符串

`loadCodeAssist` 向 daily endpoint POST `{"metadata":{"ideType":"ANTIGRAVITY"}}`；若无 paidTier 但有 project，会再带 project 查询；校验 free-tier ineligibility；无 currentTier 时 `onboardUser`，1 秒轮询 `GET /v1internal/{operation.name}`，总计最多 30 秒；最后重新 load 并要求非空 `cloudaicompanionProject`。[L158–183](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/src/registry/oauth/google-antigravity.ts#L158)、[L202–289](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/src/registry/oauth/google-antigravity.ts#L202)

保留完整状态机和取消传播；资格拒绝、需要用户验证、onboard operation.error、缺 project 分别显示，不能统称“登录失败”或不停重试。上游 mocked-fetch 测试覆盖已有账户、二次 project 查询、开通、GET 轮询、资格拒绝及非 200。[测试 L36–222](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/test/google-antigravity-oauth.test.ts#L36)

## 4. 推理最小契约与模型差异

请求发送 `POST /v1internal:streamGenerateContent?alt=sse`；header 使用 Bearer、JSON、SSE Accept 与 Antigravity User-Agent。外层是 `project, model, request, userAgent: "antigravity", requestType: "agent", requestId`；内层包含 `contents`，按需 systemInstruction、tools/toolConfig、generationConfig、sessionId/labels。[header L574–590](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/src/providers/google-gemini-cli.ts#L574)、[envelope L1254–1391](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/src/providers/google-gemini-cli.ts#L1254)

| 项目 | 首批需要保留的行为 / 可裁剪部分 |
| --- | --- |
| 内容映射 | user→`user`，assistant→`model`，systemInstruction 为 parts 对象且 Antigravity 带 `role: user`。禁止将 OpenAI messages 原样发往该端点。[转换 L180–278](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/src/providers/google-shared.ts#L180) |
| thought 签名 | thought 由 `thought:true` 判定，不能由签名存在推断；签名可在 text、thinking、functionCall 或后置空 text 出现。保存与对应 part 的关联，后续缺签名 delta 不覆盖已有值。[L108–153](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/src/providers/google-shared.ts#L108)、[流解析 L785–855](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/src/providers/google-gemini-cli.ts#L785) |
| 工具往返 | 所有相邻 functionResponse 必须合成一个 user turn。返回名对应原调用名；成功 `{output: ...}`、失败 `{error: ...}`。并行工具返回顺序可不同，必须由 tool_call_id 匹配，不能用列表位置猜。[L279–331](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/src/providers/google-shared.ts#L279) |
| 历史修复 | 上游还有通用 transform：去重/重写调用 ID、为缺失结果补失败占位、消费对应真实结果、避免孤立/重复结果。[L1018–1116](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/src/providers/transform-messages.ts#L1018) KT 需先检查自己的 controller/session 已保证什么，再决定 encoder 校验或修复；不必复制整个 1266 行跨 provider 转换器。 |
| Gemini 3 签名兼容 | CCA 首个 functionCall 无签名时上游填 `skip_thought_signature_validator`；已签名首调用之后的无签名并行调用保持无签名，不可逐个填。[L246–269](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/src/providers/google-shared.ts#L246)、[规则 L30–36](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/catalog/src/compat/rules/providers/google-antigravity.kdl#L30) |
| Claude 路由 | 需要 function part ID，丢弃无签名 thinking，legacy `parameters` schema 与 Claude thinking beta header，tool mode 强制 VALIDATED。[规则 L330–339](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/catalog/src/compat/rules/classes/anthropic.kdl#L330) 因此同是 Antigravity 不能只给 Gemini/Claude 共用一组布尔默认值。 |
| schema | Gemini 用 `parametersJsonSchema`，Claude CCA 用规范化后的 `parameters`；CCA 消除 nullable/type 数组及 residual combiners。[转换 L341–371](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/src/providers/google-shared.ts#L341)、[normalize L1276–1297](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/src/utils/schema/normalize.ts#L1276) KT 可仅实现已支持工具 schema 子集，但必须明确拒绝未支持形状，不能静默改窄参数含义。 |
| thinking | 配置按实际 wire model 分预算/level；Antigravity Gemini 3.1 Pro 使用 budget，Flash >=3.6 为 google-level，不能写成“所有 Gemini 3 都用 thinkingLevel”。[规则 L37–81](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/catalog/src/compat/rules/providers/google-antigravity.kdl#L37) |
| 模型与输出上限 | 上游特定 wire profile 会覆盖调用者 maxOutputTokens，Claude cap 64000；因此不要承诺原参数被尊重。首批应做明确 clamp/报错并文档化，或按实测可支持值实现。[wire L104–134](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/catalog/src/wire/gemini-headers.ts#L104) |
| 会话 envelope | 上游每会话维护 agent/trajectory/session/step，requestId 为 `agent/<agent>/<timestamp>/<trajectory>/<step>`，完整成功后才更新 lastExecutionId。[L1218–1251](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/src/providers/google-gemini-cli.ts#L1218)、[L1071–1083](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/src/providers/google-gemini-cli.ts#L1071) 可先保留相同形状；哪些字段由服务强制仍需探针，不把上游 telemetry 模拟自动当作必需协议。 |

签名保存建议：在 assistant `extra_fields` 下放版本化 namespace，包含 issuer provider、wire model、完成标记、按顺序的 Google parts、内部调用 ID 到原始 functionCall 的映射。还应保存收到响应时规范化 `content/tool_calls` 的摘要或可比较快照；发送时先与当前 canonical 字段比对，不一致则丢弃旧 parts 的签名并由当前字段重建，不能因 opaque parts 存在而忽略用户编辑/清理后的内容。纯文本、工具参数或 compact 改写后必须使不再对应的签名失效。跨模型/provider 不回放旧签名。上游明确只保留同 provider/model 的有效 base64 签名 [L138–153](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/src/providers/google-shared.ts#L138)；KT 是否需要绑定账号/项目尚无本次实测证据。

## 5. 流、模型发现和错误恢复

- SSE JSON 不是裸 Gemini response，而是 `response.candidates[0].content.parts`，另有外层 `error`、promptFeedback 和 responseId。functionCall 可无 ID或重 ID，上游生成内部唯一 ID；完整流必须出现 finishReason，不能把 TCP EOF 自动视为成功。[L766–886](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/src/providers/google-gemini-cli.ts#L766)、[L1064–1068](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/src/providers/google-gemini-cli.ts#L1064)
- usage 语义：上游 fresh input = prompt - cached，output = candidates + thoughts，total 使用服务字段。KT 应按自身 Usage 定义映射，防把 reasoning 或缓存重复相加。[L875–886](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/src/providers/google-gemini-cli.ts#L875)
- 首个响应/事件有超时；上游 daily→sandbox 仅在尚未发布内容且错误可重试时切换，输出后错误直接抛出；thinking 已输出也算提交，thought-only STOP 不重放相同请求。[L601–609](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/src/providers/google-gemini-cli.ts#L601)、[L1029–1097](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/ai/src/providers/google-gemini-cli.ts#L1029) KT 必须明确重试由 provider 还是调用层负责，防嵌套放大；工具已交 controller 后绝不透明重试整轮。
- 首批可以只用 daily endpoint，保留有界错误恢复；sandbox 自动切换、lastGoodEndpoint 缓存、空流重试和 Flash planning 文本清理属于可单独验收的兼容增强。错误分类、取消、无 finishReason 拒绝成功、输出提交后禁止重放不可裁剪。
- `fetchAvailableModels` 使用 Bearer、Antigravity UA，POST 空 `{}`；参数 project 已 deprecated 且被忽略。默认尝试 daily/sandbox，返回失败 `null` 与成功无模型 `[]` 两种结果，过滤 internal/denylist 并折叠 effort variants。[发现 L135–160](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/catalog/src/discovery/antigravity.ts#L135)、[L170–210](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/catalog/src/discovery/antigravity.ts#L170)、[L234–280](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/catalog/src/discovery/antigravity.ts#L234) KT 首批可展示 raw wire model ID＋能力，避免先移植复杂的逻辑 ID/effort 折叠；失败保留最后一次列表并标注过期，不能显示“无权限模型”为确定结论。
- UA 版本从更新 manifest 获取，有固定 fallback、5 秒超时和进程内单飞；上游注释声称版本影响新模型开放，并将 OS/arch 固定为捕获样本而非宿主。[L20–101](https://github.com/can1357/oh-my-pi/blob/ef6d8b2d0c2af26417c633619d8dcce1cc61a226/packages/catalog/src/wire/gemini-headers.ts#L20) 固定值＋可配置兼容 profile 足够构成首批；版本自动追踪是可选维护机制，上游注释的 live 验证不是本项目实测。

## 6. 应进入开发计划的负向测试

| 测试组 | 最小负例与验收点 |
| --- | --- |
| callback | 端口占用、错误 path/host、缺 state、错 state、缺 code、匹配 state 的拒绝、伪造拒绝、过期 URL、callback 重放、自动与粘贴竞速：错误不换 token，最多一次交换。 |
| 取消 | Web cancel/关闭登录任务、交换中取消、userinfo/onboard 中取消、timeout 后晚回包：监听/任务释放，旧凭据保留，不提交晚到成功。 |
| 凭据 | 缺 access/refresh/project、非数字或无 expires_in、刷新不轮换 refresh token、短 TTL、临时失败、invalid_grant、status/log 脱敏。 |
| 并发 | 多 creature 一次刷新；CLI/Web 两进程一次刷新；一个等待者取消；刷新时 logout/新 login；租约丢失/进程崩溃/写盘失败：无旧凭据覆盖、无账户复活。 |
| project | 无 currentTier、缺 paidTier、明确 ineligible、operation.done+error、operation 无 name/response、轮询超时、最后仍缺 project。 |
| 工具/schema | 同名并行工具乱序返回、缺/重复/孤立结果、无 ID/重 ID functionCall；Gemini/Claude schema 形状不同；nullable/enum/嵌套数组/anyOf；不能静默把未支持 schema 当通过。 |
| 签名 | text 上签名不当 thinking；后置空 text 签名；签名只在首 delta；有签名首调用＋无签名第二调用；会话保存/恢复；compact/改写/换模型后不发送失配签名。 |
| SSE | 分包 JSON、CRLF/空行、多 data 行、仅 usage、外层 error、SAFETY/MALFORMED_FUNCTION_CALL、EOF 缺 finishReason、空 STOP、thought-only STOP、发布工具后断连：不得成功或透明重放。 |
| 恢复/模型 | 401 最多刷新一次且重试基于新 revision；403 不做普通无限重试；429 尊重服务延迟与总预算；5xx/网络失败有界；discovery 失败与空列表区分；参数不能越过已知 cap。 |

## 7. 本次仍未知，必须用账户探针回答

1. 当前客户端授权范围是否仍可授权、redirect 随机端口/PKCE/独立客户端能否工作；Windows 浏览器实走、固定端口冲突手动回调的完整流程。
2. 账户实际返回的 tier/project/模型、验证拒绝格式、不同模型的 function part IDs/签名及 thinking 值；raw model ID 是否足以避开逻辑模型映射。
3. 最小 envelope 哪些字段强制、UA 版本要求、maxOutputTokens 是固定必须值还是仅上限、跨账号/项目签名回放限制。
4. 流中 signature-only parts 的真实边界，工具乱序、会话恢复与 compact 后的服务验证；401 刷新、限流和中途断流在真实环境的表现。

离线测试可以证明 KT 状态机和编码遵循已知契约，不能证明账户可调用。真实验收至少是登录→刷新→文本→单/并行工具→多轮签名→保存恢复→取消，分别覆盖首批宣称支持的 Gemini 和 Claude 模型。

## 8. 新方向：复用本地 agy 凭据（独立于上述 oh-my-pi 固定基线）

补查日期：2026-09-23。问题是能否由官方 CLI 完成登录和刷新，KT 仅读取当前 access token，再自行使用前述 CCA provider。以下同时记录官方说明、公开实现和未实测推断；没有读取本机 keyring/token 文件，没有运行生成或认证网络探针。

**调查结论：**已有公开源码证明“读 agy keyring → Bearer → daily CCA”是一条具体可参考的实现路径，因此可以把“agy 凭据源”作为首批优先方案，推迟 KT 自己的 OAuth 登录界面。但当前官方没有在所查文档中给出稳定 token export/refresh API；免做登录不等于免做来源识别、到期刷新、错误处理和兼容验证。

| 证据层级 | 已确认事实 | 不能据此推出什么 |
| --- | --- | --- |
| 官方文档 | `agy` 尝试 OS keyring 静默登录，Windows 使用 Credential Manager；无保存会话才进入浏览器；`/logout` 清除 keyring profile。[Installation & Auth](https://antigravity.google/docs/cli/install/#authentication-workflows) | 文档未给出 service/account/schema，也没有承诺别的程序可通过稳定 API 导出 token。 |
| 官方文档 | headless 使用缓存凭据，但执行的是完整 agent turn，可包含工具执行和会话状态。[Headless mode](https://antigravity.google/docs/cli/headless/#run-a-single-prompt) | `agy -p` 不是单纯刷新命令或裸 LLM HTTP API，不能因免登录就当 KT provider 的等价替代。 |
| 官方发布记录 | 1.1.23 主动提前 5 分钟刷新 browser/WIF OAuth；1.1.3 在无 D-Bus 或 keyring timeout 时旁路 keyring；后续 logout 也支持直接清文件存储。[CHANGELOG L209–218](https://github.com/google-antigravity/antigravity-cli/blob/818089f390e240921bb597b7a22ce9c96cdf7fe6/CHANGELOG.md#L209)、[L523–537](https://github.com/google-antigravity/antigravity-cli/blob/818089f390e240921bb597b7a22ce9c96cdf7fe6/CHANGELOG.md#L523)、[L179](https://github.com/google-antigravity/antigravity-cli/blob/818089f390e240921bb597b7a22ce9c96cdf7fe6/CHANGELOG.md#L179) | “只存在 keyring、永远没有文件”不准确；也不能证明运行某个非生成子命令一定触发刷新并持久化到同一位置。 |
| 官方仓库的用户一手报告 | #51 在 macOS 实测 service=`gemini`、account=`antigravity`；#479 给出的脱敏容器文件结构是 `auth_method: consumer` 和 `token{access_token,refresh_token,token_type,expiry}`。[#51](https://github.com/google-antigravity/antigravity-cli/issues/51)、[#479](https://github.com/google-antigravity/antigravity-cli/issues/479) | 这是报告者在具体版本的观察，不是官方跨平台兼容承诺；#479 当时还报告新进程不接受该文件，不能照搬为当前结论。 |
| GoogleCloudPlatform/scion 的集成源码 | 固定 SHA `1d648e64894e81c94f2960106c1b2b7900721ec7`：capture 先尝试 `~/.gemini/antigravity-cli/antigravity-oauth-token`，无文件再经 `secret-tool lookup service gemini username antigravity`；接受顶层或内层 refresh_token；provision 注入同一 keyring。[capture L58–114](https://github.com/GoogleCloudPlatform/scion/blob/1d648e64894e81c94f2960106c1b2b7900721ec7/harnesses/antigravity/capture_auth.py#L58)、[provision L177–210](https://github.com/GoogleCloudPlatform/scion/blob/1d648e64894e81c94f2960106c1b2b7900721ec7/harnesses/antigravity/provision.py#L177)、[L422–442](https://github.com/GoogleCloudPlatform/scion/blob/1d648e64894e81c94f2960106c1b2b7900721ec7/harnesses/antigravity/provision.py#L422) | 这是 Google 组织下另一个项目的容器集成代码，不是 agy 公共 token API。README 仍写 `AGY_KEYRING_TOKEN`，当前 provision 实际使用 `AGY_TOKEN`；不能照 README 写变量名。[README L21–30](https://github.com/GoogleCloudPlatform/scion/blob/1d648e64894e81c94f2960106c1b2b7900721ec7/harnesses/antigravity/README.md#L21) |
| 第三方凭据管理器 AGM 源码 | `1d3ce8497e36ffa60c3b4e369168315a7ae4d469` 使用相同 service/account，Windows target=`gemini:antigravity`，CredRead generic 类型并解 UTF-8；macOS 支持 `go-keyring-base64:` 包装；payload 使用上述 consumer JSON。[常量/结构 L15–131](https://github.com/shyim/agm/blob/1d3ce8497e36ffa60c3b4e369168315a7ae4d469/internal/credstore/credstore.go#L15)、[Windows L295–334](https://github.com/shyim/agm/blob/1d3ce8497e36ffa60c3b4e369168315a7ae4d469/internal/credstore/credstore.go#L295) | AGM 是独立实现，不是 agy 源码。其忽略 auth_method 校验、无法解析 expiry 时假定一小时的处理，不应复制进 KT。 |
| 第三方 CCA 代理 Nitpicker 源码 | `b9509442246cdbcf8d5722d483ffe37b44d69cd1` 只读 keyring，取 `token.access_token/expiry`，不消费 refresh token；用 Bearer 调 daily loadCodeAssist 与 streamGenerateContent。[token L5–69](https://github.com/arsenyinfo/nitpicker/blob/b9509442246cdbcf8d5722d483ffe37b44d69cd1/src/gemini_proxy/token.rs#L5)、[project L101–169](https://github.com/arsenyinfo/nitpicker/blob/b9509442246cdbcf8d5722d483ffe37b44d69cd1/src/gemini_proxy/proxy.rs#L101)、[推理 L394–410](https://github.com/arsenyinfo/nitpicker/blob/b9509442246cdbcf8d5722d483ffe37b44d69cd1/src/gemini_proxy/proxy.rs#L394) | 这证明有可移植的路径，不代表本机 agy 1.2.8 或所有账号已实测通过。它使用 antigravity-cli UA，而前文 oh-my-pi 使用 hub UA，不能仅替换 token 后声称协议完全相同。 |

**token 类型判断：**官方文档/发布说明确认 Google OAuth 登录及 refresh 行为，公开 consumer payload 是 OAuth access/refresh token 结构，Nitpicker 把其中 access token 直接用于 CCA。这些证据支持先研究 consumer Google OAuth 复用；没有证据支持把任意 `firstparty`、企业 WIF、ADC、API key 或 IDE 内部 session token 当成同类 bearer。KT 读取器应显式识别支持的 auth_method/schema，未知类型失败并解释来源。AGM 自行登录的公开 client ID 与上述固定 oh-my-pi 规则解码后相同（本次只比较相等性，不记录常量值）；AGM 多请求 `aicode` scope，仍不能据此断言当前官方 agy 的 client/scopes 相同。[AGM OAuth L20–37](https://github.com/shyim/agm/blob/1d3ce8497e36ffa60c3b4e369168315a7ae4d469/internal/api/api.go#L20)

`AGY_TOKEN` 是 Scion provisioner/wrapper 的秘密注入约定，由 wrapper 写进 keyring/文件后再启动 CLI；本文没有证据表明 agy 本体将其作为公开环境变量读取。`AGY_KEYRING_TOKEN` 同样不应写成官方 agy 支持的变量。

**刷新责任判断：**Nitpicker 的“refresh lock”实际只串行重读 keyring，过期后报错并提示启动 `agy`，并未实现外部刷新命令。[L574–586](https://github.com/arsenyinfo/nitpicker/blob/b9509442246cdbcf8d5722d483ffe37b44d69cd1/src/gemini_proxy/proxy.rs#L574) AGM 则自行用 refresh-token grant POST Google token endpoint，这是另一种所有权方案。[L331–358](https://github.com/shyim/agm/blob/1d3ce8497e36ffa60c3b4e369168315a7ae4d469/internal/api/api.go#L331) KT 若采用 Grok 式复用，优先保持官方 CLI 拥有 refresh token；不能同时写官方 keyring 又保有不受协调的 KT 刷新副本。

**拟议最小职责（仍需探针确认）：**读取已知 consumer 凭据源→验证非空 Bearer/可解析 expiry→必要时有界调用 `agy models` 候选刷新入口→重新读取并比较 expiry/access fingerprint→按 source/account 标识缓存 project→执行 CCA provider。`agy models` 当前只可列为候选：本次公开官方资料没有证明它必定刷新、无交互且落盘；本机只查 help 的结果也不足以验收这些属性。缺登录时引导用户运行 `agy`；Web 展示凭据来源/状态，不出现 token 值；“断开 KT”不应删除 agy 的共享凭据。

省下的范围：KT 授权 URL、callback server、state transaction、token exchange、登录进度与取消 UI。仍保留：Windows 凭据读取封装、受限来源识别、刷新命令超时/取消/单飞、账号切换后 project 缓存失效、模型发现、Google wire protocol、签名/工具持久化和所有流式验收。原登录设计可降为独立 fallback，而不是继续作为首批前置要求。

必须补的最小验证：使用合成 payload 测 keyring 解码/未知 auth_method/无 expiry/损坏 JSON；本机账户授权范围内验证 `agy models` 到期前后刷新及实际写入来源；分别测试 keyring/文件同时存在且其中一份 stale；确认 CLI 不可达/无登录时不挂起或弹出意外流程；最终仍需用该来源 token 验证 loadCodeAssist→模型文本→工具与签名。读取和验证必须只输出脱敏状态，不能把 token 注入 shell 命令参数或日志。
