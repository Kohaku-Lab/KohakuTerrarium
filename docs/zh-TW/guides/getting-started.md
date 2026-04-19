---
title: 快速開始
summary: 安裝 KohakuTerrarium、安裝 kt-biome 展示套件，並在幾分鐘內跑起一個可用的 agent。
tags:
  - guides
  - install
  - getting-started
---

# 快速開始

給從未執行過 KohakuTerrarium、但想在幾分鐘內於自己電腦上跑起一個可用 agent 的讀者。

KohakuTerrarium 提供核心框架，以及可重用的生物／外掛套件安裝路徑。官方套件 `kt-biome` 提供可直接使用的 SWE agent、reviewer、researcher，以及幾個生態瓶。你不需要自己寫任何東西就能試用。

概念預習：[什麼是生物](../concepts/foundations/what-is-an-agent.md)、[為什麼是這個框架](../concepts/foundations/why-kohakuterrarium.md)。

## 1. 安裝

### 從 PyPI 安裝（建議）

```bash
pip install kohakuterrarium
# 或安裝更多選用相依（語音、embeddings 等）
pip install "kohakuterrarium[full]"
```

這會提供 `kt` 指令。請確認：

```bash
kt --version
```

### 從原始碼安裝（用於開發）

```bash
git clone https://github.com/Kohaku-Lab/KohakuTerrarium.git
cd KohakuTerrarium
uv pip install -e ".[dev]"
```

如果你想讓 `kt web` 或 `kt app` 提供前端，請先建置一次：

```bash
npm install --prefix src/kohakuterrarium-frontend
npm run build --prefix src/kohakuterrarium-frontend
```

若未執行建置步驟，`kt web` 只會印出提示訊息，而 `kt app` 會無法開啟。

## 2. 安裝預設生物套件

`kt-biome` 內含開箱即用（OOTB）的生物（`swe`、`reviewer`、`researcher`、`ops`、`creative`、`general`、`root`）與幾個生態瓶。

```bash
kt install https://github.com/Kohaku-Lab/kt-biome.git
kt list
```

已安裝套件會放在 `~/.kohakuterrarium/packages/<name>/`，並以 `@<package>/path` 語法引用。

## 3. 驗證模型提供者

選一種：

**Codex（ChatGPT 訂閱，無需 API key）**
```bash
kt login codex
kt model default gpt-5.4
```

會開啟瀏覽器視窗；完成 device-code 流程後，token 會寫入 `~/.kohakuterrarium/codex-auth.json`。

**相容 OpenAI 的提供者（API key）**
```bash
kt config key set openai          # 會提示你輸入 key
kt config llm add                 # 互動式預設建立器
kt model default <preset-name>
```

**其他提供者**：`anthropic`、`openrouter`、`gemini` 等都是內建後端。詳情請見 `kt config provider list` 與[設定](configuration.md)。

## 4. 執行一隻生物

```bash
kt run @kt-biome/creatures/swe --mode cli
```

你會進入 SWE agent 的互動式提示環境。輸入一個請求後，它會在目前工作目錄中使用 shell、檔案與編輯工具。Ctrl+C 可乾淨結束，並印出恢復提示。

模式：

- `cli` — Rich 行內介面（TTY 時預設）
- `tui` — 全螢幕 Textual 應用程式
- `plain` — 純 stdout/stdin，適合 pipe 或 CI

覆寫單次執行的模型：

```bash
kt run @kt-biome/creatures/swe --llm claude-opus-4.6
```

## 5. 恢復

工作階段會自動儲存到 `~/.kohakuterrarium/sessions/*.kohakutr`（除非你傳入 `--no-session`）。重新啟動任何過去的工作階段：

```bash
kt resume --last                # 最近一次
kt resume                       # 互動式選擇器
kt resume swe_20240101_1234     # 依名稱前綴
```

agent 會根據儲存的設定重建、重播對話、重新註冊可恢復的觸發器，並還原 scratchpad 與頻道歷史。完整持久化模型請見[工作階段](sessions.md)。

## 6. 搜尋工作階段歷史（提示）

因為工作階段是以可操作的形式儲存，所以你可以像查詢小型本機知識庫一樣搜尋它們：

```bash
kt embedding ~/.kohakuterrarium/sessions/<name>.kohakutr
kt search <name> "auth bug"
```

完整教學請見[記憶](memory.md)。

## 7. 開啟 Web UI 或桌面應用程式

```bash
kt web           # 本機 Web 伺服器，位於 http://127.0.0.1:8001
kt app           # 原生桌面視窗（需要 pywebview）
```

若你需要比終端機生命週期更長的常駐行程：

```bash
kt serve start
kt serve status
kt serve logs --follow
kt serve stop
```

何時該用哪一種介面，請見 [Serving](serving.md)。

## 疑難排解

- **`kt login codex` 沒有開啟瀏覽器。** 複製 CLI 印出的 URL，手動貼到瀏覽器中。如果 callback port 被占用，請先釋放再重試。
- **`kt web` 沒有提供內容／`/` 回傳 404。** 前端尚未建置。執行 `npm install --prefix src/kohakuterrarium-frontend && npm run build --prefix src/kohakuterrarium-frontend`。從 PyPI 安裝時通常已內建建置好的資產。
- **寫入 `~/.kohakuterrarium/` 時出現 `Permission denied`。** 框架會在首次執行時建立該目錄。若它已存在但屬於其他使用者（常見於 `sudo pip install` 之後），請修正權限：`chown -R $USER ~/.kohakuterrarium`。
- **`kt run` 顯示 "no model set"。** 你跳過了第 3 步。請執行 `kt model default <name>` 或傳入 `--llm <name>`。
- **`ModuleNotFoundError: pywebview`。** `kt app` 需要桌面額外依賴：`pip install 'kohakuterrarium[full]'`（或改用 `kt web`）。

## 另請參見

- [生物](creatures.md)：了解如何繼承或自訂 OOTB agent。
- [工作階段](sessions.md)：了解恢復語意與壓縮。
- [Serving](serving.md)：決定該用 `kt web`、`kt app` 還是 `kt serve`。
- [參考 / CLI](../reference/cli.md)：查看所有指令與旗標。
