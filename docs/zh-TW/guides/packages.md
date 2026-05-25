---
title: 套件
summary: 透過 kt install 安裝 pack、理解 kohaku.yaml manifest、@pkg/ 參照，以及發佈你自己的 pack。
tags:
  - guides
  - package
  - distribution
---

# 套件

給想在專案之間共享生物、生態瓶、工具或外掛的讀者。

KohakuTerrarium 的 package，就是一個帶有 `kohaku.yaml` manifest 的目錄。它可以包含 creatures、terrariums、自訂工具、plugins 與 LLM presets。`kt install` 會把它安裝到 `~/.kohakuterrarium/packages/<name>/`，而 `@<name>/path` 語法則可以參照其中任何內容。

概念先讀：[邊界](../concepts/boundaries.md) —— package 是框架用來讓「共享可重用零件」變得廉價的機制。

## 官方 pack：`kt-biome`

多數人第一個會安裝的 package 是 `kt-biome`——這是展示型 pack，裡面有 `swe`、`reviewer`、`researcher`、`ops`、`creative`、`general`、`root` 生物，也有像 `swe_team` 與 `deep_research` 這些生態瓶，外加一些外掛。

```bash
kt install @kt-biome
kt run @kt-biome/creatures/swe
```

`@kt-biome` 短寫會透過 marketplace 解析（詳見下文）；若想繞過 marketplace，`kt install https://github.com/Kohaku-Lab/kt-biome.git` 仍然有效。

當你要做自己的 pack 時，把 `kt-biome` 當成參考範本來看。

## Manifest：`kohaku.yaml`

```yaml
name: my-pack
version: "0.1.0"
description: "My shared agent components"

creatures:
  - name: researcher           # 對應 creatures/researcher/ 資料夾

terrariums:
  - name: research_team        # 對應 terrariums/research_team/ 資料夾

tools:
  - name: my_tool
    module: my_pack.tools.my_tool
    class: MyTool

plugins:
  - name: my_guard
    module: my_pack.plugins.my_guard
    class: MyGuard

llm_presets:
  - name: my-custom-model

python_dependencies:
  - httpx>=0.27
  - pymupdf>=1.24
```

目錄結構：

```
my-pack/
  kohaku.yaml
  creatures/researcher/config.yaml
  terrariums/research_team/config.yaml
  prompts/git-safety.md
  skills/repo-surgery/SKILL.md
  my_pack/                     # 可安裝的 python package
    __init__.py
    tools/my_tool.py
    plugins/my_guard.py
    io/discord.py
    triggers/webhook.py
    commands/handoff.py
    user_commands/deploy.py
```

Python 模組會用點分路徑解析（`my_pack.tools.my_tool:MyTool`）。設定則透過 `@my-pack/creatures/researcher` 解析。

如果宣告了 `python_dependencies`，`kt install` 安裝時也會一併安裝這些 Python 依賴。

### 更多 manifest 槽位

除了 `tools`、`plugins`、`llm_presets`，package 還可以貢獻：

- `io:` — package 解析的輸入 / 輸出模組類別
- `triggers:` — package 解析的 trigger 類別
- `skills:` — creature 可發現的程序化技能包（`SKILL.md`）
- `commands:` — Controller `##name##` 指令
- `user_commands:` — 使用者可輸入的斜線命令
- `prompts:` / `templates:` — Prompt 的可重用 Jinja include 片段
- `framework_hints:` — 對內建 framework-hint 文案的 package 級覆寫

衝突策略是混合的：

- tools / plugins / io / triggers / user_commands / commands 共享同一個名稱
  空間，衝突時報錯或要求顯式覆寫；
- 程序化技能（skills）相反 —— last-wins，且較窄的作用域（project / user /
  creature）會覆寫 package 自帶的副本。

## 安裝模式

### Marketplace 短寫（`@name`）

```bash
kt install @kt-biome              # 解析到最新的 non-yanked 版本
kt install @kt-biome@v1.2.0       # 指定版本
kt install @myfork/kt-biome       # 限定來源
```

`@`-前綴形式會透過 marketplace（[詳見下文](#marketplace-與-name-解析)）解析為 git URL，然後跟 `kt install <git-url>` 一樣 clone 到 `~/.kohakuterrarium/packages/<name>/`。**`@` 形式不支援 editable 安裝** —— 請先 clone，再用 `-e` 安裝。

### Git URL（clone）

```bash
kt install https://github.com/you/my-pack.git
```

會 clone 到 `~/.kohakuterrarium/packages/my-pack/`。更新則用 `kt update my-pack`。

### 本機路徑（copy）

```bash
kt install ./my-pack
```

會把整個資料夾複製進去。更新方式是重新執行 `kt install`，或直接修改那份複本。

### 本機路徑（editable）

```bash
kt install ./my-pack -e
```

會寫入 `~/.kohakuterrarium/packages/my-pack.link`，指向原始碼目錄。之後你在原始碼的修改會立即生效——不需要重新安裝。很適合開發時迭代。

### 解除安裝

```bash
kt uninstall my-pack
```

## 解析 `@pkg/path`

`@my-pack/creatures/researcher` →

- 如果存在 `my-pack.link`：追蹤這個指標。
- 否則：解析到 `~/.kohakuterrarium/packages/my-pack/creatures/researcher/`。

這套機制會被 `kt run`、`kt terrarium run`、`kt edit`、`kt update`、`base_config:` 繼承，以及程式化的 `Agent.from_path(...)` 使用。

## Marketplace 與 `@name` 解析

[TerrariumMarket](https://github.com/Kohaku-Lab/TerrariumMarket) 是 KohakuTerrarium package 的公共 marketplace。它是一個公開 GitHub repo，裡面有一個 `registry.yaml` 與每個 package 一個目錄。`kt install @<name>` 讀這個檔案、解析出 git URL，然後照常安裝。

框架會把索引快取在 `~/.kohakuterrarium/marketplace/cache.json`，TTL 一小時（ETag 條件式重新驗證）。冷快取離線 = 明確錯誤。暖快取離線 = 靜默落回快取資料 + 警告 log。

### CLI 指令

```bash
kt marketplace            # 等同 `list`：顯示已配置的來源
kt marketplace list
kt marketplace refresh    # 強制 bust 快取 + 重抓
kt marketplace search [query] [--tag <t>] [--author <a>] [--json]
kt marketplace info @<name>

kt marketplace add <url> [--alias <name>]   # 新增自訂來源
kt marketplace remove <url-or-alias>
kt marketplace reset                         # 還原預設（只剩 TerrariumMarket）
```

### Spec 語法

| 形式 | 解析到 |
|---|---|
| `@kt-biome` | `kt-biome` 在第一個有的來源中、最新的 non-yanked 版本 |
| `@kt-biome@v1.2.0` | 指定版本（即使 yanked 也允許，方便可重現） |
| `@myfork/kt-biome` | 限制 `kt-biome` 只從別名 `myfork` 的來源查找 |

### 配置來源

預設來源只有 TerrariumMarket。要加 fork 或自架：

```bash
kt marketplace add https://raw.githubusercontent.com/<owner>/<repo>/main/registry.yaml --alias myfork
```

來源按設定順序合併；同名 package 以最先出現的為準（影子化會記 log）。設定儲存在 `~/.kohakuterrarium/marketplace-sources.json`。

環境變數一次性覆寫（不寫設定檔）：

```bash
KT_MARKETPLACE_SOURCES=https://a.test/r.yaml,https://b.test/r.yaml kt marketplace search
KT_MARKETPLACE_CACHE_TTL=0 kt marketplace search   # 這次跳過快取
```

### 在 app 裡瀏覽

桌面 / 網頁 app 的 **Settings → Extensions** tab 現在是兩欄的 Catalog 檢視：**Browse**（marketplace package + Install 按鈕）與 **Installed**（本地已裝的，可 Uninstall、有 "Update available" 提示）。同樣走 `@<name>` 的安裝流程，所以 CLI 的 `kt install @kt-biome` 與 app 裡按 Install 走的是同一條程式路徑。

## 探索指令

```bash
kt list                         # 已安裝 package + 本機 agents
kt info path/or/@pkg/creature   # 查看單一設定的細節
kt extension list               # 所有 package 提供的 tools/plugins/presets
kt extension info my-pack       # package 中繼資料 + 內容清單
kt marketplace                  # 已配置的 marketplace 來源
kt marketplace search           # 瀏覽 marketplace（全部）
kt marketplace search biome     # 子串 / tag 過濾
kt marketplace info @kt-biome   # marketplace 條目詳細資訊
```

`kt extension list` 看本地裝了什麼；`kt marketplace search` 看可以裝什麼。

## 編輯已安裝設定

```bash
kt edit @my-pack/creatures/researcher
```

會用 `$EDITOR` 開啟 `config.yaml`（沒有的話退回 `$VISUAL`，再退回 `nano`）。如果是 editable install，編到的是原始碼；如果是一般安裝，編到的是 `~/.kohakuterrarium/packages/` 下面那份複本。

## 發佈

1. 把 repo push 到 git（GitHub、GitLab、自架都可以——只要 `git clone` 能處理）。
2. 打版本 tag：`git tag v0.1.0 && git push --tags`。
3. 每次發版時同步更新 `kohaku.yaml` 裡的 `version:`。
4. **建議**：到 TerrariumMarket 登記 package，讓使用者可以用 `kt install @your-package`。開 PR 加 `entries/<your-package>/entry.yaml` 與 `entries/<your-package>/README.md` 到 [Kohaku-Lab/TerrariumMarket](https://github.com/Kohaku-Lab/TerrariumMarket)；CI 會驗證 schema 與 tag 存在性；維護者審核後合併。詳見 [contributing guide](https://github.com/Kohaku-Lab/TerrariumMarket/blob/main/CONTRIBUTING.md)。
5. 不登記也行，直接分享 URL：`kt install https://your/repo.git`。
4. 分享 URL：`kt install https://your/repo.git`。

登記到 TerrariumMarket **不是必要** —— package 仍然只是帶有 `kohaku.yaml` 的 git repo，直接 URL 安裝的路徑完全沒變。Marketplace 只是疊在上面的探索層，不是取代品。

### 版本管理

請讓 `version:` 與 git tag 保持一致。`kt update` 底層就是做 `git pull`；如果使用者想固定在某個 tag，也可以手動 checkout：

```bash
cd ~/.kohakuterrarium/packages/my-pack
git checkout v0.1.0
```

## 執行時的擴充發現

當框架載入一個生物時，loader 會先在生物自己的設定裡查工具／外掛名稱，再查已安裝 package 的 manifest。Package 宣告的工具，會透過設定中的 `type: package` 暴露出來：

```yaml
tools:
  - name: my_tool
    type: package          # 透過 kohaku.yaml 裡的 `tools:` 清單解析
```

這讓某個 package 裡的 creature，也能參照另一個 package 宣告的工具，只要兩者都已安裝即可。

## 疑難排解

- **`@my-pack/...` 無法解析。** 用 `kt list` 確認 package 已安裝。若是 editable install，也檢查 `.link` 檔是否指向存在的目錄。
- **`kt update my-pack` 顯示 "skipped"。** Editable 與非 git package 都不能透過 `kt update` 更新。請直接改原始碼（editable），或重新安裝（copy）。
- **`python_dependencies` 沒有安裝。** 確認 `kt install` 在目前環境中有安裝權限（建議用 virtualenv，或 `pip install --user`）。
- **Package 工具遮蔽了內建工具。** 內建工具會優先解析。若你想讓自己的版本生效，請替 package 工具改名。

## 延伸閱讀

- [生物](creatures.md) — 如何把 creature 打包。
- [自訂模組](custom-modules.md) — 撰寫要隨 package 一起發佈的工具／外掛。
- [參考 / CLI](../reference/cli.md) — `kt install`、`kt list`、`kt extension`。
- [`kt-biome`](https://github.com/Kohaku-Lab/kt-biome) — 參考 package。
