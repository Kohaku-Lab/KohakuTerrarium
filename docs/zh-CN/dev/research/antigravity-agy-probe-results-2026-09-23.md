# agy 凭据复用：首批可行性探针结果

日期：2026-09-23。阶段：已完成隔离工作区与首批真实探针；尚未接入正式 provider、CLI 设置或 Web 设置。

## 结论

**GO：本机 Windows＋agy 1.2.8 的 consumer OAuth 凭据可以复用，且可以由官方 agy 自行刷新。** 已用真实账户验证项目/模型发现、Gemini 工具调用与签名回传、Claude 文本请求。首版可以沿“KT 借用 access token，agy 管理登录和 refresh token”的路线实施，不需要先开发 KT 自有 OAuth 登录。

这个结论只覆盖本次具体环境和请求，不能扩大为全部平台、全部 agy 版本或全部账号/模型均受支持。

## 工作区与边界

- worktree：`C:/Users/slapa/.codex/worktrees/antigravity-agy-probe/KohakuTerrarium`
- 分支：`codex/antigravity-agy`
- 起点：`9f439ef430d3b0e872aff41748deb931fd828b17`
- Windows 原生 agy：`1.2.8`，Scoop 安装。
- 凭据只从已知的 `gemini:antigravity` Windows generic credential 读取；另检查了已知回退文件是否存在，没有枚举整个凭据库。
- 网络凭据只用于 Google 官方 `https://daily-cloudcode-pa.googleapis.com`；用户明确授权了发现和最多 3 次小型推理。本轮恰好执行 3 次推理。
- 只发送固定测试提示与无副作用的 `probe_echo` 工具定义；未发送仓库文件或用户会话历史。未自行执行 refresh-token grant，未写入或删除 agy 凭据。官方 agy 的正常刷新写回是本次验证对象。
- token、projectId、email、原始 SSE/signature 未落盘到探针结果或报告。报告只保存脱敏状态和布尔验证结果。

本轮控制边界：先观察 source→官方 CLI 刷新→CCA 请求→工具结果回传；不改共享运行时/会话 schema，不启用远程节点，不做账户开通。每项请求有限超时、无自动在线重试；发生未知 schema、来源冲突或不完整流时明确失败。离线样本只检验脚本合同，真实结果由本机探针单独记录。

## 实测矩阵

| 探针 | 实际结果 | 结论 |
| --- | --- | --- |
| 读取 Windows keyring | 存在 JSON 载荷；`auth_method=consumer`；有 Bearer access token 与 expiry | 已知读取路径在本机成立。 |
| 已知回退文件 | `~/.gemini/antigravity-cli/antigravity-oauth-token` 不存在 | 本次无双来源冲突；不能推导其他环境没有文件回退。 |
| 刷新前状态 | token 已过期约 24.3 小时 | 不是拿新鲜 token 跑命令后误判刷新成功。 |
| `agy --output-format json models` | exit 0，约 4.26 秒；access token 改变，剩余有效期变为约 3596 秒；refresh token 值未改变 | 官方 CLI 可非交互刷新并写回 keyring。KT 未消费 refresh token。 |
| `loadCodeAssist` | HTTP 成功，取得有效 managed project | 不需要 KT 重做登录/开通才能使用当前账号。project 仅留内存。 |
| `fetchAvailableModels` | HTTP 成功，返回 27 个模型条目，其中包含 Gemini 与 Claude | 动态发现可用；条目出现不代表其所有能力都通过验收。 |
| 推理 1：`gemini-3-flash` 发出工具调用 | 精确调用 `probe_echo({value: "AGY_PROBE_OK"})`；1 个带 thoughtSignature 的 part；finish STOP | 普通函数工具与带签名响应可取得。 |
| 推理 2：同模型消费工具结果 | 原样保留上一回合 parts/signature，拼接 functionResponse 后成功；精确回答 `AGY_PROBE_OK`；finish STOP | 两轮工具和签名协议闭环通过。工具只回传固定常量。 |
| 推理 3：`claude-sonnet-4-6` 文本 | HTTP 200，约 2.98 秒，精确回答 `AGY_PROBE_OK`；finish STOP | agy token 可用于该 Claude 模型的最小文本请求。未验证 Claude 工具/thinking。 |

### 重要的实验纠正

首次使用 `agy models --output-format json` 时约 0.15 秒退出 1，token 未改变。改为全局参数在子命令之前的 `agy --output-format json models` 后成功刷新。旧形式没有保留原始 stderr，因此只记录它失败与顺序修正成功，不声称已取得精确错误文案。

这一差异应直接落实进正式 CLI 刷新调用；不能仅凭文档中的“有 models 命令”推导任意参数顺序有效。

直接 HTTP 探针曾被自动审批拦下，要求用户明确授权凭据与目的地。收到用户对本机 agy access token、指定 Google host 和最多 3 次推理的明确授权后才执行；没有通过其他工具绕过拒绝。

## 可复跑的探针

[脚本](C:/Users/slapa/.codex/worktrees/antigravity-agy-probe/KohakuTerrarium/scripts/probe_antigravity_agy.py)是实验工具，不是生产 provider。其接口明确区分读取、官方 CLI 刷新、发现与生成：

```powershell
python scripts/probe_antigravity_agy.py inspect
python scripts/probe_antigravity_agy.py refresh --agy-executable <agy-executable>
python scripts/probe_antigravity_agy.py discover
python scripts/probe_antigravity_agy.py tool-roundtrip --model gemini-3-flash
python scripts/probe_antigravity_agy.py generate --model claude-sonnet-4-6
```

上述前两项不由探针直接调用模型接口；refresh 会启动官方 CLI 并允许其更新自己的状态。discover 会发送真实 Bearer 请求；tool-roundtrip 发起两次推理；generate 发起一次。网络目标是代码中的固定白名单，HTTP redirects 关闭，无任意 base_url 参数。

模型请求采用与凭据来源配套的 Antigravity CLI User-Agent、Windows metadata 和 Google API client header。客户端版本默认固定在此次已验证的 `1.2.8`，可显式指定；正式实现应读取并校验实际 CLI 版本。本次生成采用 `userPromptId` 与 Google contents/parts 包装，不应无条件移植 oh-my-pi hub 请求身份。

本轮最初的只读/刷新脚本在 `.probe-runtime/` 中试验，随后整理为正式可复跑脚本；后者的等价行为和补充边界经离线样本检查。Claude 实测后的脚本清理统一了两条 SSE 解析路径，只重跑离线测试，没有追加第 4 次在线推理。

## 离线验证与基线

新增 [脚本测试](C:/Users/slapa/.codex/worktrees/antigravity-agy-probe/KohakuTerrarium/tests/unit/scripts/test_probe_antigravity_agy.py)覆盖 JSON/base64 凭据、过期判断、未知 auth_method、坏 expiry、header 注入、秘密脱敏、固定目的地、发现输出、固定推理提示、截断 SSE、精确签名回传与工具结果配对。真实外部边界用 httpx.MockTransport 替代，不触碰实际账户。

- 新探针测试：14 passed。
- 既有 Grok auth、Message、backend 基线：123 passed。
- 合并执行：137 passed。
- 新脚本与测试：ruff、Black 检查通过。
- 尚未运行整个仓库的 unit/integration/e2e 或前端 build；没有生产代码/前端修改，不据此声称正式功能验收通过。

基线最初 1 failed / 122 passed：Grok 的一个测试读取本机 CLI 版本 `1.0.40`，而测试样本期望 `1.0.5`。源码明确优先读取实际可执行程序版本；隔离测试进程 PATH 后 123/123 通过，未修改 Grok 实现或测试。此环境依赖作为已有问题记录，与 Antigravity 探针分开。

## 方案收敛与下一步实施边界

首版保留：只读凭据 source、官方 CLI 的有界刷新与单飞、模型发现、Google transport、工具/signature 状态保存、现有 factory/preset 配置接线、CLI 与 Web 的来源/状态/重新检测入口。

首版移除：KT OAuth callback server、state transaction、token exchange、自有 refresh-token store、Web OAuth 登录 modal、对官方 agy 账户的 logout 操作。用户在 agy 中登录；KT 的断开只停止本项目复用。

生产实现仍需解决以下实际边界，不能把本轮小样本成功直接当作完整 provider：

1. 多 creature/多个 KT 进程并发刷新、取消等待者、agy 超时/缺失/无登录、fresh token 遭遇 401、外部 logout 与账号切换。
2. 凭据源不可读和文件/keyring 同时存在的冲突；macOS/Linux 未实测，未知类型/WIF 不自动接受。
3. project/model cache 与账户作用域；access token 刷新不应被误判为换账号，但真实换账号必须使旧签名失效。
4. 增量文本/usage/finish reason、并行工具与同名多调用、消息编辑/压缩后的签名无效化、snapshot 与事件恢复、跨 provider 出站清理。
5. CLI/Web 状态鉴权与本机账号共享提示、非 host 拒绝；本轮没有测试远程节点。
6. Gemini thinking 变体与 Claude 工具/thinking 的模型兼容性；本轮仅验证表中列明的能力。

原设计详见[开发方案](C:/Users/slapa/.codex/worktrees/antigravity-agy-probe/KohakuTerrarium/docs/zh-CN/dev/research/google-antigravity-oauth-development-plan-2026-09-23.md)。此前“能否刷新”“能否借用 token 调用 CCA”的未知，现在已在上述限定环境被实测回答；其他未验证项继续保留。
