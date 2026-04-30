---
title: KohakuTerrarium 文件說明
summary: 概念模型、指南、教學、參考資料與開發說明的首頁。
tags:
  - overview
  - docs
---

# KohakuTerrarium 文件說明

KohakuTerrarium 是一個用來建構真正代理的框架——不只是 LLM 包裝器。

其中的一級抽象是 **creature**：一種可獨立運作的代理，擁有自己的控制器、工具、子代理、觸發器、提示詞與輸入/輸出。Creature 可以獨立執行、繼承自另一個 creature，或隨套件一同發布。**terrarium** 則是可選的多代理連接層，透過 channel 組合多個 creature。所有內容都是 Python——你可以把其中任何部分嵌入自己的程式碼中。

這份文件分為四個層次：tutorials（教學式）、guides（任務導向）、concepts（心智模型）與 reference（完整查詢）。請依照你目前所在階段選擇適合的內容。

## 選擇你的路徑

| 你現在是... | 從這裡開始 |
|---|---|
| **正在評估這個專案** | [Getting Started](guides/getting-started.md) · [What is an agent](concepts/foundations/what-is-an-agent.md) · [`kt-biome`](https://github.com/Kohaku-Lab/kt-biome) |
| **正在操作 CLI / 儀表板** | [Getting Started](guides/getting-started.md) · [Serving](guides/serving.md) · [CLI Reference](reference/cli.md) |
| **正在建立 creature** | [Creatures](guides/creatures.md) · [Configuration](guides/configuration.md) · [Custom Modules](guides/custom-modules.md) |
| **正在嵌入到 Python** | [Programmatic Usage](guides/programmatic-usage.md) · [Composition](guides/composition.md) · [Python API](reference/python.md) |
| **正在為框架本身做出貢獻** | [Development](dev/README.md) · [Framework Internals](dev/internals.md) · [Testing](dev/testing.md) |

## 文件結構

### Tutorials

逐步式學習路徑。

- [First Creature](tutorials/first-creature.md)
- [First Terrarium](tutorials/first-terrarium.md)
- [First Python Embedding](tutorials/first-python-embedding.md)

### Guides

任務導向文件：「我要如何完成 X」。

- [Getting Started](guides/getting-started.md) — 安裝、驗證、執行、恢復。
- [Creatures](guides/creatures.md) — 結構、繼承、封裝。
- [Terrariums](guides/terrariums.md) — 多代理連接與 root agent。
- [Sessions](guides/sessions.md) — `.kohakutr` 持久化與恢復。
- [Memory](guides/memory.md) — 對工作階段歷史進行 FTS、語意與混合搜尋。
- [Configuration](guides/configuration.md) — 任務導向的「我要如何設定 X」。
- [Programmatic Usage](guides/programmatic-usage.md) — `Terrarium`、`Creature`、`Studio` 與底層 `Agent`。
- [Composition](guides/composition.md) — `>>`、`&`、`|`、`*` 管線。
- [Custom Modules](guides/custom-modules.md) — 工具、輸入、輸出、觸發器、子代理。
- [Plugins](guides/plugins.md) — 提示詞與生命週期外掛。
- [MCP](guides/mcp.md) — Model Context Protocol 伺服器。
- [Packages](guides/packages.md) — `kohaku.yaml`、安裝模式、發布。
- [Serving](guides/serving.md) — `kt web`、`kt app`、`kt serve` 守護程序。
- [Frontend Layout](guides/frontend-layout.md) — 儀表板面板與預設配置。
- [Examples](guides/examples.md) — `examples/` 樹的導覽。

### Concepts

心智模型——說明事情為什麼會是這樣。Concept 文件教的是模型，而不是欄位清單；它假設你想理解，而不只是完成設定。

- [Overview](concepts/README.md)
- [Foundations](concepts/foundations/README.md)
- [Modules](concepts/modules/README.md) — controller、input、trigger、tool、sub-agent、output、channel、plugin、memory、session。
- [Multi-agent](concepts/multi-agent/README.md) — terrarium、root agent、channel topology。
- [Python-native](concepts/python-native/README.md) — 將代理視為 Python 值，以及組合代數。
- [Patterns](concepts/patterns.md) — agent-inside-plugin、agent-inside-tool 與相關用法。
- [Boundaries](concepts/boundaries.md) — 何時該忽略這層抽象、何時這個框架不適合。
- [Implementation notes](concepts/impl-notes/) — 串流解析、提示詞聚合與其他內部細節。

### Reference

完整查詢資料。

- [CLI Reference](reference/cli.md) — 每一個 `kt` 指令與旗標。
- [Configuration Reference](reference/configuration.md) — 每一個設定欄位、型別與預設值。
- [HTTP API](reference/http.md) — REST 與 WebSocket 端點。
- [Python API](reference/python.md) — 類別、方法與協定。
- [Built-ins Catalog](reference/builtins.md) — 所有內建工具、子代理與 I/O 模組。
- [Plugin Hooks](reference/plugin-hooks.md) — 每個 hook 的簽章。

### Development

提供給框架本身的貢獻者。

- [Development home](dev/README.md)
- [Testing](dev/testing.md)
- [Framework Internals](dev/internals.md)
- [Frontend Architecture](dev/frontend.md)

## 程式碼庫地圖

原始碼是依執行時子系統組織，而不是依讀者意圖分類。每個子套件中的套件內 `README.md` 都會說明其內部職責與相依方向。

```
src/kohakuterrarium/
  core/             Agent 執行時、controller、executor、events、environment
  bootstrap/        LLM、tools、I/O、triggers 的初始化工廠
  cli/              CLI 指令處理器
  terrarium/        多代理執行時、拓樸連接、hot-plug
  builtins/         內建工具、子代理、I/O 模組、TUI、使用者指令
  builtin_skills/   供按需文件化工具與子代理使用的 Markdown skill 清單
  session/          持久化、記憶搜尋、embeddings
  serving/          與傳輸無關的服務管理與事件串流
  api/              FastAPI HTTP 與 WebSocket 伺服器
  modules/          tools、inputs、outputs、triggers、sub-agents 的協定
  llm/              LLM 提供者、profiles、API 金鑰管理
  parsing/          工具呼叫解析與串流
  prompt/           提示詞組裝、聚合、plugins、skill 載入
  testing/          測試基礎設施

src/kohakuterrarium-frontend/   Vue 網頁前端
kt-biome (separate repo)        展示用套件——creatures、terrariums、plugins
examples/                       可執行範例
docs/                           這個目錄樹
```

## 這份文件的承諾

- **Guides** 告訴你如何完成 X。
- **Concepts** 告訴你為什麼 X 會這樣運作。
- **Reference** 告訴你有哪些 X 存在。
- **Tutorials** 帶你從零開始做出第一個可用的 X。

如果某個頁面寫著「comprehensive」、「powerful」或「seamless」——它大概已經過時了。請送出 PR。