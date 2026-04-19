---
title: 第一個生物
summary: 撰寫生物設定、在 CLI / TUI / Web 中執行，並自訂提示詞與工具。
tags:
  - tutorials
  - creature
  - getting-started
---

# 第一個生物

**問題：**你已經安裝好 KohakuTerrarium，現在想從零開始做出一個可自訂、可執行，而且你自己也能理解的生物。

**完成狀態：**你已經執行過一個現成生物、恢復過工作階段、把生物 fork 到自己的資料夾、修改了 system prompt、加入一個工具，然後再次執行它。

**先決條件：**`PATH` 中已有 `kt`（從 repo 執行 `uv pip install -e .`，或使用已發佈版本安裝），以及一台可呼叫 API 的機器。

生物是一個獨立代理 —— controller + input + output + tools（+ 可選的 triggers、sub-agents、plugins）。本教學會走最短路徑，帶你碰到所有相關的核心組件。

## 步驟 1 —— 安裝預設套件

目標：把隨附的生物（swe、general、reviewer、root、……）裝到你的機器上，這樣你就能透過 `@kt-biome/...` 參照它們。

```bash
kt install https://github.com/Kohaku-Lab/kt-biome.git
```

`kt install` 可接受 git URL 或本機路徑。完成後，套件會位於 `~/.kohakuterrarium/packages/kt-biome/`，任何設定都能透過 `@kt-biome/...` 參照它。

驗證：

```bash
kt list
```

你應該會看到 `kt-biome` 以及其中包含的生物（`swe`、`general`、`reviewer`、`root`、`researcher`、`ops`、`creative`）。

## 步驟 2 —— 驗證 LLM

目標：選擇一個 provider 並登入。SWE 生物使用預設模型，因此你需要對應的憑證。

如果你有 ChatGPT 訂閱並想使用 OAuth：

```bash
kt login codex
```

否則，請為其他後端（OpenAI、Anthropic、OpenRouter、……）設定金鑰：

```bash
kt config key set openai
```

你也可以設定預設模型 preset，這樣每次執行命令時就不用帶 `--llm`：

```bash
kt model list
kt model default gpt-5.4
```

## 步驟 3 —— 執行現成生物

目標：在修改任何東西之前，先看看一個完整生物如何運作。

```bash
kt run @kt-biome/creatures/swe --mode cli
```

問它一個簡單問題：

```text
> 列出這個目錄中的 python 檔案
```

你應該會看到它串流輸出答案、呼叫工具（`glob`、`read`），並顯示結果。用 `/exit` 或 Ctrl+C 離開。離開時，`kt` 會印出類似 `kt resume <session-name>` 的恢復提示；工作階段會自動儲存到 `~/.kohakuterrarium/sessions/*.kohakutr`。

## 步驟 4 —— 恢復工作階段

目標：確認工作階段可持久保存且可恢復。

```bash
kt resume --last
```

這會接續最近一次的工作階段。你會回到同一段對話，並保留相同的草稿區、工具歷程與模型。完成後再離開即可。

## 步驟 5 —— 將生物 fork 到本機資料夾

目標：擁有一個屬於你自己的生物，並以 SWE 為基底疊加修改。

```bash
mkdir -p creatures/my-swe/prompts
```

`creatures/my-swe/config.yaml`：

```yaml
name: my_swe
version: "1.0"
base_config: "@kt-biome/creatures/swe"

system_prompt_file: prompts/system.md
```

`creatures/my-swe/prompts/system.md`：

```markdown
# My SWE

You are a careful repo-surgery agent.

House rules:
- read before editing, always
- keep diffs small and obvious
- when unsure, ask rather than guess
```

`base_config` 會把 SWE 生物中的所有內容帶進來 —— LLM 預設、工具集、sub-agents，以及上游 system prompt。你的 `system.md` 會附加到繼承而來的 prompt 後面（prompt 會沿著繼承鏈串接）。其他未設定的內容都會維持繼承。

## 步驟 6 —— 加入一個工具

目標：在繼承的工具清單上多加一個項目。Web 搜尋是一個很實用的選擇。

編輯 `creatures/my-swe/config.yaml`：

```yaml
name: my_swe
version: "1.0"
base_config: "@kt-biome/creatures/swe"

system_prompt_file: prompts/system.md

tools:
  - { name: web_search, type: builtin }
```

像 `tools:` 與 `subagents:` 這類清單，除非你透過 `no_inherit:` 明確選擇不繼承，否則都會在繼承清單上 **延伸**（並依 `name` 去重）。因此，這裡會把 `web_search` 加進 SWE 的工具集，而不需要重新宣告其他項目。

## 步驟 7 —— 執行你的生物

```bash
kt run creatures/my-swe --mode cli
```

問它一個需要上網的問題：

```text
> 搜尋網路上的 "kohakuterrarium github"，並摘要第一個結果
```

你應該會看到 system prompt 中的 house rules 生效，而新的 `web_search` 工具也可供使用。正常結束即可；工作階段會自動儲存。

## 你學到了什麼

- 生物是**一個帶有設定的資料夾**，而不是單純一段 prompt。
- `kt install` + `kt login` + `kt run` 就是完整的 OOTB 流程。
- `kt resume` 會從磁碟恢復完整工作階段。
- `base_config: "@pkg/creatures/<name>"` 會繼承所有內容；純量欄位覆寫，`tools:` / `subagents:` 則是延伸。
- `system_prompt_file` 會沿著繼承鏈串接。

## 接下來讀什麼

- [Creatures](../guides/creatures.md) —— 在脈絡中理解每個設定欄位。
- [Configuration reference](../guides/configuration.md) —— 精確的 schema 與繼承規則。
- [First custom tool](first-custom-tool.md) —— 當 `builtin` 不夠用時該怎麼做。
- [What is an agent](../concepts/foundations/what-is-an-agent.md) —— 幫助你理解這種設定形狀的心智模型。