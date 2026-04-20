---
title: 会话与恢复
summary: .kohakutr 会话文件如何工作、如何恢复一只 Creature，以及如何回放对话历史。
tags:
 - guides
 - session
 - persistence
---

# 会话

适合需要持久化、恢复，或保存代理执行状态的读者。

一个会话把一次执行的运行状态记录下来 — 对话、事件、子代理对话、频道历史、草稿区、job、可恢复的触发器、config metadata — 写入 `.kohakutr` 文件。你可以随时停掉一只 Creature，之后再从同一个地方继续运行。

相关概念：[记忆与压缩](../concepts/modules/memory-and-compaction.md)、[会话与环境](../concepts/modules/session-and-environment.md)。

## `.kohakutr` 文件

`.kohakutr` 是一个 SQLite 数据库 (基于 KohakuVault)，里面九张表：

| Table | 用途 |
|---|---|
| `meta` | 会话 metadata、config 快照、Terrarium拓扑 |
| `state` | 每只代理的草稿区、回合数、累积 token 用量、可恢复触发器 |
| `events` | Append-only 日志，记下每个文字 chunk、工具调用、触发器、token 用量事件 |
| `channels` | 以频道名为键的频道消息历史 |
| `subagents` | 子代理对话快照，key 是 parent + name + run |
| `jobs` | 工具与子代理 job 记录 |
| `conversation` | 每只代理最新的对话快照 (用来快速 resume) |
| `fts` | 事件上的 FTS5 索引 (供 `kt search` 使用) |
| `vectors` | 可选 embedding 字段 (由 `kt embedding` 填入) |

事件数据是 append-only，并通过 KohakuVault 的 auto-pack 做版本管理。你可以安全地复制、封存、通过电子邮件发送 `.kohakutr` 文件；它不依赖任何外部内容。

## 会话放在哪

```
~/.kohakuterrarium/sessions/<name>.kohakutr
```

`<name>` 由 Creature 或 Terrarium 的名称加上时间戳自动生成。用 `--session <path>` 覆盖，或用 `--no-session` 完全跳过。

## 哪些东西会保留下来

每回合 KohakuTerrarium 会记录：

- **对话快照** — 原始 message dict，用 msgpack 存。保留 `tool_calls`、多模态内容、metadata。
- **事件日志** — 每个 chunk、工具调用、子代理输出、触发器触发、频道消息、压缩、interrupt、错误都各一笔。这是历史的正本。
- **子代理对话** — 在子代理被销毁前存起来，事后你可以查看它做了什么。
- **草稿区与频道消息** — 每只代理与每条频道分开存。
- **Job 记录** — 长时间工具与子代理的输出。
- **可恢复触发器** — 任何设置了 `resumable: True` 的 `BaseTrigger` 子类会序列化到 `state`，resume 时再重新创建。
- **Config 快照** — 执行期完整解析过的 config，所以就算磁盘上的 config 之后改了，resume 一样能把代理重新创建。

## 恢复

```bash
kt resume --last            # 最近一个
kt resume                   # 交互式挑选 (显示最近 10 个)
kt resume my-agent_20240101 # 使用名称前缀
kt resume ~/backup/run.kohakutr
```

会自动侦测类型：agent 会话恢复为一只 Creature；terrarium 会话会恢复完整连接，并强制使用 TUI 模式。

旗标跟 `kt run` 一样：`--mode`、`--llm`、`--log-level`，另外有 `--pwd <dir>` 可以覆盖工作目录。

恢复时会执行以下操作：

1. 从 `meta` 读 config 快照。
2. 重新加载目前磁盘上的 config (你之后改的 prompt/工具会生效)。
3. 合并：config 快照提供身份信息，现行 config 给执行逻辑。
4. 重建代理、连接到同一个 `SessionStore`、恢复对话快照、回放草稿区/频道/触发器状态。
5. 控制器从头启动；先前的事件都在 context 里。

因此，小幅度的配置变动通常没有问题 (换 LLM、改 prompt 都 OK)。结构性的变动 (修改 Creature 名称、移除一个正在用的工具) 会让回放出错 — 如果要完美还原，把会话固定在原本的 config 上。

## 中断与恢复流程

```bash
kt run @kt-biome/creatures/swe
# 运行一段时间... 然后 Ctrl+C
# 之后：
kt resume --last
```

按下 Ctrl+C 后，代理会正常退出：执行完正在运行的工具、刷新 session store，并打印恢复提示。若强制终止进程（SIGKILL），则会跳过最后一次刷新；但由于写入是 append-only，最近的大部分状态通常仍会保留在磁盘上。

## 复制或封存会话

```bash
# 备份
cp ~/.kohakuterrarium/sessions/swe_20240101.kohakutr ~/backups/

# 从移动后的位置 resume
kt resume ~/backups/swe_20240101.kohakutr

# 不做完整 resume 只查看 (只读 CLI 后续会提供；目前先用 Python)
```

用 Python 查看：

```python
from kohakuterrarium.session.store import SessionStore
store = SessionStore("~/backups/swe_20240101.kohakutr")
print(store.load_meta())
for agent, event in store.get_all_events():
    print(agent, event["type"])
store.close()
```

## 压缩

上下文接近上限时，压缩会缩短对话。每只 Creature 都可以单独配置：

```yaml
compact:
  enabled: true
  threshold: 0.8              # context 到 window 的 80% 就压缩
  target: 0.5                 # 压完目标剩 50%
  keep_recent_turns: 5        # 最后 N 回合一定保留原样
  compact_model: gpt-4o-mini  # 摘要用的便宜模型
```

压缩会在后台运行 (参见 [概念 / 记忆与压缩](../concepts/modules/memory-and-compaction.md)) — 控制器照常工作；新摘要好了再把对话替换掉。每次压缩都会记成一个事件。

手动压缩：在 CLI/TUI 的提示符下执行

```
/compact
```

要把长会话交给人接手、或把它当成下一次执行的 context 时很实用。

## 记忆搜索

会话本身也是一个可搜索的知识库。建立索引后：

```bash
kt embedding ~/.kohakuterrarium/sessions/swe.kohakutr
kt search swe "auth bug"
```

代理也可以通过 `search_memory` 工具进行搜索。完整流程见：[记忆指南](memory.md)。

## 关掉持久化

有时候就只想运行一次不留下痕迹：

```bash
kt run @kt-biome/creatures/swe --no-session
```

不会产生 `.kohakutr`。这也会让压缩无法从磁盘回收之前的回合 (但内存里还是会压)。

## 疑难排解

- **压缩跑不完 / OOM**。 Compact model 默认是跟控制器一样的重模型。把 `compact_model` 设置为便宜的 (`gpt-4o-mini`、`claude-haiku`)。
- **Resume 出现 `tool not registered`**。 Creature config 改了 (某个工具被移除)，但对话还在引用它。手动把 `config.yaml` 里的工具加回来，或开新会话。
- **`kt resume` 找不到我刚刚看到的会话**。 会话会通过文件名前缀匹配 `~/.kohakuterrarium/sessions/` 的。如果你改名或搬过，就传完整路径。
- **`.kohakutr` 很大**。 事件日志是 append-only；长会话会膨胀。封存旧的、或把工作切到不同会话。压缩缩的是活动对话，完整事件历史仍会保留，供搜索使用。
- **恢复后看不到子代理输出**。 子代理对话是在它完成时才存的。如果父代理在子代理运行到一半时被打断，最新快照就只到上一个检查点为止。

## 延伸阅读

- [记忆指南](memory.md) — 在会话历史上进行 FTS、语义和混合搜索。
- [配置指南](configuration.md) — 压缩 recipe 与会话旗标。
- [程序化使用指南](programmatic-usage.md) — 供自定义查看使用的 `SessionStore` API。
- [概念 / 记忆与压缩](../concepts/modules/memory-and-compaction.md) — 压缩如何工作。
