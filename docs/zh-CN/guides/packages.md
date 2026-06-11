---
title: 包
summary: 用 kt install 安装包、kohaku.yaml 清单、@pkg/ 引用，以及发布你自己的包。
tags:
  - guides
  - package
  - distribution
---

# 包

写给想在项目之间共享生物 (creature)、terrarium、工具或插件的读者。

KohakuTerrarium 的包就是一个带 `kohaku.yaml` 清单的目录。它可以包含
生物、terrarium、自定义工具、插件、触发器、I/O 模块、过程性技能、
控制器命令、用户斜杠命令、提示词片段、framework-hint 覆盖和 LLM
preset。`kt install` 把它放到 `~/.kohakuterrarium/packages/<name>/`，
之后用 `@<name>/path` 语法引用包里的任何东西。

概念入门：[边界](../concepts/boundaries.md)。包是框架把“共享可复用
组件”这件事做便宜的方式。

## 官方包：`kt-biome`

大多数人安装的第一个包是 `kt-biome`，一个示范包，内含 `swe`、
`reviewer`、`researcher`、`ops`、`creative`、`general`、`root` 等生物，
`swe_team`、`deep_research` 等 terrarium，外加若干插件。

```bash
kt install @kt-biome
kt run @kt-biome/creatures/swe
```

`@kt-biome` 这个短写法经市场解析（见下文）；想绕开市场的话，
`kt install https://github.com/Kohaku-Lab/kt-biome.git` 照样可用。

自己造包的时候，把 `kt-biome` 当参考来研究。

## 清单：`kohaku.yaml`

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

目录布局：

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

Python 模块按点路径解析（`my_pack.tools.my_tool:MyTool`）。配置经
`@my-pack/creatures/researcher` 解析。

`python_dependencies`（加上 `requirements.txt`，如果有）由
`kt install` 通过 `sys.executable -m pip` 安装，也就是装进
KohakuTerrarium 自己运行的环境。传 `--no-deps` 可以跳过；依赖安装
失败会抛 `PackageError`，而不是降级成一条警告。

### 较新的清单槽位

除了 `tools`、`plugins` 和 `llm_presets`，包现在还可以提供：

- `io:`：经包解析的输入/输出模块类
- `triggers:`：经包解析的触发器类
- `skills:`：可被生物发现的过程性技能包（`SKILL.md`）
- `commands:`：控制器的 `##name##` 命令
- `user_commands:`：人可以敲的斜杠命令
- `prompts:` / `templates:`：可复用的 Jinja include 提示词片段
- `framework_hints:`：包级别的内置 framework-hint 文案覆盖

冲突策略是刻意分两套的：

- 工具/插件/io/触发器/用户命令/控制器命令共用一个名字命名空间，
  冲突按错误处理或要求显式覆盖；
- 过程性技能是例外：后到者赢，作用域更窄的
  （项目/用户/生物）覆盖包里自带的副本。

## 安装方式

### 市场 spec（`@name`）

```bash
kt install @kt-biome              # newest non-yanked version
kt install @kt-biome@v1.2.0       # explicit version pin
kt install @myfork/kt-biome       # name restricted to a specific source
```

`@` 前缀形式经市场（[见下文](#市场与-name-解析)）解析成 git URL，
然后像 `kt install <git-url>` 一样克隆到
`~/.kohakuterrarium/packages/<name>/`。**`@` spec 不支持可编辑模式**：
先克隆，再用 `-e` 安装。

### Git URL（克隆）

```bash
kt install https://github.com/you/my-pack.git
```

克隆到 `~/.kohakuterrarium/packages/my-pack/`。用 `kt update my-pack` 更新。

### 本地路径（复制）

```bash
kt install ./my-pack
```

把文件夹复制进去。更新时重跑 `kt install`，或直接改副本。

### 本地路径（可编辑）

```bash
kt install ./my-pack -e
```

写一个指向源目录的 `~/.kohakuterrarium/packages/my-pack.link`。源里的
修改立即可见，不需要重装。开发迭代时非常好用。

### 卸载

```bash
kt uninstall my-pack
```

## 解析 `@pkg/path`

`@my-pack/creatures/researcher` →

- 若存在 `my-pack.link`：跟随指针。
- 否则：`~/.kohakuterrarium/packages/my-pack/creatures/researcher/`。

`@pkg/...` 引用在配置加载的咽喉处解析，所以所有消费者都统一接受
它们：`kt run`、`kt edit`、`kt update`、`base_config:` 继承、配方，
以及编程式加载器：`Agent.build(...)`、`engine.add_creature(...)`、
`Terrarium.from_recipe(...)`、`compose.agent(...)`、
`Studio.sessions.start_creature(...)`。引用未安装的包抛
`kt.errors.PackageNotInstalledError`（会点名包名并提示 `kt install`）；
格式错误的引用（裸 `@`、越出包根的路径穿越）抛
`kt.errors.PackageRefError`。

## 编程 API：`kohakuterrarium.packages`

`kt install` / `kt list` 能做的事都可以从
`kohakuterrarium.packages` 导入。这是个惰性门面，导入它不会拖进
市场 / 安装器那一摞东西，碰到那些名字时才加载。

```python
from kohakuterrarium import packages

# Idempotent install: the right call at the top of a batch script.
# Returns the package name; if a package with that name is already
# installed it returns immediately (no version check, even for pins).
packages.ensure("@kt-biome")

# Explicit installs (marketplace spec / git URL / local dir):
packages.install_package_spec("@kt-biome@v1.2.0")
packages.install_package("https://github.com/you/my-pack.git")
packages.install_package("./my-pack", editable=True)

packages.update_package("my-pack")        # git pull --ff-only; refuses pins
packages.uninstall_package("my-pack")

# Resolution and enumeration:
path = packages.resolve_package_path("@kt-biome/creatures/swe")
packages.is_package_ref("@kt-biome/creatures/swe")   # True
packages.packages_dir()                   # honours KT_CONFIG_DIR
for pkg in packages.list_packages():
    print(pkg["name"], pkg["version"])
```

依赖策略：安装函数接受 `deps="auto" | "never"`
（默认 `"auto"`，运行 `sys.executable -m pip`；`"never"` 跳过 Python
依赖，相当于 `--no-deps`）。失败抛 `PackageError` 家族的类型化错误
（`PackageRefError`、`PackageNotInstalledError`、
`PackagePathNotFoundError`），门面里有重导出，用起来方便。

`packages.ensure(spec)` 只保证*存在*，不保证版本：要把特定版本压到
已有安装上，调用 `install_package_spec("@pkg@vX.Y.Z")`。

完整符号列表（清单槽位解析器、包根查询）见
[Python API 参考](../reference/python.md#包)。

## 市场与 `@name` 解析

[TerrariumMarket](https://github.com/Kohaku-Lab/TerrariumMarket) 是
KohakuTerrarium 包的公共市场。它就是一个公开的 GitHub 仓库，内含一个
YAML 文件（`registry.yaml`）和每个包一个的条目目录。`kt install
@<name>` 读这个文件把名字解析成 git URL，然后照常安装。

框架抓取索引并缓存在 `~/.kohakuterrarium/marketplace/cache.json`，
TTL 一小时（用 ETag 对上游再验证）。冷缓存 + 离线 = 明确报错。
热缓存 + 离线 = 静默回退到缓存数据，并记一条警告日志。

### CLI 命令

```bash
kt marketplace            # alias for `list`: show configured sources
kt marketplace list
kt marketplace refresh    # force cache bust + re-fetch
kt marketplace search [query] [--tag <t>] [--author <a>] [--json]
kt marketplace info @<name>

kt marketplace add <url> [--alias <name>]   # add a custom source
kt marketplace remove <url-or-alias>
kt marketplace reset                         # restore the default-only source list
```

### Spec 语法

| 形式 | 解析为 |
|---|---|
| `@kt-biome` | 第一个列出它的源里最新的未撤回 (non-yanked) 版本 |
| `@kt-biome@v1.2.0` | 精确版本锁定（为了可复现，允许已撤回的版本） |
| `@myfork/kt-biome` | 限定从别名为 `myfork` 的源解析 `kt-biome` |

### 配置源

默认源列表只有 TerrariumMarket。要加一个 fork 或你自己的服务器：

```bash
kt marketplace add https://raw.githubusercontent.com/<owner>/<repo>/main/registry.yaml --alias myfork
```

源按查找顺序合并；同名包第一次出现者胜（遮蔽会记日志）。设置持久化
在 `~/.kohakuterrarium/marketplace-sources.json`。

环境变量覆盖（一次性，不写设置文件）：

```bash
KT_MARKETPLACE_SOURCES=https://a.test/r.yaml,https://b.test/r.yaml kt marketplace search
KT_MARKETPLACE_CACHE_TTL=0 kt marketplace search   # bypass cache for this call
```

### 从应用里浏览

桌面 / Web 应用的 **Settings → Extensions** 标签现在是双栏的
“Catalog”视图：**Browse**（市场里的包，带 Install 按钮）和
**Installed**（你的本地集合，带 Uninstall + “Update available”
标记）。背后跑的是同一套 `@<name>` 安装流程，所以 CLI 里的
`kt install @kt-biome` 和应用里点 Install 走的是同一条代码路径。

## 发现类命令

```bash
kt list                         # installed packages + local agents
kt info path/or/@pkg/creature   # details of one config
kt extension list               # all tools/plugins/presets from all packages
kt extension info my-pack       # package metadata + what it ships
kt marketplace                  # configured marketplace sources
kt marketplace search           # browse the marketplace (all packages)
kt marketplace search biome     # substring + tag filter
kt marketplace info @kt-biome   # detail view for a marketplace entry
```

`kt extension list` 是查看本地装了什么最省事的方式；
`kt marketplace search` 则是查看有什么可装的对应物。

## 编辑已安装的配置

```bash
kt edit @my-pack/creatures/researcher
```

用 `$EDITOR` 打开 `config.yaml`（回退到 `$VISUAL`，再到 `nano`）。
可编辑安装改的是源；普通安装改的是
`~/.kohakuterrarium/packages/` 下的副本。

## 发布

1. 把仓库推到 git（GitHub、GitLab、自建，`git clone` 能处理的都行）。
2. 打版本标签：`git tag v0.1.0 && git push --tags`。
3. 每次发版同步更新 `kohaku.yaml` 里的 `version:`。
4. **可选但推荐**：把包列到 TerrariumMarket，用户就能
   `kt install @your-package`。向
   [Kohaku-Lab/TerrariumMarket](https://github.com/Kohaku-Lab/TerrariumMarket)
   提 PR，添加 `entries/<your-package>/entry.yaml` +
   `entries/<your-package>/README.md`；CI 验证 schema + 标签存在性；
   维护者合并。流程详见[贡献指南](https://github.com/Kohaku-Lab/TerrariumMarket/blob/main/CONTRIBUTING.md)。
5. 不上市场的话，直接分享 URL：`kt install https://your/repo.git`。

上 TerrariumMarket **不是必须的**：包终究只是带 `kohaku.yaml` 的
git 仓库，直接 URL 安装路径没有变。市场是叠在其上的发现层，不是
替代品。

### 版本管理

让 `kohaku.yaml` 的 `version:` 和 git 标签保持同步。`kt update` 底层
是 `git pull`；锁定到标签的使用者可以手动 checkout：

```bash
cd ~/.kohakuterrarium/packages/my-pack
git checkout v0.1.0
```

## 运行时的扩展发现

框架加载生物时，加载器先在生物自己的本地配置里查工具/插件名，再查
已安装包的清单。包声明的工具在配置里通过 `type: package` 引出：

```yaml
tools:
  - name: my_tool
    type: package          # resolved through the `tools:` list in kohaku.yaml
```

包声明的 I/O 和触发器现在也是同一套写法：

```yaml
input:
  type: package
  name: discord_input

triggers:
  - type: package
    name: webhook
```

提示词片段从 Jinja include 解析：

```md
{% include "git-safety" %}
```

控制器/用户命令则从包清单里发现，而不是生物文件夹。

这样一个包里的生物就能引用另一个包声明的扩展，只要两个包都装了。

## 故障排查

- **`@my-pack/...` 解析失败。**先 `kt list` 确认包装了。可编辑安装
  要检查 `.link` 文件指向的目录还在。
- **`kt update my-pack` 显示 “skipped”。**可编辑安装和非 git 包没法
  用 `kt update` 更新。改源（可编辑）或重装（复制）。
- **`python_dependencies` 没装上。**确认 `kt install` 在当前环境有
  安装包的权限（用 virtualenv 或 `pip install --user`）。
- **包工具被内置工具遮住了。**内置工具优先解析。想让你的赢，
  给包工具改名。

## 另请参阅

- [生物](creatures.md)：打包一个生物。
- [自定义模块](custom-modules.md)：编写要发布的工具/插件。
- [参考 / CLI](../reference/cli.md)：`kt install`、`kt list`、`kt extension`。
- [参考 / Python API](../reference/python.md#包)：`kohakuterrarium.packages` 门面。
- [`kt-biome`](https://github.com/Kohaku-Lab/kt-biome)：参考包。
