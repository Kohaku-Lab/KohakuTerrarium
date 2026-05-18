---
title: 应用更新
summary: KohakuTerrarium 桌面应用的更新机制 —— 瘦壳层 Briefcase 包、托管 venv，以及来源 / 更新模式设置。
tags:
  - guides
  - update
  - briefcase
  - desktop
---

# 应用更新

KohakuTerrarium 桌面应用是**包裹在托管 Python venv 外的瘦壳层**。壳层很少变更；框架本身通过 `pip` 在你设定的节奏下更新 —— 每次发版无需重新下载安装器。

本指南说明壳层做什么、状态文件在哪里、如何选择框架来源、以及如何更新 / 回滚 / 恢复。

## 心智模型

```
┌──────────────────────────────────────────────────────┐
│  Briefcase 桌面包                                    │
│  ┌────────────────────────────────────────────────┐  │
│  │  Wrapper (kohakuterrarium-launcher)            │  │
│  │  - Python 运行时                               │  │
│  │  - 引导器 (~/.kohakuterrarium/runtime/...)     │  │
│  │  - 启动画面                                    │  │
│  │  - 内嵌备用 wheels                             │  │
│  └────────────────────────────────────────────────┘  │
│                       │                              │
│                       ▼                              │
│  ┌────────────────────────────────────────────────┐  │
│  │  托管 venv（首次启动时创建）                  │  │
│  │  ~/.kohakuterrarium/runtime/venv/              │  │
│  │  └── kohakuterrarium == <你选择的来源>          │  │
│  └────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────┘
```

双击应用图标时：

1. 壳层读取 `~/.kohakuterrarium/app-settings.json`。
2. 若 `~/.kohakuterrarium/runtime/venv/` 不存在，启动画面打开，壳层按配置的来源安装框架。
3. 若 venv 已存在，壳层调用 `exec` 把自己替换为 venv 里的 `kt` 入口 —— 此刻起你直接运行框架，壳层退出。

## 路径

| 路径 | 用途 |
|---|---|
| `~/.kohakuterrarium/app-settings.json` | 来源 + 更新模式设置（**设置 → 更新** 标签读写此文件） |
| `~/.kohakuterrarium/runtime/venv/` | 活动的托管 venv |
| `~/.kohakuterrarium/runtime/venv.old/` | 上一次成功更新后保留的旧 venv，可单次回滚 |
| `~/.kohakuterrarium/runtime/.update.lock` | flock 文件锁，避免两个应用实例同时跑 `pip install` |
| `~/.kohakuterrarium/logs/launcher.log` | 壳层滚动日志（1 MB × 3） |

## 选择来源

壳层支持四种来源类型。在 **设置 → 更新 → 来源** 中选择：

| 来源 | 执行的 pip 命令 | 适用场景 |
|---|---|---|
| **PyPI stable** | `pip install -U kohakuterrarium` | 默认。最新正式版。 |
| **PyPI 版本固定** | `pip install -U kohakuterrarium==1.5.0`（或 `<2.0` 等） | 临时锁定版本以测试或集群发布前。 |
| **Git 引用** | `pip install -U "git+<url>@main"`（分支 / 标签 / commit） | 跟随开发分支、自己的 fork、或未发布到 PyPI 的 RC。 |
| **本地可编辑路径** | `pip install -e /path/to/checkout` | 从 Git 仓库本地开发。**禁用自动更新** —— 由你的 `git pull` 驱动。 |
| **内嵌（离线）** | `pip install --no-index --find-links=wheels-bundle/ kohakuterrarium` | 离线机器首次启动，或远程来源不可达时恢复使用。 |

## 更新模式

| 模式 | 壳层启动时的行为 |
|---|---|
| **手动** | 永不检查。点击标签里的「立即检查」/「更新」由你触发。 |
| **启动时通知** *（默认）* | 启动后台异步检查 PyPI / git（缓存 24 小时）。若有新版本，**设置 → 更新** 显示横幅。点「更新」安装。 |
| **启动时自动** | 启动时检查 **并** 安装更新（启动画面显示进度）。可取消；取消则回到现有 venv。 |

`source.kind=local` 会强制覆盖任何模式为 **手动** —— 可编辑安装是用户自己管的。

## 更新流程细节

点击 **更新**（或壳层触发自动更新）时：

1. **Flock** `~/.kohakuterrarium/runtime/.update.lock`，避免两次启动同时跑。
2. 在 `~/.kohakuterrarium/runtime/venv.new/` 新建独立 venv。
3. 按来源跑 `pip install`。
4. **冒烟测试**：import 框架、执行 `kt --help`。两者必须在 30 秒内成功。
5. 通过后，**原子地** 把 `venv` 重命名为 `venv.old`，再把 `venv.new` 重命名为 `venv`（内核级 rename，瞬时完成）。
6. 把新版本号 + 检查时间戳写回 `app-settings.json`。
7. 释放 flock。重启应用即可使用新版本。

3-5 中任何一步失败，壳层会删除 `venv.new/`，保留现有 `venv/` 不动。错误显示在进度弹窗里；应用继续运行原版本。

## 回滚

每次更新成功后会把旧 venv 保留在 `~/.kohakuterrarium/runtime/venv.old/`。点 **更新** 标签的 **回滚** 即可交换回去。仅能回滚一次（下一次成功更新会覆盖 `venv.old`）。

## 恢复 —— 两个 venv 都坏了

若 `venv/` 和 `venv.old/` 都丢失或不可用,壳层会回退到 Briefcase 包内附的 **内嵌 wheels**。更新标签会显示「恢复模式」横幅,附带 **从内嵌 wheels 重置 venv** 按钮。即使网络或来源不可达,也能从离线副本重新装好框架。

## 离线首次启动 —— 内嵌优先安装

桌面包(MSI / `.app` / AppImage)**自带一份框架 wheels**,与壳层并排打入安装包内。首次启动时壳层会从这些内嵌 wheels 安装,而不是去访问 PyPI —— 哪怕没网或在防火墙后面,首启也能跑起来。

壳层的判断规则:

| 场景 | 首次安装实际执行的 pip 命令 |
|---|---|
| 默认配置 + 安装包内含内嵌 wheels | `pip install --no-index --find-links=<bundled>/ kohakuterrarium` |
| 用户改过 `source.kind`(例如选了 Git) | 按用户选择执行 —— 跳过内嵌 wheels |
| 内嵌 wheels 缺失(开发安装、坏掉的包) | 按配置来源走 PyPI 等回退 |
| 内嵌安装失败(wheel 坏) + 默认源 | 自动改用 PyPI 恢复 |
| 内嵌安装失败 + 用户配了 Git / 本地 | 直接报错 —— 不掩盖用户的明确意图 |

首次安装完成后,**设置 → 更新** 标签的「Installed」一行会写明 `Installed: 1.5.x (from bundled offline copy)`,一眼就能看出当前 venv 是哪个源装出来的。

### 后续更新默认仍走 PyPI

内嵌优先只对**首次安装**生效。之后的更新(手动、启动时提醒、启动时自动)按 `source.kind` 走,默认是 PyPI。点 **更新** 时壳层照常从 PyPI 拉最新版;内嵌 wheels 不会动,继续作为 C2 安全网保留。

更新按钮的文案也会跟着源变化:

- 源 = PyPI → `Update to <X> from PyPI`
- 源 = Git → `Update from git`
- 源 = 本地 → `Reinstall editable`
- 源 = 内嵌(显式) → `Reinstall from bundled (same version)`

若想**永远停留在内嵌版本**,把 **更新模式** 设成 **手动**,永不点更新 —— 壳层就再不会触网。

## CLI 等价：`kt self-update`

终端也能跑同样的流程：

```bash
kt self-update                  # 按配置来源更新
kt self-update --dry-run        # 打印要执行的动作，不动文件
kt self-update --check-only     # 有新版本退 0，已是最新退 1
kt self-update --source git --spec "https://github.com/.../@main"
```

`kt self-update` 会自动检测 KohakuTerrarium 的安装方式并走对应路径：

- **壳层托管 venv** → 走原子 rename 流程（与 GUI 一致）。
- **pipx** → `pipx upgrade kohakuterrarium`。
- **可编辑安装** → 拒绝；提示去你的 checkout 里 `git pull`。
- **系统包**（`/usr/bin/python`） → 拒绝；提示用平台包管理器。
- **其他用户 venv** → 当前解释器跑 `pip install -U`。原子 rename + 回滚是壳层专属，此处不可用。

## 从旧 Bundle 迁移

KohakuTerrarium 1.5.0 同时发布旧版「冻结全框架」Briefcase 包和新的壳层包。若你用的是旧包，**设置 → 更新** 会显示一次性的「切换到自动更新版本」横幅，链接到发布页。下载壳层安装器**一次**、安装一遍，之后每次更新都是壳层 venv 里的 pip 操作 —— 不再需要下载安装器。

壳层会保留你的 `~/.kohakuterrarium/` 用户数据（会话、配置、MCP 服务器、API key）。所有配置原地不动；只是框架源码被重新装到新的 venv。

## 排查

- **首次启动卡在「Installing framework」** —— 查看 `~/.kohakuterrarium/logs/launcher.log` 找 pip 的输出。通常是网络 / 代理 / 防火墙问题。把标签里的来源切到 **内嵌（离线）**，从备用 wheels 装即可。
- **「Another update is in progress」** —— 之前的更新崩溃了，留下了 lockfile。10 分钟后壳层会提示「覆盖陈旧锁」，确认后重试。
- **冒烟测试在安装后失败** —— 安装完了但 `kt --help` 跑不起来。点 **回滚** 切回 `venv.old/`。若也坏了，点 **从内嵌 wheels 重置 venv** 恢复离线副本。
- **可编辑安装但 `kt self-update` 拒绝** —— 这是有意的。在你的 checkout 里 `git pull`，再跑 `pip install -e .` 刷新已安装的元数据。

## 另请参阅

- [部署 — Docker](deployment-docker.md) —— 容器更新流程改用 `docker pull`。
- [部署 — systemd](deployment-systemd.md) —— systemd 主机上跑 `kt self-update`，再 `systemctl restart kohakuterrarium-host` 让新版本生效。
- [Serving 指南](serving.md) —— 壳层 `exec` 之后框架的 `kt` 入口跑的就是 `kt serve`。
