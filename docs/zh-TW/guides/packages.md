---
title: 套件
summary: 用 kt install 安裝套件包、kohaku.yaml manifest、@pkg/ 參照，以及發佈你自己的套件包。
tags:
  - guides
  - package
  - distribution
---

# 套件

寫給想在專案之間分享生物 (creature)、生態瓶、工具或外掛的讀者。

一個 KohakuTerrarium 套件就是一個帶 `kohaku.yaml` manifest 的目錄。裡面可以放生物、生態瓶、自訂工具、外掛、觸發器、I/O 模組、程序性 skill、控制器指令、使用者 slash 指令、提示詞片段、framework-hint 覆寫與 LLM preset。`kt install` 會把它放到 `~/.kohakuterrarium/packages/<name>/` 底下，之後就能用 `@<name>/path` 語法參照裡面的任何東西。

概念入門：[邊界](../concepts/boundaries.md)，套件就是框架讓「分享可重用元件」變便宜的方式。

## 官方套件包：`kt-biome`

大多數人安裝的第一個套件是 `kt-biome`：展示用套件包，內含 `swe`、`reviewer`、`researcher`、`ops`、`creative`、`general`、`root` 等生物、`swe_team` 與 `deep_research` 等生態瓶，還有幾個外掛。

```bash
kt install @kt-biome
kt run @kt-biome/creatures/swe
```

`@kt-biome` 這個短寫透過市集解析 (見下文)；想繞過市集的話，`kt install https://github.com/Kohaku-Lab/kt-biome.git` 一樣可以用。

要做自己的套件包時，把 `kt-biome` 當參考範本來研究。

## Manifest：`kohaku.yaml`

```yaml
name: my-pack
version: "0.1.0"
description: "My shared agent components"

creatures:
  - name: researcher           # folder at creatures/researcher/

terrariums:
  - name: research_team        # folder at terrariums/research_team/

tools:
  - name: my_tool
    module: my_pack.tools.my_tool
    class: MyTool

plugins:
  - name: my_guard
    module: my_pack.plugins.my_guard
    class: MyGuard

io:
  - name: discord_input
    module: my_pack.io.discord
    class: DiscordInput

triggers:
  - name: webhook
    module: my_pack.triggers.webhook
    class: WebhookTrigger

skills:
  - name: repo-surgery
    path: skills/repo-surgery
    description: Shared repo surgery workflow

commands:
  - name: handoff
    module: my_pack.commands.handoff
    class: HandoffCommand

user_commands:
  - name: deploy
    module: my_pack.user_commands.deploy
    class: DeployCommand

prompts:
  - name: git-safety
    path: prompts/git-safety.md

framework_hints:
  framework.execution_model.dynamic: |
    Use background work aggressively, but never duplicate it.

llm_presets:
  - name: my-custom-model

python_dependencies:
  - httpx>=0.27
  - pymupdf>=1.24
```

資料夾佈局：

```
my-pack/
  kohaku.yaml
  creatures/researcher/config.yaml
  terrariums/research_team/config.yaml
  prompts/git-safety.md
  skills/repo-surgery/SKILL.md
  my_pack/                     # installable python package
    __init__.py
    tools/my_tool.py
    plugins/my_guard.py
    io/discord.py
    triggers/webhook.py
    commands/handoff.py
    user_commands/deploy.py
```

Python 模組用點分路徑解析 (`my_pack.tools.my_tool:MyTool`)。設定檔用 `@my-pack/creatures/researcher` 解析。

`python_dependencies` (加上 `requirements.txt`，若存在的話) 由
`kt install` 透過 `sys.executable -m pip` 安裝，也就是裝進
KohakuTerrarium 自己所在的環境。傳 `--no-deps` 可以跳過；
依賴安裝失敗會拋 `PackageError`，不會降級成警告。

### 較新的 manifest 槽位

除了 `tools`、`plugins` 與 `llm_presets`，套件現在還可以提供：

- `io:`：由套件解析的輸入 / 輸出模組類別
- `triggers:`：由套件解析的觸發器類別
- `skills:`：生物可以發現的程序性 skill 包 (`SKILL.md`)
- `commands:`：控制器的 `##name##` 指令
- `user_commands:`：人可以打的 slash 指令
- `prompts:` / `templates:`：提示詞用的可重用 Jinja include 片段
- `framework_hints:`：套件層級覆寫內建的 framework-hint 文字

衝突政策刻意分成兩種：

- 工具 / 外掛 / I/O / 觸發器 / 使用者指令 / 控制器指令共用同一個
  名稱空間，衝突視為錯誤或必須明確覆寫；
- 程序性 skill 是例外：後者勝出 (last-wins)，作用域較窄的
  (專案 / 使用者 / 生物) 覆蓋套件附帶的版本。

## 安裝模式

### 市集 spec (`@name`)

```bash
kt install @kt-biome              # 最新的未下架版本
kt install @kt-biome@v1.2.0       # 明確鎖定版本
kt install @myfork/kt-biome       # 限定到特定來源的名稱
```

`@` 開頭的形式透過市集解析 ([見下文](#市集與-name-解析)) 成 git URL，再像 `kt install <git-url>` 一樣 clone 進 `~/.kohakuterrarium/packages/<name>/`。**`@` spec 不支援 editable 模式**：想用 editable 就先 clone，再用 `-e` 安裝。

### Git URL (clone)

```bash
kt install https://github.com/you/my-pack.git
```

Clone 進 `~/.kohakuterrarium/packages/my-pack/`。用 `kt update my-pack` 更新。

### 本地路徑 (複製)

```bash
kt install ./my-pack
```

把資料夾複製進去。更新時重跑 `kt install`，或直接編輯複製出來的版本。

### 本地路徑 (editable)

```bash
kt install ./my-pack -e
```

寫一個指向原始目錄的 `~/.kohakuterrarium/packages/my-pack.link`。原始目錄裡的修改立即可見，不用重新安裝。開發迭代時非常好用。

### 解除安裝

```bash
kt uninstall my-pack
```

## 解析 `@pkg/path`

`@my-pack/creatures/researcher` →

- 如果 `my-pack.link` 存在：跟著指標走。
- 否則：`~/.kohakuterrarium/packages/my-pack/creatures/researcher/`。

`@pkg/...` 參照在設定載入的瓶頸點解析，所以每個消費者都統一支援：
`kt run`、`kt edit`、`kt update`、`base_config:` 繼承、配方，
以及程式化載入器，例如 `Agent.build(...)`、`engine.add_creature(...)`、
`Terrarium.from_recipe(...)`、`compose.agent(...)`、
`Studio.sessions.start_creature(...)`。參照到未安裝的套件會拋
`kt.errors.PackageNotInstalledError` (指名套件、附上 `kt install`
提示)；格式錯誤的參照 (裸 `@`、跳脫套件根目錄的路徑) 拋
`kt.errors.PackageRefError`。

## 程式化 API：`kohakuterrarium.packages`

`kt install` / `kt list` 做的事都可以從
`kohakuterrarium.packages` import，它是惰性門面，import 本身
不會拖進市集 / 安裝器那一疊，碰到那些名稱才載入。

```python
from kohakuterrarium import packages

# 冪等安裝：批次腳本開頭就該呼叫這個。
# 回傳套件名稱；同名套件已安裝就立即返回
# (不做版本檢查，連鎖定版本的 spec 也一樣)。
packages.ensure("@kt-biome")

# 明確安裝 (市集 spec / git URL / 本地目錄)：
packages.install_package_spec("@kt-biome@v1.2.0")
packages.install_package("https://github.com/you/my-pack.git")
packages.install_package("./my-pack", editable=True)

packages.update_package("my-pack")        # git pull --ff-only；鎖定版本會拒絕
packages.uninstall_package("my-pack")

# 解析與列舉：
path = packages.resolve_package_path("@kt-biome/creatures/swe")
packages.is_package_ref("@kt-biome/creatures/swe")   # True
packages.packages_dir()                   # 尊重 KT_CONFIG_DIR
for pkg in packages.list_packages():
    print(pkg["name"], pkg["version"])
```

依賴政策：安裝函式都吃 `deps="auto" | "never"`
(`"auto"` 是預設，用 `sys.executable -m pip` 安裝；`"never"`
跳過 Python 依賴，等同 `--no-deps`)。失敗拋出
`PackageError` 家族的型別化錯誤 (`PackageRefError`、
`PackageNotInstalledError`、`PackagePathNotFoundError`)，
為了方便也從這個門面 re-export。

`packages.ensure(spec)` 只保證*存在*，不保證版本：要把特定版本
壓到既有的安裝上，呼叫 `install_package_spec("@pkg@vX.Y.Z")`。

完整符號列表 (manifest 槽位解析器、套件根目錄查詢) 在
[Python API 參考](../reference/python.md#packages)。

## 市集與 `@name` 解析

[TerrariumMarket](https://github.com/Kohaku-Lab/TerrariumMarket) 是 KohakuTerrarium 套件的公開市集。它是一個公開的 GitHub repo，裡面是一個 YAML 檔 (`registry.yaml`) 加上每個套件一個條目目錄。`kt install @<name>` 讀那個檔案把名稱解析成 git URL，然後照常安裝。

框架會抓取索引並快取在 `~/.kohakuterrarium/marketplace/cache.json`，TTL 一小時 (對上游做 ETag 重新驗證)。冷快取 + 離線 = 清楚的錯誤。熱快取 + 離線 = 靜默退回快取資料，並留一條警告 log。

### CLI 動詞

```bash
kt marketplace            # `list` 的別名：顯示已設定的來源
kt marketplace list
kt marketplace refresh    # 強制清快取 + 重抓
kt marketplace search [query] [--tag <t>] [--author <a>] [--json]
kt marketplace info @<name>

kt marketplace add <url> [--alias <name>]   # 加一個自訂來源
kt marketplace remove <url-or-alias>
kt marketplace reset                         # 還原成只剩預設來源
```

### Spec 語法

| 形式 | 解析成 |
|---|---|
| `@kt-biome` | 第一個有列出它的來源裡，`kt-biome` 最新的未下架版本 |
| `@kt-biome@v1.2.0` | 精確的版本鎖定 (為了可重現性，允許已下架的版本) |
| `@myfork/kt-biome` | 限定到別名 `myfork` 來源的 `kt-biome` |

### 設定來源

預設來源列表只有 TerrariumMarket。要加 fork 或自己的伺服器：

```bash
kt marketplace add https://raw.githubusercontent.com/<owner>/<repo>/main/registry.yaml --alias myfork
```

來源按查詢順序合併；同名第一個出現的勝出 (遮蔽會記 log)。設定持久化在 `~/.kohakuterrarium/marketplace-sources.json`。

環境變數覆寫 (一次性，不寫設定檔)：

```bash
KT_MARKETPLACE_SOURCES=https://a.test/r.yaml,https://b.test/r.yaml kt marketplace search
KT_MARKETPLACE_CACHE_TTL=0 kt marketplace search   # 這次呼叫繞過快取
```

### 從 app 裡瀏覽

桌面 / 網頁 app 的 **Settings → Extensions** 分頁現在是雙欄的「Catalog」視圖：**Browse** (市集套件 + Install 按鈕) 與 **Installed** (你本地的集合 + Uninstall 與「Update available」徽章)。背後跑的是同一套 `@<name>` 安裝流程，所以 CLI 的 `kt install @kt-biome` 和在 app 裡點 Install 走的是同一條程式路徑。

## 探索指令

```bash
kt list                         # 已安裝套件 + 本地 agent
kt info path/or/@pkg/creature   # 單一設定的細節
kt extension list               # 所有套件提供的工具/外掛/preset
kt extension info my-pack       # 套件 metadata + 內容物
kt marketplace                  # 已設定的市集來源
kt marketplace search           # 瀏覽市集 (所有套件)
kt marketplace search biome     # 子字串 + tag 過濾
kt marketplace info @kt-biome   # 市集條目的詳細視圖
```

`kt extension list` 是看本地裝了什麼最簡單的方式；`kt marketplace search` 則是看有什麼可以裝。

## 編輯已安裝的設定

```bash
kt edit @my-pack/creatures/researcher
```

用 `$EDITOR` 開啟 `config.yaml` (退而求其次用 `$VISUAL`，再來 `nano`)。Editable 安裝會編輯原始目錄；一般安裝會編輯 `~/.kohakuterrarium/packages/` 下的副本。

## 發佈

1. 把 repo 推上 git (GitHub、GitLab、自架，只要 `git clone` 拿得到就行)。
2. 打版本 tag：`git tag v0.1.0 && git push --tags`。
3. 每次發佈時更新 `kohaku.yaml` 裡的 `version:`。
4. **選用但建議**：把套件上架到 TerrariumMarket，讓使用者可以用 `kt install @your-package` 安裝。對 [Kohaku-Lab/TerrariumMarket](https://github.com/Kohaku-Lab/TerrariumMarket) 開 PR，加上 `entries/<your-package>/entry.yaml` + `entries/<your-package>/README.md`；CI 會驗證 schema 與 tag 存在；維護者合併。流程細節見[貢獻指南](https://github.com/Kohaku-Lab/TerrariumMarket/blob/main/CONTRIBUTING.md)。
5. 不上架也行，直接分享 URL：`kt install https://your/repo.git`。

上架 TerrariumMarket **不是必須的**：套件本質上仍是一個帶 `kohaku.yaml` 的 git repo，直接 URL 的安裝路徑沒有改變。市集是疊在那之上的探索層，不是替代品。

### 版本管理

讓 `version:` 跟 git tag 保持同步。`kt update` 底層做 `git pull`；鎖定在某個 tag 的使用者可以手動 checkout：

```bash
cd ~/.kohakuterrarium/packages/my-pack
git checkout v0.1.0
```

## 執行期的擴充探索

框架載入生物時，載入器先在生物的本地設定裡找工具 / 外掛名稱，再去已安裝套件的 manifest 找。套件宣告的工具在設定裡用 `type: package` 引用：

```yaml
tools:
  - name: my_tool
    type: package          # 透過 kohaku.yaml 的 `tools:` 清單解析
```

套件宣告的 I/O 與觸發器也是同一個模式：

```yaml
input:
  type: package
  name: discord_input

triggers:
  - type: package
    name: webhook
```

提示詞片段透過 Jinja include 解析：

```md
{% include "git-safety" %}
```

控制器 / 使用者指令則從套件 manifest 探索，不從生物資料夾。

只要兩個套件都有安裝，一個套件裡的生物就能引用另一個套件宣告的擴充。

## 疑難排解

- **`@my-pack/...` 解析失敗。** 先 `kt list` 確認套件有裝。Editable 安裝的話，檢查 `.link` 檔指向的目錄還存在。
- **`kt update my-pack` 說 "skipped"。** Editable 和非 git 的套件不能透過 `kt update` 更新。Editable 直接改原始目錄，複製安裝就重裝。
- **`python_dependencies` 沒裝起來。** 確認 `kt install` 在目前環境有安裝套件的權限 (用 virtualenv 或 `pip install --user`)。
- **套件工具被內建工具遮蔽。** 內建工具優先解析。想讓你的版本勝出，就改名。

## 另見

- [撰寫生物](creatures.md)：把生物打包。
- [自訂模組](custom-modules.md)：撰寫要出貨的工具 / 外掛。
- [參考 / CLI](../reference/cli.md)：`kt install`、`kt list`、`kt extension`。
- [參考 / Python API](../reference/python.md#packages)：`kohakuterrarium.packages` 門面。
- [`kt-biome`](https://github.com/Kohaku-Lab/kt-biome)：參考套件。
