---
title: 應用更新
summary: KohakuTerrarium 桌面 app 的更新機制 —— 下載預建的發布 tarball、版本並列安裝、原子指針切換、自訂鏡像、發布通道。
tags:
  - guides
  - update
  - briefcase
  - desktop
---

# 應用更新

KohakuTerrarium 桌面 app 透過**下載與你的平台 + Python ABI 相符的預建
發布 tarball**自我更新：在本機解壓到目前版本的並列目錄、做冒煙測試,
再原子性地切換一個小指針檔來決定下次啟動哪一份。這個模型借鏡自
Squirrel / Velopack / Sparkle 這類原生 app 更新器 —— 小、事務化,
每次更新只需要一次 HTTPS GET + 一次解壓。

更關鍵的是:你的機器**不需要**執行 `pip`、`venv`、`git` 或 `ensurepip`。
這些只存在於建置機上;你拿到的就是建置好的成品。

## 心智模型

```
┌──────────────────────────────────────────────────────────────┐
│  Briefcase 桌面包                                            │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  Launcher (~50KB Python，urllib + hashlib + tarfile)   │  │
│  │  - 讀取 runtime/active                                 │  │
│  │  - 下載 + 解壓發布 tarball                             │  │
│  │  - exec 進入 versions/<active>/scripts/kt              │  │
│  └────────────────────────────────────────────────────────┘  │
│  + bundled-release/kohakuterrarium-<v>-<plat>-py<X.Y>.tar.zst│  ← 離線首次啟動
└──────────────────────────────────────────────────────────────┘

使用者家目錄 (~/.kohakuterrarium/):
├── app-settings.json
└── runtime/
    ├── active                      ← 指針 JSON
    ├── versions/
    │   ├── 1.5.0/                  ← 解壓好的發布目錄
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

每個版本都在自己的獨立目錄。切換版本只是改寫 50 byte 的 `active` 指針
(在 POSIX 與 Windows 都是原子的)。目前執行中的行程不受影響 ——
新版本在下次啟動時生效。

## 首次啟動

如果安裝包帶了 `bundled-release/<tarball>`,首次啟動會把這個離線 tarball
解壓到 `versions/<v>/` 並指向它。之後連網時你可以從 GUI 或 CLI 更新到
更新的版本。

沒有 bundled tarball(開發者安裝 / 最小化包)時,首次啟動會從配置的通道
清單解析、下載、驗證、解壓相符的工件,再寫指針。如果連網也沒有,啟動器
會顯示「首次啟動需要網路」錯誤,而不會悄悄變磚。

## 更新

| 入口 | 怎麼用 |
|---|---|
| 桌面 app Admin → Updates 分頁 | 設定 + 「立即檢查」+「更新」按鈕 |
| `kt self-update` | CLI 對應入口 |
| `kt self-update --check-only` | 解析並印出最新版;有更新回傳 0,已是最新回傳 1 |
| `kt self-update --dry-run` | 解析並印出將要安裝的內容 |
| `kt self-update --rollback` | 指針回退到上一個已安裝版本 |

更新不會修改目前執行的行程。更新成功後,離開並重新啟動 app —— 新指針
會在下次啟動時讀取。

## 發布通道

| 通道 | 內容 | 何時選 |
|---|---|---|
| `stable` | 經過測試的發布 | 預設、推薦 |
| `beta` | 預發布候選 | 幫我們驗證下個大版本 |
| `nightly` | 每日自動建置 | 追前緣 / 貢獻者 |

通道選擇在 **Admin → Updates → Channel** 以及
`kt self-update --channel <name>`(黏性 —— 會寫回設定)。

## 版本固定

固定到某個版本,忽略通道更新直到取消固定。適用於:

- 新版本破壞了你依賴的行為,你想留時間回報 + 等修復。
- 受管部署在多節點上統一一個已知版本。

`kt self-update --pin 1.5.0` 設定;`--pin ""` 清除。GUI 提供下拉,
內容來自通道清單。

## 自訂 feed(鏡像 / 離線伺服器)

公司 / 內網使用者可以把發布 tarball 託管到自己的 HTTPS 伺服器。
啟動器需要:

- `<channel>.json` 清單放在 `<your-base-url>/<channel>.json`。
- 清單裡的 tarball URL 指向你放工件的位置(你的鏡像、內網、物件儲存…)。

透過 **Admin → Updates → Release feed → Custom mirror** 貼上 base URL,
或用 CLI 切換:

```
kt self-update --feed-url https://internal.mirror/kt --channel stable
```

啟動器會原樣抓取 `<base>/<channel>.json`,再下載並驗證
`(platform, py_abi)` 相符的工件。自訂 feed 支援與預設 GitHub Releases
feed 完全相同的通道 + 固定語意。

### 通道清單結構

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

平台標籤:`linux-x64`、`linux-arm64`、`macos-x64`、`macos-arm64`、
`win-x64`。ABI 標籤:`cp311`、`cp312`、`cp313`、`cp314`。

## 更新模式

| 模式 | 啟動時行為 |
|---|---|
| `manual` | 不檢查;你從 UI 顯式更新 |
| `notify-on-launch` | 每天檢查;有新版本就跳橫幅 |
| `auto-on-launch` | 在 exec 框架前先檢查並安裝 |

在 **Admin → Updates → Update mode** 設定。

## 回滾

並列安裝在磁碟上保留舊版本。回滾就是把指針改寫回最近的非活動版本。
不需要重新下載。

```
kt self-update --rollback
```

或在 Admin → Updates 分頁點 **Rollback to <prev>**。GC 保留 active
+ 上一個 + `update.keep-versions` 個最近版本(預設 3,因此磁碟上
最多 5 個版本)。

## 設定檔

`~/.kohakuterrarium/app-settings.json`:

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

手動編輯沒問題;無效欄位會退回預設值並印一行警告,而不會卡住啟動器。

`--reset-settings` 用預設值覆寫;`--reset-runtime` 清空
`runtime/versions/` 並重新首次安裝。

## 啟動器**不**依賴的

- `pip` —— 沒打包、也不呼叫
- `venv` / `ensurepip` —— 不使用(Windows 上 briefcase 殼層會剝掉這些,
  這就是之前的設計走不通的原因)
- `git` —— 不呼叫
- PyPI —— 只查詢配置的 feed(github_releases 或 custom)
- 任何第三方 HTTP 客戶端 —— 只用 `urllib`

唯一可選的第三方依賴是 `zstandard`(用於 `.tar.zst`)。`.tar.gz` 是
fallback 路徑;如果你的鏡像供應 `.tar.gz` 工件,沒有 `zstandard` 一切
照常運作。

## 開發者提示

如果你從 git 工作樹執行框架(在你自己的 Python 裡 `pip install -e .`),
啟動器與你無關。`kt self-update` 會發現你不在啟動器安裝裡,並以
「用 git pull」的一行提示拒絕,而不會試圖管理你的開發環境。

## 失敗復原

| 失敗 | 表現 |
|---|---|
| tarball sha256 不相符 | 刪 partial,報「下載損毀」 |
| 新版本冒煙測試失敗 | 刪 partial,活動版本不動 |
| 解壓中盤滿 | 刪 partial |
| 指針檔損毀 | 啟動器掃描 `versions/` 自動復原到最新的有效版本 |
| 清單 URL 5xx | 用 <24h 的快取清單;否則報錯 |
| `versions/` 與 bundled-release 都缺 | 「需要網路」錯誤,不靜默變磚 |

## 參見

- [設定參考](../reference/configuration.md) —— 所有設定欄位
- [CLI 參考](../reference/cli.md) —— 全部 `kt self-update` 旗標
