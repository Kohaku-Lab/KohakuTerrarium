# agy 1.2.9 模型、限制与 effort 对齐

日期：2026-09-24。工作分支：`codex/antigravity-agy`。

## 一手观察

- 本机官方 CLI `--version` 返回 `1.2.9`。
- `agy --output-format json models` 返回 command.data.models，列出 Flash 3.6/3.7/3.8
  各 low/medium/high、Pro 3.1 low/high，以及两个 Claude Thinking。
- `models` 子命令忽略全局模型参数，不能用于判断 effort 是否有效。
  改用 `--input-format stream-json --output-format stream-json`，stdin 立即关闭，
  无提示词，无推理。结果：Sonnet high、Opus low 都报 `--effort is not supported`；
  Pro medium 报无此档位，可用 low/high。此判断来自官方程序，不是第三方 API 推测。
- 按此前发现探针授权，以内存中的本地 consumer access token 查询固定 daily endpoint
  `v1internal:fetchAvailableModels`。不追加推理，不保存凭据、账号或项目元数据。

| 发现 ID | maxTokens | maxOutputTokens | thinkingBudget | minThinkingBudget |
| --- | ---: | ---: | ---: | ---: |
| gemini-3.6-flash-low | 1048576 | 65536 | 1000 | 32 |
| gemini-3.6-flash-medium | 1048576 | 65536 | 4000 | 32 |
| gemini-3.6-flash-high | 1048576 | 65536 | -1 | 32 |
| gemini-3.6-flash-tiered | 1048576 | 65536 | -1 | 32 |
| gemini-3.7-flash-tiered | 1048576 | 65536 | -1 | 32 |
| gemini-3.8-flash-tiered | 1048576 | 65536 | -1 | 32 |
| gemini-3.1-pro-low | 1048576 | 65535 | 1001 | 128 |
| gemini-3.1-pro-high | 1048576 | 65535 | 10001 | 128 |
| claude-sonnet-4-6 | 250000 | 64000 | 1024 | 未返回 |
| claude-opus-4-6-thinking | 250000 | 64000 | 1024 | 未返回 |

Flash 3.7/3.8 的发现接口只返回 tiered，但官方 CLI 明确展开三档。
静态预设取其系列限制。2026-09-24 对照探针确认 3.8 的 `-high` 请求返回 404，
`-tiered` 携带相同 HIGH thinkingLevel 返回 200；CLI 档位名称不能直接当作该接口的 wire ID。

## 协议依据与选择

[Google 官方 CLI 文档](https://antigravity.google/docs/cli/headless/#select-a-model-effort-or-agent)
描述模型选择与 effort。路由辅助依据取 oh-my-pi 固定提交
`62bc57be1b03ef0802a33cf7f5f530e534527531`，没有复制其用户提示或服务端系统提示。

- [collapse 规则](https://github.com/can1357/oh-my-pi/blob/62bc57be1b03ef0802a33cf7f5f530e534527531/packages/catalog/src/compat/rules/taxonomy/_collapse.kdl)：
  上游为 Flash >=3.6 配置明确的 low/medium/high wire ID 和 google-level。
  此规则不能直接套用于当前 agy consumer daily endpoint：KT 对 3.7/3.8 使用
  发现目录中的 tiered ID，另以 thinkingLevel 表达档位；3.6 保留目录中的明确档位 ID。
- [Antigravity 规则](https://github.com/can1357/oh-my-pi/blob/62bc57be1b03ef0802a33cf7f5f530e534527531/packages/catalog/src/compat/rules/providers/google-antigravity.kdl)：
  Pro 3.1 使用数字 budget。初版选择目录中的 `gemini-3.1-pro-high`；后续在线矩阵证明
  它返回 400，而相同参数经 `gemini-pro-agent` 成功，因此已修正 high 路由。
  发现目录列出某 ID，并不证明当前 endpoint 接受该 ID。
- [CCA 请求实现](https://github.com/can1357/oh-my-pi/blob/62bc57be1b03ef0802a33cf7f5f530e534527531/packages/ai/src/providers/google-gemini-cli.ts)：
  `generationConfig.thinkingConfig` 使用 includeThoughts 加 level 或 budget；
  Claude 思考请求带 interleaved-thinking beta header。
- 虽然第三方可以把 Claude budget 包装成 effort，官方 agy 不提供这些档位。
  此次以对齐 agy 为目标，只开放其明确支持的档位，Claude 固定使用发现预算。
- 默认 high 是 KT 的预设策略，不声称所有 agy 用户的默认模型/档位相同。
- 原 Gemini 3 Flash 预设保留兼容；对未知自定义 ID 不推断能力。

## 实现与验证边界

`antigravity_presets.py` 集中描述六系列限制、档位与请求参数。
KT 原有 variation 解析、CLI/Web 模型选择器直接消费 reasoning group。
构造 provider 时校验不支持的档位、明确 ID 与 effort 冲突、输出上限与 budget。
inline 路径采用模型默认限制。模型克隆保留可用的 effort 和较小输出限制，
并采用目标模型上下文限制。

签名状态绑定实际 wire ID，避免仅因为逻辑系列名相同便混用签名。
同一 wire ID 的别名可复用；只有实际 wire ID 改变时，带工具签名的历史要求压缩或新会话。
Flash 3.7/3.8 的三档共用 tiered ID，切换 effort 不改变签名绑定。
这是 KT 的保守实现边界，不是宣称 Google 一定拒绝所有跨档位签名。

本节所述初始验证仅包括目录发现与官方 CLI 参数校验；当时没有追加推理调用。
离线测试覆盖最终 HTTP 请求、负例、profile/bootstrap、Web 目录，以及真实 Terrarium
工具执行、会话落盘和恢复。当时尚未逐一验证的六系列／全部 effort，现已完成
[在线三阶段矩阵](antigravity-online-matrix-2026-09-24.md)，13 个组合均通过。

## 验证结果

- 先添加行为测试并确认缺少预设、旧限制、effort 拒绝导致失败，再实现并复验。
- 受影响后端回归：3,368 passed，包含 `tests/unit/llm`、`tests/unit/bootstrap`、
  `test_agent_model.py`、文件大小/依赖门禁、LLM/Studio 集成、API Studio E2E。
  运行时仅将测试进程 PATH 限为 Windows 系统目录，隔离本机 Grok CLI 版本对其既有
  fixture 的干扰；Python 使用原工作区虚拟环境绝对路径，源码由测试配置指向本 worktree。
- 前端 ModelSwitcher、PresetEditor、AntigravityCard、modelInventory、useModelInventory：
  5 个文件、32 项测试通过；`npm run format:check`、`npm run build` 通过。
- 全仓库 `ruff check src/ tests/`、`black --check --target-version py310 src/ tests/`
  通过；Black 检查 1,668 个文件；`git diff --check` 通过。
- 已知非失败告警：Pydantic `__fields_set__` 弃用、Node localStorage 实验提示，
  前端既有组件重名、动态导入混用和大 chunk 提示。

## 404 实际会话回归（2026-09-24）

用户会话在选择 `google-antigravity/gemini-3.8-flash` 后首轮报 `http_404`。
两次明确授权的对照推理使用相同凭据、项目、固定提示词 `Reply exactly OK.`、
输出上限 128、`includeThoughts: true` 和 `thinkingLevel: HIGH`，仅替换模型 ID：

| wire ID | HTTP | 输出字符数 | finish |
| --- | ---: | ---: | --- |
| gemini-3.8-flash-high | 404 | 0 | 无 |
| gemini-3.8-flash-tiered | 200 | 2 | STOP |

项目发现成功；目录再次确认 3.7/3.8 只列出 tiered，3.6 列出各档位和 tiered。
根因是将 CLI 逻辑档位与这个 consumer endpoint 的实际路由混为一谈；此前 mock
重复实现假设，没有模拟不存在的 SKU。现已补充不存在模型返回 404 的回归，覆盖
默认档位与 low/medium/high，以及签名历史、profile 创建和真实 agent 会话恢复。
此轮 404 修复时，3.7 仅有目录依据，3.8 仅验证 HIGH 文本请求；后续完整矩阵
已补齐 3.6/3.7/3.8 全部档位的文本及工具往返。
未上传用户会话或仓库内容，未保存 token、项目 ID 或原始响应。

本次修复验证：先确认 19 项路由/签名用例在旧实现失败，修复后该组 70 项通过；
LLM 单元、bootstrap LLM、LLM 集成、文件大小与依赖门禁合计 3,184 项通过。
全仓库 Ruff、Black（1,668 文件）及差异空白检查通过。既有 45 条 Pydantic 弃用告警。
