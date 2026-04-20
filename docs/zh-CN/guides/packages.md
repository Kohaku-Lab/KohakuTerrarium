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
kt install https://github.com/Kohaku-Lab/kt-biome.git
kt run @kt-biome/creatures/swe
```

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
  my_pack/                     # 可安装的 python package
    __init__.py
    tools/my_tool.py
    plugins/my_guard.py
```

Python 模块会用点分路径解析（`my_pack.tools.my_tool:MyTool`）。设置则通过 `@my-pack/creatures/researcher` 解析。

如果宣告了 `python_dependencies`，`kt install` 安装时也会一并安装这些 Python 依赖。

## 安装模式

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

## 探索指令

```bash
kt list                         # 已安装 package + 本地 agents
kt info path/or/@pkg/creature   # 查看单一设置的细节
kt extension list               # 所有 package 提供的 tools/plugins/presets
kt extension info my-pack       # package 元数据 + 内容清单
```

`kt extension list` 是最快看出你目前安装环境里有哪些扩展可用的方法。

## 编辑已安装设置

```bash
kt edit @my-pack/creatures/researcher
```

会用 `$EDITOR` 开启 `config.yaml`（没有的话退回 `$VISUAL`，再退回 `nano`）。如果是 editable install，编到的是原始码；如果是一般安装，编到的是 `~/.kohakuterrarium/packages/` 下面那份复本。

## 发布

1. 把 repo push 到 git（GitHub、GitLab、自架都可以——只要 `git clone` 能处理）。
2. 打版本 tag：`git tag v0.1.0 && git push --tags`。
3. 每次发版时同步更新 `kohaku.yaml` 里的 `version:`。
4. 分享 URL：`kt install https://your/repo.git`。

没有中央注册表。Package 本质上就是带有 `kohaku.yaml` 的 git repo。

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
