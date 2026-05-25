---
title: 包
summary: 通过 kt install 安装 pack、理解 kohaku.yaml manifest、@pkg/ 参照，以及发布你自己的 pack。
tags:
 - guides
 - package
 - distribution
---

# 包

给想在专案之间共享Creature、Terrarium、工具或插件的读者。

KohakuTerrarium 的 package，就是一个带有 `kohaku.yaml` manifest 的目录。它可以包含 creatures、terrariums、自定义工具、plugins 与 LLM presets。`kt install` 会把它安装到 `~/.kohakuterrarium/packages/<name>/`，而 `@<name>/path` 语法则可以参照其中任何内容。

概念先读：[边界](../concepts/boundaries.md) —— package 是框架用来让「共享可复用零件」变得廉价的机制。

## 官方 pack：`kt-biome`

多数人第一个会安装的 package 是 `kt-biome`——这是展示型 pack，里面有 `swe`、`reviewer`、`researcher`、`ops`、`creative`、`general`、`root` Creature，也有像 `swe_team` 与 `deep_research` 这些Terrarium，外加一些插件。

```bash
kt install @kt-biome
kt run @kt-biome/creatures/swe
```

`@kt-biome` 短形式会通过 marketplace 解析（详见下文）；如果偏好绕过 marketplace，`kt install https://github.com/Kohaku-Lab/kt-biome.git` 仍然有效。

当你要做自己的 pack 时，把 `kt-biome` 当成参考范本来看。

## Manifest：`kohaku.yaml`

```yaml
name: my-pack
version: "0.1.0"
description: "My shared agent components"

creatures:
  - name: researcher           # 对应 creatures/researcher/ 目录

terrariums:
  - name: research_team        # 对应 terrariums/research_team/ 目录

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

目录结构：

```
my-pack/
  kohaku.yaml
  creatures/researcher/config.yaml
  terrariums/research_team/config.yaml
  prompts/git-safety.md
  skills/repo-surgery/SKILL.md
  my_pack/                     # 可安装的 python package
    __init__.py
    tools/my_tool.py
    plugins/my_guard.py
    io/discord.py
    triggers/webhook.py
    commands/handoff.py
    user_commands/deploy.py
```

Python 模块会用点分路径解析（`my_pack.tools.my_tool:MyTool`）。设置则通过 `@my-pack/creatures/researcher` 解析。

如果宣告了 `python_dependencies`，`kt install` 安装时也会一并安装这些 Python 依赖。

### 更多 manifest 槽位

除了 `tools`、`plugins`、`llm_presets`，package 还可以贡献：

- `io:` — package 解析的输入 / 输出模块类
- `triggers:` — package 解析的 trigger 类
- `skills:` — creature 可发现的程序化技能包（`SKILL.md`）
- `commands:` — Controller `##name##` 指令
- `user_commands:` — 用户可输入的斜杠命令
- `prompts:` / `templates:` — Prompt 的可复用 Jinja include 片段
- `framework_hints:` — 对内建 framework-hint 文案的 package 级覆写

冲突策略是混合的：

- tools / plugins / io / triggers / user_commands / commands 共享同一个名称
  空间，冲突时报错或要求显式覆写；
- 程序化技能（skills）相反 —— last-wins，且较窄的作用域（project / user /
  creature）会覆写 package 自带的副本。

## 安装模式

### Marketplace 短写（`@name`）

```bash
kt install @kt-biome              # 解析到最新的 non-yanked 版本
kt install @kt-biome@v1.2.0       # 指定版本
kt install @myfork/kt-biome       # 限定来源
```

`@`-前缀形式会通过 marketplace（[详见下文](#marketplace--name-解析)）解析为 git URL，然后跟 `kt install <git-url>` 一样 clone 到 `~/.kohakuterrarium/packages/<name>/`。**`@` 形式不支持 editable 安装** —— 请先 clone，再用 `-e` 安装。

### Git URL（clone）

```bash
kt install https://github.com/you/my-pack.git
```

会 clone 到 `~/.kohakuterrarium/packages/my-pack/`。更新则用 `kt update my-pack`。

### 本地路径（copy）

```bash
kt install ./my-pack
```

会把整个目录复制进去。更新方式是重新执行 `kt install`，或直接修改那份复本。

### 本地路径（editable）

```bash
kt install ./my-pack -e
```

会写入 `~/.kohakuterrarium/packages/my-pack.link`，指向原始码目录。之后你在原始码的修改会立即生效——不需要重新安装。很适合开发时迭代。

### 解除安装

```bash
kt uninstall my-pack
```

## 解析 `@pkg/path`

`@my-pack/creatures/researcher` →

- 如果存在 `my-pack.link`：追踪这个指标。
- 否则：解析到 `~/.kohakuterrarium/packages/my-pack/creatures/researcher/`。

这套机制会被 `kt run`、`kt terrarium run`、`kt edit`、`kt update`、`base_config:` 继承，以及程序化的 `Agent.from_path(...)` 使用。

## Marketplace 与 `@name` 解析

[TerrariumMarket](https://github.com/Kohaku-Lab/TerrariumMarket) 是 KohakuTerrarium package 的公共 marketplace。它是一个公开 GitHub repo，里面有一个 `registry.yaml` 与每个 package 一个目录。`kt install @<name>` 读这个文件、解析出 git URL，然后照常安装。

框架会把索引缓存在 `~/.kohakuterrarium/marketplace/cache.json`，TTL 一小时（ETag 条件式重新验证）。冷缓存离线 = 明确错误。暖缓存离线 = 静默落回缓存数据 + 警告日志。

### CLI 指令

```bash
kt marketplace            # 等同 `list`：显示配置的来源
kt marketplace list
kt marketplace refresh    # 强制 bust 缓存 + 重抓
kt marketplace search [query] [--tag <t>] [--author <a>] [--json]
kt marketplace info @<name>

kt marketplace add <url> [--alias <name>]   # 添加自定义来源
kt marketplace remove <url-or-alias>
kt marketplace reset                         # 恢复默认（只剩 TerrariumMarket）
```

### Spec 语法

| 形式 | 解析到 |
|---|---|
| `@kt-biome` | `kt-biome` 在第一个有的来源中、最新的 non-yanked 版本 |
| `@kt-biome@v1.2.0` | 指定版本（即使 yanked 也允许，便于可复现） |
| `@myfork/kt-biome` | 限制 `kt-biome` 只从别名 `myfork` 的来源查找 |

### 配置来源

默认来源只有 TerrariumMarket。要加 fork 或自架：

```bash
kt marketplace add https://raw.githubusercontent.com/<owner>/<repo>/main/registry.yaml --alias myfork
```

来源按配置顺序合并；同名 package 以最先出现的为准（影子化会记 log）。设置保存在 `~/.kohakuterrarium/marketplace-sources.json`。

环境变量一次性覆盖（不写设置档）：

```bash
KT_MARKETPLACE_SOURCES=https://a.test/r.yaml,https://b.test/r.yaml kt marketplace search
KT_MARKETPLACE_CACHE_TTL=0 kt marketplace search   # 这次跳过缓存
```

### 在 app 里浏览

桌面 / 网页 app 的 **Settings → Extensions** tab 现在是两栏的 Catalog 视图：**Browse**（marketplace package + Install 按钮）与 **Installed**（本地已装的，可 Uninstall、有 "Update available" 提示）。同样走 `@<name>` 的安装流程，所以 CLI 的 `kt install @kt-biome` 与 app 里按 Install 走的是同一条代码路径。

## 探索指令

```bash
kt list                         # 已安装 package + 本地 agents
kt info path/or/@pkg/creature   # 查看单一设置的细节
kt extension list               # 所有 package 提供的 tools/plugins/presets
kt extension info my-pack       # package 元数据 + 内容清单
kt marketplace                  # 配置的 marketplace 来源
kt marketplace search           # 浏览 marketplace（全部）
kt marketplace search biome     # 子串 / tag 过滤
kt marketplace info @kt-biome   # marketplace 条目详细信息
```

`kt extension list` 看本地装了什么；`kt marketplace search` 看可以装什么。

## 编辑已安装设置

```bash
kt edit @my-pack/creatures/researcher
```

会用 `$EDITOR` 开启 `config.yaml`（没有的话退回 `$VISUAL`，再退回 `nano`）。如果是 editable install，编到的是原始码；如果是一般安装，编到的是 `~/.kohakuterrarium/packages/` 下面那份复本。

## 发布

1. 把 repo push 到 git（GitHub、GitLab、自架都可以——只要 `git clone` 能处理）。
2. 打版本 tag：`git tag v0.1.0 && git push --tags`。
3. 每次发版时同步更新 `kohaku.yaml` 里的 `version:`。
4. **建议**：到 TerrariumMarket 登记 package，让用户可以用 `kt install @your-package`。开 PR 加 `entries/<your-package>/entry.yaml` 与 `entries/<your-package>/README.md` 到 [Kohaku-Lab/TerrariumMarket](https://github.com/Kohaku-Lab/TerrariumMarket)；CI 会验证 schema 与 tag 存在性；维护者审核后合并。详见 [contributing guide](https://github.com/Kohaku-Lab/TerrariumMarket/blob/main/CONTRIBUTING.md)。
5. 不登记也行，直接分享 URL：`kt install https://your/repo.git`。

登记到 TerrariumMarket **不是必需** —— package 仍然只是带有 `kohaku.yaml` 的 git repo，直接 URL 安装的路径完全没变。Marketplace 只是叠在上面的发现层，不是取代品。

### 版本管理

请让 `version:` 与 git tag 保持一致。`kt update` 底层就是做 `git pull`；如果用户想固定在某个 tag，也可以手动 checkout：

```bash
cd ~/.kohakuterrarium/packages/my-pack
git checkout v0.1.0
```

## 执行时的扩展发现

当框架加载一个Creature时，loader 会先在Creature自己的设置里查工具／插件名称，再查已安装 package 的 manifest。Package 宣告的工具，会通过设置中的 `type: package` 暴露出来：

```yaml
tools:
  - name: my_tool
    type: package          # 通过 kohaku.yaml 里的 `tools:` 清单解析
```

这让某个 package 里的 creature，也能参照另一个 package 宣告的工具，只要两者都已安装即可。

## 疑难排解

- **`@my-pack/...` 无法解析**。 用 `kt list` 确认 package 已安装。若是 editable install，也检查 `.link` 档是否指向存在的目录。
- **`kt update my-pack` 显示 "skipped"**。 Editable 与非 git package 都不能通过 `kt update` 更新。请直接改原始码（editable），或重新安装（copy）。
- **`python_dependencies` 没有安装**。 确认 `kt install` 在目前环境中有安装权限（建议用 virtualenv，或 `pip install --user`）。
- **Package 工具遮蔽了内置工具**。 内置工具会优先解析。若你想让自己的版本生效，请替 package 工具改名。

## 延伸阅读

- [Creatures 指南](creatures.md) — 如何把 creature 打包。
- [自定义模块指南](custom-modules.md) — 编写要随 package 一起发布的工具／插件。
- [参考 / CLI](../reference/cli.md) — `kt install`、`kt list`、`kt extension`。
- [`kt-biome`](https://github.com/Kohaku-Lab/kt-biome) — 参考 package。
