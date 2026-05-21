---
title: 身份驗證
summary: 四個可選的身份驗證層 — 主機權杖、管理員密碼、使用者帳戶 — 在 API 伺服器邊界堆疊。依部署形態設定；預設全部關閉（目前行為）。
tags:
  - guides
  - deployment
  - authentication
---

# 身份驗證

KohakuTerrarium 預設以無身份驗證模式執行 — 適合桌面應用程式在
loopback 上執行，作業系統使用者即為信任邊界。其他情境（區網主機、
家庭伺服器、網際網路暴露的部署）有四個可選的身份驗證層在 API
伺服器上堆疊。每一層都透過 ``[auth]`` 設定區段選擇性啟用；預設值
保留單一使用者開放主機行為。

## 四個層級

| 層級 | 關卡 | 使用情境 |
|---|---|---|
| **L1** 主機選擇 | 僅前端 — 應用程式連接哪個後端 | 總是啟用（打包應用程式內建） |
| **L2** 主機權杖 | "用戶端是否被允許連接此主機？" | 區網 / 網際網路暴露的主機 |
| **L3** 管理員權杖 | "呼叫者是否被允許修改主機設定？" | 你希望家人使用主機但不給設定權限 |
| **L4** 使用者帳戶 | "請求作用於哪個使用者的工作階段 / UI 偏好？" | 多使用者共用主機 |

各層可以組合。鎖定設定的多使用者家庭伺服器同時啟用 L2 + L3 + L4；
單一使用者區網主機只啟用 L2；預設桌面什麼都不啟用。

## 架構不變量

身份驗證完全位於 API 伺服器邊界（``api/auth/``）。引擎、Studio、
terrarium runtime 和 session store 對使用者、權杖、主機**毫不知情**。
當 L4 啟用時，按使用者隔離透過引擎池將每個已認證請求路由到一個按
使用者分配的 ``Terrarium`` 引擎 — 引擎本身保持單租戶。

這意味著 CLI（``kt run``、``kt list``、``kt resume``）和內嵌 TUI
在所有身份驗證模式下保持不變；只有 FastAPI 伺服器進行多路復用。

## 設定

所有設定都在 ``<config_dir>/config.toml`` 的 ``[auth]`` 區段下：

```toml
[auth]
host_token = ""                   # 空字串 = 關閉
admin_token = ""                  # 空字串 = 關閉
multi_user = "off"                # off | optional | required
registration = "admin_only"       # open | invite_only | admin_only
loopback_bypass = true            # 127.0.0.1 跳過 L2
session_expire_hours = 168        # 7 天
session_idle_minutes = 0          # 0 = 不按閒置過期
bcrypt_rounds = 12                # 密碼雜湊成本因子
```

環境變數覆寫（最高優先級）：

| 環境變數 | 含義 |
|---|---|
| ``KT_AUTH_HOST_TOKEN`` | L2 權杖（內嵌） |
| ``KT_AUTH_HOST_TOKEN_FILE`` | 從檔案讀取 L2 權杖（Docker / systemd secrets） |
| ``KT_AUTH_ADMIN_TOKEN`` | L3 權杖（內嵌） |
| ``KT_AUTH_ADMIN_TOKEN_FILE`` | 從檔案讀取 L3 權杖 |
| ``KT_AUTH_MULTI_USER`` | ``off`` / ``optional`` / ``required`` |
| ``KT_AUTH_REGISTRATION`` | ``open`` / ``invite_only`` / ``admin_only`` |
| ``KT_AUTH_LOOPBACK_BYPASS`` | ``0`` / ``1`` |

``*_FILE`` 變體的存在是為了讓密鑰透過 Docker ``secrets:`` 掛載或
systemd ``LoadCredential=`` 指令傳遞 — 它們永遠不會出現在
``/proc/<pid>/environ`` 中。

## 發現主機啟用了什麼

```
GET /api/auth/capabilities                   （無需認證）
```

前端在任何其他 API 呼叫之前先存取這個端點，以了解需要提示什麼。
回應不包含任何密鑰 — 只有啟用旗標 + 模式中繼資料：

```json
{
  "schema": 1,
  "auth": {
    "host_token":  { "enabled": true,  "loopback_bypass": true },
    "admin_token": { "enabled": true },
    "multi_user":  { "enabled": true,  "mode": "required",
                      "registration": "invite_only" }
  }
}
```

## 第 2 層 — 主機權杖

一個共用密鑰控制每一個 ``/api/*`` 和 ``/ws/*`` 請求。

```bash
# 產生 + 儲存新的 host_token。
kt admin set-host-token
# 輪換已有的權杖（效果相同；別名）：
kt admin rotate-host-token
```

權杖寫入 ``<config_dir>/config.toml``。用戶端透過以下方式提供：

- HTTP：``Authorization: Bearer <token>``
- WebSocket：``Sec-WebSocket-Protocol: kt-token.<token>``（首選，
  不會洩漏到代理日誌中）**或** ``?token=<token>`` 查詢字串
  （便於 curl 使用的回退方案）。

**Loopback 旁路。** 預設情況下，來自 ``127.0.0.1`` / ``::1`` 的請求
跳過 L2（``loopback_bypass = true``）。這讓桌面應用程式使用順暢 —
你的應用程式不需要知道權杖就能與自己捆綁的主機通訊。在反向代理後
的生產部署中停用此功能：

```toml
[auth]
host_token = "..."
loopback_bypass = false
```

**CORS 預檢。** ``OPTIONS`` 請求無條件通過 L2，使跨來源瀏覽器能夠在
傳送實際（已認證）請求之前完成預檢。

## 第 3 層 — 管理員權杖

第二個共用密鑰**僅**控制設定修改路由 — 新增 LLM 密鑰、註冊 MCP
伺服器、安裝套件、編輯模型 / 設定檔。讀取存取和對話使用不被控制。

```bash
kt admin set-admin-token
```

用戶端在相關路由上透過 ``X-Admin-Token: <token>`` 提供。來自 L3
的 401 回應攜帶 ``X-Auth-Required: admin``，前端據此區分"需要管理員
密碼"和"需要登入"。

這就是**家庭伺服器**模式：擁有主機權杖的人可以對話 / 讀取設定；
只有持有管理員權杖的營運者可以修改 LLM 設定檔或安裝套件。

## 第 4 層 — 使用者帳戶

帶隔離工作階段、UI 偏好和 API 權杖的按使用者帳戶。

```toml
[auth]
multi_user = "required"           # 每個 /api/* 都需要登入
registration = "invite_only"
```

三種註冊模式：

| 模式 | 自助註冊 | 新使用者來自 |
|---|---|---|
| ``open`` | 是 | ``POST /api/auth/register``（任何人） |
| ``invite_only`` | 是，需權杖 | 管理員透過 ``kt admin invitations create`` 產生一次性邀請 |
| ``admin_only`` | 否 | 管理員執行 ``kt admin users add <username>`` |

**身份驗證形態：**

- **Web（同源）**：``POST /api/auth/login`` 設定 ``HttpOnly`` +
  ``SameSite=Lax`` Cookie。瀏覽器在每個後續請求中傳送它。
- **打包應用程式 / CLI / 跨源 Web**：``POST /api/auth/tokens`` 產生
  長期 API 權杖（建立時僅顯示一次）。用戶端透過
  ``Authorization: Bearer <token>`` 提供。

**按使用者資料佈局：**

```
<config_dir>/
├── auth.db
├── api_keys.yaml          # 共用 — 管理員維護
├── llm_profiles.yaml      # 共用 — 管理員維護
├── mcp_servers.yaml       # 共用 — 管理員維護
└── users/
    └── <user_id>/
        ├── ui_prefs.json
        └── sessions/
            └── <session-name>.kohakutr
```

LLM 密鑰、設定檔和 MCP 伺服器是**共用的**（管理員只需要管理一次）。
工作階段和 UI 偏好是**按使用者**的。

## 引導配方

新搭建家庭伺服器：

```bash
# 1. 產生身份驗證密鑰。
kt admin set-host-token
kt admin set-admin-token

# 2. 編輯 <config_dir>/config.toml 設定 multi_user + registration：
#    [auth]
#    multi_user = "required"
#    registration = "invite_only"

# 3. 建立第一個管理員使用者（互動式密碼提示）。
kt admin users add operator --role admin

# 4. 為家庭成員產生邀請。
kt admin invitations create --role user --expires-in-hours 168

# 5. 啟動伺服器。
kt serve start --host 0.0.0.0
```

透過你選擇的管道把邀請權杖發給每位家庭成員；他們使用權杖呼叫
``POST /api/auth/register`` 建立帳戶。

## 把現有單一使用者主機遷移到多使用者

當你在原本單一使用者的主機上啟用 L4 時，現有的 ``ui_prefs.json``
和 ``sessions/`` **不會**被自動移動。請明確宣告它們的歸屬：

```bash
# 把共用狀態的 UI 偏好 + .kohakutr 工作階段移到某使用者的命名空間。
kt admin migrate --from-shared-state --to-user operator
```

這是刻意設計 — 多使用者升級中的自動遷移可能把某人的工作階段移到
錯誤的命名空間。

## 加密原語

| 用途 | 原語 |
|---|---|
| 密碼雜湊 | bcrypt（成本因子可設定；預設 12） |
| API 權杖雜湊 | SHA3-512（單向、快速查詢） |
| 工作階段 ID 產生 | ``secrets.token_urlsafe(32)``（256 位元） |
| 權杖 / 管理員比較 | ``secrets.compare_digest``（常數時間） |

API 權杖和邀請由 CSPRNG 產生並以雜湊形式儲存 — DB 洩漏無法被重放。

## 威脅模型

| 威脅 | 防禦層 |
|---|---|
| 區網鄰居掃描連接埠並呼叫 API | L2 |
| 室友使用共用主機改你的 LLM 設定檔 | L3 或 L4 |
| 兩位家庭成員的對話歷史互相污染 | L4 |
| URL 中的權杖透過 referer / 代理日誌洩漏 | WS 子協定認證 |

身份驗證**不**防禦：

- 擁有 ``<config_dir>/`` shell 存取權限的使用者 — 他們能讀取
  ``auth.db``（bcrypt 雜湊；離線破解是標準成本）以及每位使用者的
  工作階段檔案。身份驗證是 API 邊界，不是作業系統邊界。
- 旁路通道攻擊（時序、流量分析） — 不在範圍內。

## TLS

框架在 1.5.0 中**不**自己終止 TLS。營運者在前面放置反向代理
（Caddy / nginx / Traefik）來處理 HTTPS。請參閱
[部署 — 反向代理](deployment-reverse-proxy.md)。當 ``auth`` 啟用
但主機 URL 為明文 ``http://`` 且非 loopback 時，前端會顯示橫幅
*"連線未加密"*。

## 參見

- [部署 — Docker](deployment-docker.md) — 透過 ``secrets:`` 掛載
  傳遞 ``[auth]`` 設定
- [部署 — systemd](deployment-systemd.md) — 透過 ``LoadCredential=``
  指令傳遞 ``[auth]`` 設定
- [部署 — 反向代理](deployment-reverse-proxy.md) — TLS 終止 +
  CORS 白名單用於託管的靜態前端
