---
title: 應用更新
summary: KohakuTerrarium 桌面 app 的更新機制 —— 瘦殼層 Briefcase 包、托管 venv，以及來源 / 更新模式設定。
tags:
  - guides
  - update
  - briefcase
  - desktop
---

# 應用更新

KohakuTerrarium 桌面 app 是**包在托管 Python venv 外的瘦殼層**。殼層幾乎不變；框架本身透過 `pip` 在你設定的節奏下更新 —— 每次發版不需要重新下載安裝器。

本指南說明殼層在做什麼、狀態檔在哪、如何選擇框架來源、以及如何更新 / 回滾 / 復原。

## 心智模型

```
┌──────────────────────────────────────────────────────┐
│  Briefcase 桌面包                                    │
│  ┌────────────────────────────────────────────────┐  │
│  │  Wrapper (kohakuterrarium-launcher)            │  │
│  │  - Python runtime                              │  │
│  │  - bootloader (~/.kohakuterrarium/runtime/...) │  │
│  │  - 啟動畫面                                    │  │
│  │  - 內建備用 wheels                             │  │
│  └────────────────────────────────────────────────┘  │
│                       │                              │
│                       ▼                              │
│  ┌────────────────────────────────────────────────┐  │
│  │  托管 venv（第一次啟動時建立）                │  │
│  │  ~/.kohakuterrarium/runtime/venv/              │  │
│  │  └── kohakuterrarium == <你選的來源>           │  │
│  └────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────┘
```

雙擊 app 時：

1. 殼層讀 `~/.kohakuterrarium/app-settings.json`。
2. 若 `~/.kohakuterrarium/runtime/venv/` 不存在，啟動畫面開啟，殼層按設定的來源把框架裝進去。
3. 若 venv 存在，殼層用 `exec` 把自己換成 venv 裡的 `kt` 進入點 —— 從這一刻開始你直接在跑框架，殼層退場。

## 路徑

| 路徑 | 用途 |
|---|---|
| `~/.kohakuterrarium/app-settings.json` | 來源 + 更新模式設定（**設定 → 更新** 分頁讀/寫這個檔） |
| `~/.kohakuterrarium/runtime/venv/` | 當前活躍的托管 venv |
| `~/.kohakuterrarium/runtime/venv.old/` | 上一次成功更新後保留的舊 venv，可一次性回滾 |
| `~/.kohakuterrarium/runtime/.update.lock` | flock 檔，避免兩個 app 同時跑 `pip install` |
| `~/.kohakuterrarium/logs/launcher.log` | 殼層輪轉日誌（1 MB × 3） |

## 選擇來源

殼層支援四種來源。在 **設定 → 更新 → 來源** 選擇：

| 來源 | 執行的 pip 指令 | 適用情境 |
|---|---|---|
| **PyPI stable** | `pip install -U kohakuterrarium` | 預設。最新正式版。 |
| **PyPI 版本鎖定** | `pip install -U kohakuterrarium==1.5.0`（或 `<2.0`） | 暫時鎖在某版本測試或大規模部署前。 |
| **Git ref** | `pip install -U "git+<url>@main"`（分支 / tag / commit） | 跟著開發分支、你自己的 fork、或還沒上 PyPI 的 RC。 |
| **本地可編輯路徑** | `pip install -e /path/to/checkout` | 開發者直接從 Git checkout 跑。**停用自動更新** —— 你自己用 `git pull` 驅動。 |
| **內建（離線）** | `pip install --no-index --find-links=wheels-bundle/ kohakuterrarium` | 離線機器第一次啟動，或遠端來源不可達時的復原。 |

## 更新模式

| 模式 | 殼層啟動時的行為 |
|---|---|
| **手動** | 永遠不檢查。標籤上「立即檢查」/「更新」由你按。 |
| **啟動時通知** *（預設）* | 啟動後背景去查 PyPI / git（24 小時快取）。有新版會在 **設定 → 更新** 顯示橫幅；點「更新」安裝。 |
| **啟動時自動** | 啟動時檢查 **並** 安裝（啟動畫面顯示進度）。可以取消；取消就回到現有 venv。 |

`source.kind=local` 會強制把任何模式改成 **手動** —— 可編輯安裝是使用者自己管的。

## 更新流程細節

當你按 **更新**（或殼層觸發自動更新）：

1. **Flock** `~/.kohakuterrarium/runtime/.update.lock`，避免兩次啟動同時跑。
2. 在 `~/.kohakuterrarium/runtime/venv.new/` 建立新的 venv。
3. 按來源跑 `pip install`。
4. **冒煙測試**：import 框架、跑 `kt --help`。兩個都必須在 30 秒內成功。
5. 通過後，**原子地** 把 `venv` 改名為 `venv.old`，再把 `venv.new` 改名為 `venv`（kernel 級的 rename，瞬間完成）。
6. 把新版本 + 檢查時間戳寫回 `app-settings.json`。
7. 釋放 flock。重新啟動 app 就能用新版。

3-5 任何一步失敗，殼層會刪掉 `venv.new/`，現有 `venv/` 保留不動。錯誤顯示在進度彈窗；app 繼續跑原版本。

## 回滾

每次更新成功後上一個 venv 保留在 `~/.kohakuterrarium/runtime/venv.old/`。在更新分頁按 **回滾** 就交換回去。只能回滾一次（下一次成功更新會覆蓋 `venv.old`）。

## 復原 —— 兩個 venv 都壞了

如果 `venv/` 跟 `venv.old/` 都不在或都壞了,殼層會回退到 Briefcase 包內附的 **內建 wheels**。更新分頁會顯示「Recovery mode」橫幅,附 **從內建 wheels 重置 venv** 按鈕。即使網路或來源不可達,也能從離線副本把框架裝回來。

## 離線首次啟動 —— 內建優先安裝

桌面安裝包(MSI / `.app` / AppImage)**自帶一份框架 wheels**,與殼層並排打進包內。首次啟動時殼層會從這些內建 wheels 安裝,不會去打 PyPI —— 即使沒網或在防火牆後面,首啟一樣跑得起來。

殼層的判斷規則:

| 場景 | 首次安裝實際執行的 pip 指令 |
|---|---|
| 預設設定 + 安裝包內含內建 wheels | `pip install --no-index --find-links=<bundled>/ kohakuterrarium` |
| 使用者改過 `source.kind`(例如選了 Git) | 按使用者選擇執行 —— 跳過內建 wheels |
| 內建 wheels 不存在(開發安裝、損毀包) | 按設定來源走 PyPI 等備援 |
| 內建安裝失敗(wheel 壞) + 預設來源 | 自動改用 PyPI 復原 |
| 內建安裝失敗 + 使用者配了 Git / local | 直接報錯 —— 不掩蓋使用者的明確意圖 |

首次安裝完成後,**設定 → 更新** 分頁的「Installed」那行會寫成 `Installed: 1.5.x (from bundled offline copy)`,一眼就知道目前 venv 是哪個來源裝出來的。

### 後續更新預設仍走 PyPI

內建優先只對**首次安裝**生效。之後的更新(手動、啟動時通知、啟動時自動)按 `source.kind` 走,預設是 PyPI。按 **更新** 時殼層照常從 PyPI 拉最新版;內建 wheels 不會動,繼續作為 C2 安全網保留。

更新按鈕的文案會隨來源變化:

- 來源 = PyPI → `Update to <X> from PyPI`
- 來源 = Git → `Update from git`
- 來源 = Local → `Reinstall editable`
- 來源 = Bundled(明確設定) → `Reinstall from bundled (same version)`

要**永遠停留在內建版本**,就把 **更新模式** 設為 **手動**,永遠不按更新 —— 殼層就再也不會碰網路。

## CLI 對等：`kt self-update`

同樣的流程在終端也能跑：

```bash
kt self-update                  # 按設定的來源更新
kt self-update --dry-run        # 印出會跑什麼，不動任何東西
kt self-update --check-only     # 有新版退 0，已是最新退 1
kt self-update --source git --spec "https://github.com/.../@main"
```

`kt self-update` 會自動偵測 KohakuTerrarium 的安裝方式並走對應路徑：

- **殼層托管 venv** → 走原子 rename 流程（跟 GUI 一致）。
- **pipx** → `pipx upgrade kohakuterrarium`。
- **可編輯安裝** → 拒絕；告訴你去 checkout 跑 `git pull`。
- **系統套件** （`/usr/bin/python`） → 拒絕；告訴你用平台套件管理器。
- **其他使用者 venv** → 在當前直譯器跑 `pip install -U`。原子 rename + 回滾是殼層專屬，這裡沒有。

## 從舊 Bundle 遷移

KohakuTerrarium 1.5.0 同時發布舊版「凍結整個框架」的 Briefcase 包跟新的殼層包。如果你在舊版上，**設定 → 更新** 會顯示一次性「切換到自動更新版本」橫幅，連到 release 頁。下載殼層安裝器**一次**、裝一遍，之後每次更新都是殼層 venv 裡的 pip 操作 —— 不再需要下載安裝器。

殼層會保留你的 `~/.kohakuterrarium/` 使用者資料（session、設定、MCP server、API key）。所有設定原地不動，只是框架原始碼被重新裝到新的 venv。

## 疑難排解

- **第一次啟動卡在「Installing framework」** —— 看 `~/.kohakuterrarium/logs/launcher.log` 找 pip 的輸出。多半是網路 / proxy / 防火牆。把分頁裡的來源切到 **內建（離線）**，從備用 wheels 裝就好。
- **「Another update is in progress」** —— 之前的更新崩了，留下 lockfile。10 分鐘後殼層會提示「覆寫過期鎖」，同意後重試。
- **冒煙測試在安裝後失敗** —— 安裝完了但 `kt --help` 跑不起來。點 **回滾** 切回 `venv.old/`。如果也壞了就點 **從內建 wheels 重置 venv** 復原離線副本。
- **可編輯安裝但 `kt self-update` 拒絕** —— 故意的。在你的 checkout 跑 `git pull`，再 `pip install -e .` 刷新已安裝的 metadata。

## 另請參閱

- [部署 — Docker](deployment-docker.md) —— 容器更新流程改用 `docker pull`。
- [部署 — systemd](deployment-systemd.md) —— systemd 主機上跑 `kt self-update`，再 `systemctl restart kohakuterrarium-host` 讓新版本生效。
- [Serving 指南](serving.md) —— 殼層 `exec` 後框架的 `kt` 入口跑的就是 `kt serve`。
