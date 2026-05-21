---
title: 給你的主機加鎖
summary: 一步步給 KohakuTerrarium 主機加上身份驗證 — 從「區網誰都能用」到「家人各自登入、只有我能改設定」。
tags:
  - tutorials
  - auth
  - deployment
---

# 給你的主機加鎖

**問題：** 你開始跑 `kt serve`，結果發現區網上任何人都能存取
`http://你的IP:8001` 並使用你的 LLM（= 燒你的 API key）。

**終態：** 四個遞進的加鎖級別,每個級別只需 30 秒複製貼上。挑一個
匹配你目前情況的級別。

**前置：** `kt` 已安裝、能執行 `kt serve start`。

如果你想要參考文件（四層模型、威脅模型、加密原語），請參閱
[身份驗證](../guides/authentication.md)。本教學跳過理論,直接展示指令。

---

## 挑選你的級別

| 你想要 | 對應級別 |
|---|---|
| 自己機器上的桌面應用程式 — 零設定、零打擾 | **Level 0**（預設 — 什麼都不用做） |
| 只有知道共用密碼的人才能連線 | **Level 1** — 主機權杖 |
| 朋友可以聊天 / 使用主機；只有我能改 LLM key + 裝套件 | **Level 2** — 管理員密碼 |
| 每位家庭成員有自己的登入 + 隔離的對話工作階段 | **Level 3** — 多使用者 |

每個級別在前一級別之上疊加。需求達到了就停。

---

## Level 0 — 桌面應用程式,預設就夠了

**什麼都別做。** 桌面應用程式綁在 `127.0.0.1`；網路上沒人能連。
作業系統使用者就是信任邊界。

測試:

```bash
kt app                    # 開啟桌面視窗
# 從區網另一台機器:
curl http://你的區網IP:8001/api/auth/capabilities
# → connection refused（桌面從未綁到區網）
```

如果以後你要開始跑 `kt serve --host 0.0.0.0`,跳到 Level 1。

---

## Level 1 — 主機權杖（5 分鐘）

主機現在要求每個 API 呼叫都帶 `Authorization: Bearer <token>`。
Loopback（`127.0.0.1`）預設依然旁路,所以桌面應用程式不用輸權杖也能繼續用。

### 步驟 1 — 產生權杖

```bash
kt admin set-host-token
# host_token saved (length 64 chars).
# written to: /home/you/.kohakuterrarium/config.toml
```

這會產生 32 個隨機位元組並寫入 `config.toml` 的 `[auth] host_token`。

### 步驟 2 — 重啟伺服器

```bash
kt serve restart
# (如果還沒啟動,就直接 kt serve start --host 0.0.0.0)
```

### 步驟 3 — 驗證

從另一台機器:

```bash
curl http://你的區網IP:8001/api/version
# → 401 Unauthorized
```

帶上正確權杖:

```bash
TOKEN=$(kt admin show-host-token --yes)
curl -H "Authorization: Bearer $TOKEN" http://你的區網IP:8001/api/version
# → 200 OK
```

### 步驟 4 — 把權杖發給朋友

透過安全管道分享 `$TOKEN`（Signal / 1Password 分享 / 不要走 LINE）。
任何拿到權杖的人都能透過 web 前端或 `curl` 連線。

### 關掉 loopback 旁路（生產環境在反向代理後面）

如果你在 nginx / Caddy 後面,你的「loopback」流量其實是從網際網路
代理過來的,編輯 `~/.kohakuterrarium/config.toml`:

```toml
[auth]
host_token = "..."          # 已經有了
loopback_bypass = false     # 加上這一行
```

重啟。現在連 `127.0.0.1` 都要權杖。

### 權杖洩漏後輪換

```bash
kt admin rotate-host-token   # 產生新的
kt serve restart             # 現有用戶端掉線,需要新權杖
```

---

## Level 2 — 管理員密碼（再加 5 分鐘）

拿到主機權杖的朋友現在能聊天 — 但他們也能點 Models 頁面的「儲存」
按鈕,把你的 OpenAI key 改了。再加一個用於設定修改的密碼。

### 步驟 1 — 產生管理員權杖

```bash
kt admin set-admin-token
# admin_token saved (length 64 chars).
```

重啟伺服器。

### 步驟 2 — 現在哪些路由被關卡

這些路由沒有 `X-Admin-Token: <admin_token>` 就拒絕:

- `POST /api/settings/keys` — 新增 / 修改 LLM API key
- `POST /api/settings/profiles` — LLM 模型設定
- `POST /api/settings/mcp` — MCP 伺服器註冊
- `POST /api/registry/install` — 裝套件
- `PUT /api/settings/config-files/{name}/content` — 直接編輯設定檔

這些沒受影響,照常運作（唯讀 / 聊天 / 工作階段）:

- `/api/auth/capabilities`、`/me`、`/sessions/*`、聊天 WS

### 步驟 3 — 驗證

```bash
HOST=$(kt admin show-host-token --yes)
ADMIN=$(... 從 set-admin-token 的輸出儲存)

# 朋友嘗試改 key → 401
curl -X POST http://localhost:8001/api/settings/keys \
  -H "Authorization: Bearer $HOST" \
  -H "Content-Type: application/json" \
  -d '{"provider":"openai","key":"sk-..."}'
# → 401 {"detail": {"error": "admin_required", ...}}

# 你帶管理員權杖 → 200
curl -X POST http://localhost:8001/api/settings/keys \
  -H "Authorization: Bearer $HOST" \
  -H "X-Admin-Token: $ADMIN" \
  -H "Content-Type: application/json" \
  -d '{"provider":"openai","key":"sk-..."}'
```

### 步驟 4 — 分享主機權杖,保留管理員權杖

把 `$HOST` 發給朋友。**不要**給 `$ADMIN`。前端要求他們登入時,
他們貼上主機權杖；他們想編輯設定時,UI 會灰掉（等 Vue 管理介面
上線後會跳「管理員密碼」提示框）— 只有你有那個。

---

## Level 3 — 多使用者（再加 10 分鐘）

現在每個人用自己的使用者名稱 + 密碼登入。他們的聊天工作階段、
分頁、UI 偏好都隔離到各自帳號。共用資源（LLM key、設定檔、MCP
伺服器、已裝套件）繼續共用,因為管理員只需要管理一次。

### 步驟 1 — 編輯 config.toml

```toml
[auth]
host_token = "..."              # 已設定
admin_token = "..."             # 已設定
multi_user = "required"         # ← 新增
registration = "invite_only"    # ← 新增（或 "admin_only" / "open"）
loopback_bypass = false         # 在代理後面就關掉
```

重啟伺服器。

### 步驟 2 — 建立第一個管理員使用者

```bash
kt admin users add operator --role admin
# Password: ************
# Confirm password: ************
# user created: id=1 username=operator role=admin
```

寫入 `~/.kohakuterrarium/auth.db`（sqlite、bcrypt 雜湊）。

### 步驟 3 — 邀請家庭成員

每人產生一個邀請權杖:

```bash
kt admin invitations create --role user --expires-in-hours 168
# invitation created (id=1, role=user):
#   token: 9f3a8b7e2c1d4f5a6b9c8e7d2f1a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a
#   expires_at: 2026-05-29T12:00:00+00:00
```

透過安全管道把每個權杖發給對應的人。每個權杖一次性使用,可選時間限制。

### 步驟 4 — 家庭成員註冊

每人用自己的邀請權杖 POST 一次:

```bash
curl -X POST http://你的主機:8001/api/auth/register \
  -H "Authorization: Bearer $HOST" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "alice",
    "password": "他們選的密碼",
    "invitation_token": "9f3a8b..."
  }'
```

回應設定 session cookie + 回傳使用者資訊。之後他們用使用者名稱 + 密碼登入:

```bash
curl -X POST http://你的主機:8001/api/auth/login \
  -H "Authorization: Bearer $HOST" \
  -H "Content-Type: application/json" \
  -d '{"username":"alice","password":"他們選的密碼"}' \
  -c alice-cookies.txt
```

### 步驟 5 — 驗證隔離

每個使用者在磁碟上有自己的一塊:

```
~/.kohakuterrarium/
├── auth.db
├── api_keys.yaml           # 共用 — 管理員看 + 管
├── llm_profiles.yaml       # 共用
├── mcp_servers.yaml        # 共用
└── users/
    ├── 1/                  # operator
    │   ├── ui_prefs.json
    │   └── sessions/
    │       └── *.kohakutr  # operator 的對話
    └── 2/                  # alice
        ├── ui_prefs.json
        └── sessions/
            └── *.kohakutr  # alice 的對話 — operator 看不到
```

### 把現有工作階段遷到你的使用者命名空間

如果你之前一直單一使用者用主機,想把現有對話保留在你的新帳號下:

```bash
kt admin migrate --from-shared-state --to-user operator --dry-run
# （展示會被移動的內容；安全 — 不改檔案）

kt admin migrate --from-shared-state --to-user operator
# 把 <config_dir>/ui_prefs.json + <config_dir>/sessions/*.kohakutr
# 移到 users/<operator-id>/
```

在任何其他使用者開始用主機之前跑一次,避免他們的空命名空間被你的資料
意外填入。

---

## 常用操作

### 停用一個使用者（比如孩子發現了密碼）

```bash
kt admin users disable alice
# user 'alice' disabled
#   (dropped 2 active session(s))
```

他們的工作階段立即撤銷；之後登入失敗,直到你 `kt admin users enable alice`。

### 刪除使用者

```bash
kt admin users delete alice --yes
# user 'alice' deleted (id=2).
# note: per-user dir users/2/ kept (rm -rf to discard the user's sessions / prefs).
```

磁碟目錄**不會**被自動刪除 — 想清掉他們的資料自己 `rm -rf`。

### 提升 / 降級管理員

```bash
kt admin users grant alice    # alice → 管理員
kt admin users demote alice   # alice → 普通使用者
```

CLI 拒絕降級 / 停用最後一個活躍管理員,避免你把自己鎖在外面。

### 列出主機上的人

```bash
kt admin users list
# ID    USERNAME                  ROLE      ACTIVE    LAST_LOGIN
# ----------------------------------------------------------------------
# 1     operator                  admin     yes       2026-05-22T14:32:01+00:00
# 2     alice                     user      yes       2026-05-22T13:50:11+00:00
# 3     bob                       user      no        -
```

### 重設密碼（管理員）

目前沒有「重設」動詞 — 管理員透過 API 重新發:

```bash
# kt admin 暫時沒有 password-reset；管理員先用自己的工作階段
# 直接用 auth API:
curl -X PATCH http://localhost:8001/api/auth/users/2 \
  -H "Authorization: Bearer $HOST" \
  -H "X-Admin-Token: $ADMIN" \
  -c admin-cookies.txt \
  -b admin-cookies.txt \
  -d '{"is_active": false}'
# 然後刪除 + 重建,或等未來的 kt admin users reset-password。
```

`kt admin users reset-password` 在路線圖上。

---

## 部署專屬配方

上面四個級別是同一套邏輯設定；不同的是按部署方式怎麼傳遞權杖。

### Docker compose — 透過 secrets 檔案傳遞

```yaml
services:
  kohakuterrarium:
    image: ghcr.io/kohaku-lab/kohakuterrarium:latest
    environment:
      KT_AUTH_HOST_TOKEN_FILE: /run/secrets/host_token
      KT_AUTH_ADMIN_TOKEN_FILE: /run/secrets/admin_token
      KT_AUTH_MULTI_USER: "required"
      KT_AUTH_REGISTRATION: "invite_only"
      KT_AUTH_LOOPBACK_BYPASS: "0"
    secrets:
      - host_token
      - admin_token
    volumes:
      - kt-config:/root/.kohakuterrarium
    ports:
      - "127.0.0.1:8001:8001"   # 前面跑反向代理做 TLS
secrets:
  host_token:  { file: ./secrets/host_token }
  admin_token: { file: ./secrets/admin_token }
volumes:
  kt-config:
```

啟動 stack 前一次性產 secret 檔案:

```bash
mkdir -p secrets
python -c "import secrets;print(secrets.token_hex(32))" > secrets/host_token
python -c "import secrets;print(secrets.token_hex(32))" > secrets/admin_token
chmod 600 secrets/*
docker compose up -d
docker compose exec kohakuterrarium kt admin users add operator --role admin
```

完整 Compose 範例參見 [部署 — Docker](../guides/deployment-docker.md)。

### systemd — 透過 `LoadCredential=`

```bash
sudo mkdir -p /etc/systemd/system/kohakuterrarium-host.service.d
sudo cp packaging/systemd/auth-secrets.example.conf \
    /etc/systemd/system/kohakuterrarium-host.service.d/auth.conf

sudo mkdir -p /etc/kohakuterrarium/credentials
python -c "import secrets;print(secrets.token_hex(32))" | \
    sudo install -m 0400 /dev/stdin /etc/kohakuterrarium/credentials/host_token
python -c "import secrets;print(secrets.token_hex(32))" | \
    sudo install -m 0400 /dev/stdin /etc/kohakuterrarium/credentials/admin_token

sudo systemctl daemon-reload
sudo systemctl restart kohakuterrarium-host

sudo -u kohakuterrarium-host kt admin users add operator --role admin
```

drop-in 用 `LoadCredential=`,所以密鑰不會出現在 `/proc/<pid>/environ` 中。
參見 [部署 — systemd](../guides/deployment-systemd.md)。

---

## 速查表

### 環境變數覆寫（最高優先級,覆寫 config.toml）

```bash
export KT_AUTH_HOST_TOKEN="..."           # 或 KT_AUTH_HOST_TOKEN_FILE=/path
export KT_AUTH_ADMIN_TOKEN="..."          # 或 KT_AUTH_ADMIN_TOKEN_FILE=/path
export KT_AUTH_MULTI_USER=required        # off | optional | required
export KT_AUTH_REGISTRATION=invite_only   # open | invite_only | admin_only
export KT_AUTH_LOOPBACK_BYPASS=0          # 0 = 始終要權杖
```

### 前端傳送的請求格式

| 協定形態 | 攜帶 |
|---|---|
| `Authorization: Bearer <host_token>` | L1（主機權杖） |
| `Cookie: kt_session=<id>` | L4（使用者工作階段,HTTP 路由） |
| `Authorization: Bearer <api_token>` | L4（使用者 API 權杖,CLI / 行動端） |
| `X-Admin-Token: <admin_token>` | L3（管理員操作） |
| WS `Sec-WebSocket-Protocol: kt-token.<host_token>` | L1（WebSocket） |
| WS `?token=<host_token>`（回退） | L1（WebSocket,會進日誌） |

### 探測啟用了哪些層（無需認證）

```bash
curl http://你的主機:8001/api/auth/capabilities
```

回傳每層的啟用旗標 — 適合 shell 腳本和前端連線狀態機。

---

## 可能出錯的情況

| 現象 | 原因 | 修復 |
|---|---|---|
| 所有呼叫都 `401 unauthorized` | 主機權杖錯 / 缺 | 重跑 `kt admin show-host-token --yes`,仔細核對 header |
| 儲存設定時 `401 admin_required` | L3 啟用,沒帶 `X-Admin-Token` | 加上管理員權杖 header |
| 切換認證後瀏覽器一直在重連 | localStorage 裡的權杖與新設定不匹配 | 清瀏覽器儲存 / 重貼權杖 |
| `/me` 回 `multi_user_disabled` | L4 沒開；`/me` 沒意義 | 要麼開 L4,要麼別呼叫 `/me` |
| 註冊時 `invitation_invalid` | 權杖已被用 / 已過期 | 產生新邀請 |
| `kt admin set-host-token` 報 「TOML shape ... cannot preserve」 | 你的 `config.toml` 有頂層純量 / 巢狀表 | 把頂層 key 移到 `[section]` 下 |
| 把自己鎖在管理員外面 | 降級了唯一的管理員 | 任何 shell 跑 `kt admin users grant <name>` — 離線就能用 |

---

## 接下來

- Vue 前端的認證 UI 還沒出（路線圖
  [Phase H–K](../../../plans/1.5.0-roadmap/03-frontend-backend-connection/README.md)）。
  在那之前你用 `curl` + cookies,或者 `Authorization: Bearer` 配 API 權杖。
  後端已穩定可用。
- 跨主機工作階段匯入 / 匯出、密碼重設、2FA — 都推到 1.6+。

架構 / 威脅模型 / 為什麼這麼設計的閱讀,參見
[身份驗證指南](../guides/authentication.md)。
