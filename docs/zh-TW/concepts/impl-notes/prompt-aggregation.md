---
title: 提示詞聚合
summary: 說明 system prompt 如何由人格、工具清單、框架提示與按需載入的技能組合而成。
tags:
  - concepts
  - impl-notes
  - prompt
---

# 提示詞聚合

## 這要解決的問題

代理的「system prompt」不是單一字串，而是由下列內容組合而成：

- 生物的人格／角色，
- 可用工具清單（名稱 + 描述），
- 在這個生物所選格式中，實際上該如何呼叫工具，
- 任何頻道拓樸資訊（在生態瓶中），
- 對具名輸出的說明（讓 LLM 知道何時該送到
  Discord、何時該送到 stdout），
- 由外掛貢獻的區段（專案規則、環境資訊等），
- 每個工具的完整文件（若使用 `static` skill
  模式）——或者完全不包含這些內容（若使用 `dynamic` 模式）。

如果把這件事交給手寫 prompt，你就會把 bug 一起交付出去：
工具清單過時、呼叫語法錯誤、區段重複。這個框架會以確定性的方式
組裝整份內容。

## 曾考慮的方案

- **手寫 prompts。** 很脆弱。每次新增工具都可能壞掉。
- **永遠使用完整靜態 prompts。** 很完整，但也非常大——光是工具文件
  就可能有數萬 tokens。
- **按需載入文件。** 只提供名稱；需要時再讓代理透過 `info`
  framework command 拉取完整文件。
- **可設定。** 每個生物自行選擇取捨：`skill_mode:
  dynamic` 或 `skill_mode: static`。這就是實際採用的方案。

## 我們實際怎麼做

`prompt/aggregator.py:aggregate_system_prompt(...)` 會依照以下順序
串接各個區段：

1. **基礎 prompt。** 使用 Jinja2 渲染（safe-undefined fallback）；
   內容包含生物的人格，以及宣告在 `prompt_context_files`
   底下的任何專案上下文檔案。
2. **工具區段。**
   - `skill_mode: dynamic` → 工具*索引*：每個工具提供名稱 +
     一行描述。代理會在需要時透過 `info` framework command
     載入完整文件。
   - `skill_mode: static` → 直接內嵌每個工具的完整文件。
3. **頻道拓樸區段**（僅限生態瓶中的生物）。描述
   「你會監聽 X、Y；你可以傳送到 Z；另一端是誰。」
   由 `terrarium/config.py:build_channel_topology_prompt`
   產生。
4. **框架提示。** 說明如何用這個生物的格式呼叫工具
   （bracket / XML / native）、如何使用內嵌 framework commands
   （`read_job`、`info`、`jobs`、`wait`），以及輸出協定
   長什麼樣子。
5. **具名輸出區段。** 對每個 `named_outputs.<name>`，簡短說明
   何時該把文字路由到該處。
6. **Prompt 外掛區段。** 每個已註冊的 prompt plugin
   （依優先級排序，由低到高）都會貢獻一個區段。內建有：
   `ToolListPlugin`、`FrameworkHintsPlugin`、`EnvInfoPlugin`、
   `ProjectInstructionsPlugin`。

當 MCP 工具已連線時，還會額外插入一個名為
「Available MCP Tools」的區段，依伺服器用條列方式列出工具。

## 維持不變的條件

- **具確定性。** 給定相同的 config + registry + plugin 集合，
  產生的 prompt 在位元組層級上是穩定的。
- **自動區段不會取代手寫區段。** 如果你在 `system.md` 裡自行放入
  工具清單，aggregator 的工具清單仍然會被加入；框架不會依內容去重。
- **Skill mode 是調節旋鈕，不是策略。** 系統中其他任何部分都不會因
  `skill_mode` 而改變——它純粹是 prompt 大小上的取捨。
- **外掛順序是明確的。** 依優先級排序。若優先級相同，則保持穩定的
  插入順序。

## 程式碼中的位置

- `src/kohakuterrarium/prompt/aggregator.py` — 組合函式。
- `src/kohakuterrarium/prompt/plugins.py` — 內建 prompt plugins。
- `src/kohakuterrarium/prompt/templates.py` — Jinja 安全渲染。
- `src/kohakuterrarium/terrarium/config.py` — 頻道拓樸區塊。
- `src/kohakuterrarium/core/agent.py` — `_init_controller()` 會在
  啟動時呼叫 aggregator 一次。

## 另請參閱

- [Plugin](../modules/plugin.md) — 如何撰寫 prompt plugins。
- [Tool](../modules/tool.md) — 工具文件如何被註冊。
- [reference/configuration.md — skill_mode, tool_format, include_*](../../reference/configuration.md) — 相關設定旋鈕。
