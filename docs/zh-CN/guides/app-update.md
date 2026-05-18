---
title: 应用更新
summary: KohakuTerrarium 桌面应用的更新机制 —— 下载预构建的发布 tarball、版本并列安装、原子指针切换、自定义镜像、发布通道。
tags:
  - guides
  - update
  - briefcase
  - desktop
---

# 应用更新

KohakuTerrarium 桌面应用通过**下载与你的平台 + Python ABI 匹配的预构建
发布 tarball**来自我更新：在本地解压到当前版本的并列目录、做冒烟测试，
然后原子性地切换一个小指针文件来决定下次启动哪一份。这个模型借鉴自
Squirrel / Velopack / Sparkle 这类原生应用更新器 —— 小、事务化，
每次更新只需要一次 HTTPS GET + 一次解压。

更关键的是：你的机器**不需要**运行 `pip`、`venv`、`git` 或 `ensurepip`。
这些只存在于构建机上；你拿到的就是构建好的结果。

## 心智模型

```
┌──────────────────────────────────────────────────────────────┐
│  Briefcase 桌面包                                            │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  Launcher (~50KB Python，urllib + hashlib + tarfile)   │  │
│  │  - 读取 runtime/active                                 │  │
│  │  - 下载 + 解压发布 tarball                             │  │
│  │  - exec 进入 versions/<active>/scripts/kt              │  │
│  └────────────────────────────────────────────────────────┘  │
│  + bundled-release/kohakuterrarium-<v>-<plat>-py<X.Y>.tar.zst│  ← 离线首次启动
└──────────────────────────────────────────────────────────────┘

用户家目录 (~/.kohakuterrarium/):
├── app-settings.json
└── runtime/
    ├── active                      ← 指针 JSON
    ├── versions/
    │   ├── 1.5.0/                  ← 解压好的发布目录
    │   │   ├── site-packages/
    │   │   ├── scripts/kt
    │   │   └── manifest.json
    │   ├── 1.5.1/
    │   └── 1.5.2-nightly-2026-05-19/
    └── manifest-cache/
        ├── stable.json
        ├── beta.json
        └── nightly.json
```

每个版本都在自己独立的目录。切换版本只是改写 50 字节的 `active` 指针
（在 POSIX 和 Windows 上都是原子的）。当前正在运行的进程不受影响 ——
新版本在下次启动时生效。

## 首次启动

如果安装包带了 `bundled-release/<tarball>`，首次启动会把这个离线 tarball
解压到 `versions/<v>/` 并指向它。之后联网时你可以从 GUI 或 CLI 更新到
更新的版本。

没有 bundled tarball（开发者安装 / 最小化包）的情况下，首次启动会从
配置的频道清单解析、下载、校验、解压匹配的工件，然后写指针。如果连网
也没有，启动器会显示「首次启动需要网络」错误，而不是悄悄变砖。

## 更新

| 入口 | 怎么用 |
|---|---|
| 桌面应用 Admin → Updates 标签 | 设置 + 「立即检查」+「更新」按钮 |
| `kt self-update` | CLI 对应入口 |
| `kt self-update --check-only` | 解析并打印最新版；有更新返回 0，已是最新返回 1 |
| `kt self-update --dry-run` | 解析并打印将要安装的内容 |
| `kt self-update --rollback` | 指针回退到上一个已安装版本 |

更新不会修改当前运行的进程。更新成功后，退出并重新启动应用 —— 新指针
会在下次启动时被读取。

## 发布通道

| 通道 | 内容 | 何时选 |
|---|---|---|
| `stable` | 经过测试的发布 | 默认、推荐 |
| `beta` | 预发布候选 | 帮我们验证下一个大版本 |
| `nightly` | 每日自动构建 | 追前沿 / 贡献者 |

通道选择在 **Admin → Updates → Channel** 以及
`kt self-update --channel <name>`（粘性 —— 会写回设置）。

## 版本固定

固定到某个版本，忽略通道更新直到取消固定。适用于：

- 新版本破坏了你依赖的行为，你想留时间报告 + 等修复。
- 受管部署在多节点上统一一个已知版本。

`kt self-update --pin 1.5.0` 设置；`--pin ""` 清除。GUI 提供下拉框，
内容来自通道清单。

## 自定义 feed（镜像 / 离线服务器）

公司 / 内网用户可以把发布 tarball 托管到自己的 HTTPS 服务器。启动器需要：

- `<channel>.json` 清单放在 `<your-base-url>/<channel>.json`。
- 清单里的 tarball URL 指向你放置工件的位置（你的镜像、内网、对象存储…）。

通过 **Admin → Updates → Release feed → Custom mirror** 粘贴 base URL，
或用 CLI 切换：

```
kt self-update --feed-url https://internal.mirror/kt --channel stable
```

启动器会原样拉取 `<base>/<channel>.json`，然后下载并校验
`(platform, py_abi)` 匹配的工件。自定义 feed 支持与默认 GitHub Releases
feed 完全相同的通道 + 固定语义。

### 通道清单结构

```json
{
  "schema": 1,
  "channel": "stable",
  "generated_at": "2026-05-19T00:00:00Z",
  "releases": [
    {
      "version": "1.5.1",
      "build_id": "20260519-153000-abc1234",
      "release_notes_url": "https://your.mirror/notes/1.5.1.md",
      "artifacts": [
        {
          "platform": "linux-x64",
          "py_abi": "cp313",
          "url": "https://your.mirror/dl/kohakuterrarium-1.5.1-linux-x64-py3.13.tar.zst",
          "sha256": "9f86d0...",
          "size_bytes": 178234567
        }
      ]
    }
  ]
}
```

平台标签：`linux-x64`、`linux-arm64`、`macos-x64`、`macos-arm64`、
`win-x64`。ABI 标签：`cp311`、`cp312`、`cp313`、`cp314`。

## 更新模式

| 模式 | 启动时行为 |
|---|---|
| `manual` | 不检查；你从 UI 显式更新 |
| `notify-on-launch` | 每天检查；有新版本就弹横幅 |
| `auto-on-launch` | 在 exec 框架前先检查并安装 |

在 **Admin → Updates → Update mode** 设置。

## 回滚

并列安装在磁盘上保留旧版本。回滚就是把指针改写回最近的非活动版本。
不需要重新下载。

```
kt self-update --rollback
```

或者在 Admin → Updates 标签点 **Rollback to <prev>**。GC 保留 active
+ 上一个 + `update.keep-versions` 个最近版本（默认 3 个，所以磁盘上
最多 5 个版本）。

## 设置文件

`~/.kohakuterrarium/app-settings.json`：

```json
{
  "feed": {
    "kind": "github_releases",
    "repo": "Kohaku-Lab/KohakuTerrarium",
    "url": null
  },
  "channel": "stable",
  "pinned_version": null,
  "update": {
    "mode": "notify-on-launch",
    "check-cache-hours": 24,
    "keep-versions": 3
  },
  "runtime": {
    "active-version": "1.5.1",
    "active-build-id": "20260519-153000-abc1234",
    "last-check-at": "2026-05-19T12:34:56Z",
    "last-check-error": null
  }
}
```

手动编辑没问题；无效字段会回退到默认值并打一行警告，而不是让启动器卡住。

`--reset-settings` 用默认值覆盖；`--reset-runtime` 清空
`runtime/versions/` 并重新首次安装。

## 启动器**不**依赖的

- `pip` —— 没打包、也不调用
- `venv` / `ensurepip` —— 不使用（Windows 上 briefcase 壳层会剥掉这些，
  这就是之前的设计走不通的原因）
- `git` —— 不调用
- PyPI —— 只查询配置的 feed（github_releases 或 custom）
- 任何第三方 HTTP 客户端 —— 只用 `urllib`

唯一可选的第三方依赖是 `zstandard`（用于 `.tar.zst`）。`.tar.gz` 是
fallback 路径；如果你的镜像服务 `.tar.gz` 工件，没有 `zstandard` 一切
照常工作。

## 开发者提示

如果你从 git 检出运行框架（在你自己的 Python 里 `pip install -e .`），
启动器与你无关。`kt self-update` 会发现你不在启动器安装里，并以
「用 git pull」的一行提示拒绝，而不是试图管理你的开发环境。

## 失败恢复

| 失败 | 表现 |
|---|---|
| tarball sha256 不匹配 | 删 partial，报「下载损坏」 |
| 新版本冒烟测试失败 | 删 partial，活动版本不动 |
| 解压中盘满 | 删 partial |
| 指针文件损坏 | 启动器扫描 `versions/` 自动恢复到最新的有效版本 |
| 清单 URL 5xx | 用 <24h 内的缓存清单；否则报错 |
| `versions/` 和 bundled-release 都缺 | 「需要网络」错误，不静默变砖 |

## 参见

- [配置参考](../reference/configuration.md) —— 所有设置字段
- [CLI 参考](../reference/cli.md) —— 全部 `kt self-update` 选项
