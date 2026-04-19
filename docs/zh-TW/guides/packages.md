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
kt install https://github.com/Kohaku-Lab/kt-biome.git
kt run @kt-biome/creatures/swe
```

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
  my_pack/                     # 可安裝的 python package
    __init__.py
    tools/my_tool.py
    plugins/my_guard.py
```

Python 模組會用點分路徑解析（`my_pack.tools.my_tool:MyTool`）。設定則透過 `@my-pack/creatures/researcher` 解析。

如果宣告了 `python_dependencies`，`kt install` 安裝時也會一併安裝這些 Python 依賴。

## 安裝模式

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

## 探索指令

```bash
kt list                         # 已安裝 package + 本機 agents
kt info path/or/@pkg/creature   # 查看單一設定的細節
kt extension list               # 所有 package 提供的 tools/plugins/presets
kt extension info my-pack       # package 中繼資料 + 內容清單
```

`kt extension list` 是最快看出你目前安裝環境裡有哪些擴充可用的方法。

## 編輯已安裝設定

```bash
kt edit @my-pack/creatures/researcher
```

會用 `$EDITOR` 開啟 `config.yaml`（沒有的話退回 `$VISUAL`，再退回 `nano`）。如果是 editable install，編到的是原始碼；如果是一般安裝，編到的是 `~/.kohakuterrarium/packages/` 下面那份複本。

## 發佈

1. 把 repo push 到 git（GitHub、GitLab、自架都可以——只要 `git clone` 能處理）。
2. 打版本 tag：`git tag v0.1.0 && git push --tags`。
3. 每次發版時同步更新 `kohaku.yaml` 裡的 `version:`。
4. 分享 URL：`kt install https://your/repo.git`。

沒有中央註冊表。Package 本質上就是帶有 `kohaku.yaml` 的 git repo。

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
