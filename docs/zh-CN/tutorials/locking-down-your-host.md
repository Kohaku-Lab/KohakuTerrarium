---
title: 给你的主机加锁
summary: 一步步给 KohakuTerrarium 主机加上身份验证 — 从「局域网谁都能用」到「家人各自登录、只有我能改设置」。
tags:
  - tutorials
  - auth
  - deployment
---

# 给你的主机加锁

**问题：** 你开始跑 `kt serve`，结果发现局域网上任何人都能访问
`http://你的IP:8001` 并使用你的 LLM（= 烧你的 API key）。

**终态：** 四个递进的加锁级别，每个级别只需 30 秒复制粘贴。挑一个
匹配你当前情况的级别。

**前置：** `kt` 已安装、能执行 `kt serve start`。

如果你想要参考文档（四层模型、威胁模型、加密原语），参见
[身份验证](../guides/authentication.md)。本教程跳过理论，直接展示命令。

---

## 挑选你的级别

| 你想要 | 对应级别 |
|---|---|
| 自己机器上的桌面应用 — 零设置、零打扰 | **Level 0**（默认 — 什么都不用做） |
| 只有知道共享密码的人才能连接 | **Level 1** — 主机令牌 |
| 朋友可以聊天 / 使用主机；只有我能改 LLM key + 装包 | **Level 2** — 管理员密码 |
| 每位家庭成员有自己的登录 + 隔离的对话会话 | **Level 3** — 多用户 |

每个级别在前一级别之上叠加。需求达到了就停。

---

## Level 0 — 桌面应用，默认就够了

**什么都别做。** 桌面应用绑在 `127.0.0.1`；网络上没人能连。操作
系统用户就是信任边界。

测试：

```bash
kt app                    # 打开桌面窗口
# 从局域网另一台机器：
curl http://你的局域网IP:8001/api/auth/capabilities
# → connection refused（桌面从未绑到局域网）
```

如果以后你要开始跑 `kt serve --host 0.0.0.0`，跳到 Level 1。

---

## Level 1 — 主机令牌（5 分钟）

主机现在要求每个 API 调用都带 `Authorization: Bearer <token>`。
Loopback（`127.0.0.1`）默认依然旁路，所以桌面应用不用输令牌也能继续用。

### 步骤 1 — 生成令牌

```bash
kt admin set-host-token
# host_token saved (length 64 chars).
# written to: /home/you/.kohakuterrarium/config.toml
```

这会生成 32 个随机字节并写入 `config.toml` 的 `[auth] host_token`。

### 步骤 2 — 重启服务器

```bash
kt serve restart
# (如果还没启动，就直接 kt serve start --host 0.0.0.0)
```

### 步骤 3 — 验证

从另一台机器：

```bash
curl http://你的局域网IP:8001/api/version
# → 401 Unauthorized
```

带上正确令牌：

```bash
TOKEN=$(kt admin show-host-token --yes)
curl -H "Authorization: Bearer $TOKEN" http://你的局域网IP:8001/api/version
# → 200 OK
```

### 步骤 4 — 把令牌发给朋友

通过安全渠道分享 `$TOKEN`（Signal / 1Password 分享 / 不要走微信）。
任何拿到令牌的人都能通过 web 前端或 `curl` 连接。

### 关掉 loopback 旁路（生产环境在反向代理后面）

如果你在 nginx / Caddy 后面，你的"loopback"流量其实是从互联网代理过来的，
编辑 `~/.kohakuterrarium/config.toml`：

```toml
[auth]
host_token = "..."          # 已经有了
loopback_bypass = false     # 加上这一行
```

重启。现在连 `127.0.0.1` 都要令牌。

### 令牌泄露后轮换

```bash
kt admin rotate-host-token   # 生成新的
kt serve restart             # 现有客户端掉线，需要新令牌
```

---

## Level 2 — 管理员密码（再加 5 分钟）

拿到主机令牌的朋友现在能聊天 — 但他们也能点 Models 页面的"保存"
按钮，把你的 OpenAI key 改了。再加一个用于配置修改的密码。

### 步骤 1 — 生成管理员令牌

```bash
kt admin set-admin-token
# admin_token saved (length 64 chars).
```

重启服务器。

### 步骤 2 — 现在哪些路由被关卡

这些路由没有 `X-Admin-Token: <admin_token>` 就拒绝：

- `POST /api/settings/keys` — 新增 / 修改 LLM API key
- `POST /api/settings/profiles` — LLM 模型配置
- `POST /api/settings/mcp` — MCP 服务器注册
- `POST /api/registry/install` — 装包
- `PUT /api/settings/config-files/{name}/content` — 直接编辑配置文件

这些没受影响，照常工作（只读 / 聊天 / 会话）：

- `/api/auth/capabilities`、`/me`、`/sessions/*`、聊天 WS

### 步骤 3 — 验证

```bash
HOST=$(kt admin show-host-token --yes)
ADMIN=$(... 从 set-admin-token 的输出保存)

# 朋友尝试改 key → 401
curl -X POST http://localhost:8001/api/settings/keys \
  -H "Authorization: Bearer $HOST" \
  -H "Content-Type: application/json" \
  -d '{"provider":"openai","key":"sk-..."}'
# → 401 {"detail": {"error": "admin_required", ...}}

# 你带管理员令牌 → 200
curl -X POST http://localhost:8001/api/settings/keys \
  -H "Authorization: Bearer $HOST" \
  -H "X-Admin-Token: $ADMIN" \
  -H "Content-Type: application/json" \
  -d '{"provider":"openai","key":"sk-..."}'
```

### 步骤 4 — 分享主机令牌，保留管理员令牌

把 `$HOST` 发给朋友。**不要** 给 `$ADMIN`。前端要求他们登录时，
他们粘贴主机令牌；他们想编辑配置时，UI 会灰掉（等 Vue 管理界面
上线后会弹"管理员密码"提示框） — 只有你有那个。

---

## Level 3 — 多用户（再加 10 分钟）

现在每个人用自己的用户名 + 密码登录。他们的聊天会话、标签页、
UI 偏好都隔离到各自账户。共享资源（LLM key、配置文件、MCP 服务器、
已装包）继续共享，因为管理员只需要管理一次。

### 步骤 1 — 编辑 config.toml

```toml
[auth]
host_token = "..."              # 已设置
admin_token = "..."             # 已设置
multi_user = "required"         # ← 新增
registration = "invite_only"    # ← 新增（或 "admin_only" / "open"）
loopback_bypass = false         # 在代理后面就关掉
```

重启服务器。

### 步骤 2 — 创建第一个管理员用户

```bash
kt admin users add operator --role admin
# Password: ************
# Confirm password: ************
# user created: id=1 username=operator role=admin
```

写入 `~/.kohakuterrarium/auth.db`（sqlite、bcrypt 哈希）。

### 步骤 3 — 邀请家庭成员

每人生成一个邀请令牌：

```bash
kt admin invitations create --role user --expires-in-hours 168
# invitation created (id=1, role=user):
#   token: 9f3a8b7e2c1d4f5a6b9c8e7d2f1a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a
#   expires_at: 2026-05-29T12:00:00+00:00
```

通过安全渠道把每个令牌发给对应的人。每个令牌一次性使用，可选时间限制。

### 步骤 4 — 家庭成员注册

每人用自己的邀请令牌 POST 一次：

```bash
curl -X POST http://你的主机:8001/api/auth/register \
  -H "Authorization: Bearer $HOST" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "alice",
    "password": "他们选的密码",
    "invitation_token": "9f3a8b..."
  }'
```

响应设置 session cookie + 返回用户信息。之后他们用用户名 + 密码登录：

```bash
curl -X POST http://你的主机:8001/api/auth/login \
  -H "Authorization: Bearer $HOST" \
  -H "Content-Type: application/json" \
  -d '{"username":"alice","password":"他们选的密码"}' \
  -c alice-cookies.txt
```

### 步骤 5 — 验证隔离

每个用户在磁盘上有自己的一块：

```
~/.kohakuterrarium/
├── auth.db
├── api_keys.yaml           # 共享 — 管理员看 + 管
├── llm_profiles.yaml       # 共享
├── mcp_servers.yaml        # 共享
└── users/
    ├── 1/                  # operator
    │   ├── ui_prefs.json
    │   └── sessions/
    │       └── *.kohakutr  # operator 的对话
    └── 2/                  # alice
        ├── ui_prefs.json
        └── sessions/
            └── *.kohakutr  # alice 的对话 — operator 看不到
```

### 把现有会话迁到你的用户命名空间

如果你之前一直单用户用主机，想把现有对话保留在你的新账户下：

```bash
kt admin migrate --from-shared-state --to-user operator --dry-run
# （展示会被移动的内容；安全 — 不改文件）

kt admin migrate --from-shared-state --to-user operator
# 把 <config_dir>/ui_prefs.json + <config_dir>/sessions/*.kohakutr
# 移到 users/<operator-id>/
```

在任何其他用户开始用主机之前跑一次，避免他们的空命名空间被你的数据
意外填充。

---

## 常用操作

### 禁用一个用户（比如孩子发现了密码）

```bash
kt admin users disable alice
# user 'alice' disabled
#   (dropped 2 active session(s))
```

他们的会话立即吊销；之后登录失败，直到你 `kt admin users enable alice`。

### 删除用户

```bash
kt admin users delete alice --yes
# user 'alice' deleted (id=2).
# note: per-user dir users/2/ kept (rm -rf to discard the user's sessions / prefs).
```

磁盘目录**不会**被自动删除 — 想清掉他们的数据自己 `rm -rf`。

### 提升 / 降级管理员

```bash
kt admin users grant alice    # alice → 管理员
kt admin users demote alice   # alice → 普通用户
```

CLI 拒绝降级 / 禁用最后一个活跃管理员，避免你把自己锁在外面。

### 列出主机上的人

```bash
kt admin users list
# ID    USERNAME                  ROLE      ACTIVE    LAST_LOGIN
# ----------------------------------------------------------------------
# 1     operator                  admin     yes       2026-05-22T14:32:01+00:00
# 2     alice                     user      yes       2026-05-22T13:50:11+00:00
# 3     bob                       user      no        -
```

### 重置密码（管理员）

目前没有"重置"动词 — 管理员通过 API 重新发：

```bash
# kt admin 暂时没有 password-reset；管理员先用自己的会话
# 直接用 auth API：
curl -X PATCH http://localhost:8001/api/auth/users/2 \
  -H "Authorization: Bearer $HOST" \
  -H "X-Admin-Token: $ADMIN" \
  -c admin-cookies.txt \
  -b admin-cookies.txt \
  -d '{"is_active": false}'
# 然后删除 + 重建，或等未来的 kt admin users reset-password。
```

`kt admin users reset-password` 在路线图上。

---

## 部署专属配方

上面四个级别是同一套逻辑配置；不同的是按部署方式怎么传递令牌。

### Docker compose — 通过 secrets 文件传递

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

启动栈前一次性产 secret 文件：

```bash
mkdir -p secrets
python -c "import secrets;print(secrets.token_hex(32))" > secrets/host_token
python -c "import secrets;print(secrets.token_hex(32))" > secrets/admin_token
chmod 600 secrets/*
docker compose up -d
docker compose exec kohakuterrarium kt admin users add operator --role admin
```

完整 Compose 示例参见 [部署 — Docker](../guides/deployment-docker.md)。

### systemd — 通过 `LoadCredential=`

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

drop-in 用 `LoadCredential=`，所以密钥不会出现在 `/proc/<pid>/environ` 中。
参见 [部署 — systemd](../guides/deployment-systemd.md)。

---

## 速查表

### 环境变量覆盖（最高优先级，覆盖 config.toml）

```bash
export KT_AUTH_HOST_TOKEN="..."           # 或 KT_AUTH_HOST_TOKEN_FILE=/path
export KT_AUTH_ADMIN_TOKEN="..."          # 或 KT_AUTH_ADMIN_TOKEN_FILE=/path
export KT_AUTH_MULTI_USER=required        # off | optional | required
export KT_AUTH_REGISTRATION=invite_only   # open | invite_only | admin_only
export KT_AUTH_LOOPBACK_BYPASS=0          # 0 = 始终要令牌
```

### 前端发送的请求格式

| 协议形态 | 携带 |
|---|---|
| `Authorization: Bearer <host_token>` | L1（主机令牌） |
| `Cookie: kt_session=<id>` | L4（用户会话，HTTP 路由） |
| `Authorization: Bearer <api_token>` | L4（用户 API 令牌，CLI / 移动端） |
| `X-Admin-Token: <admin_token>` | L3（管理员操作） |
| WS `Sec-WebSocket-Protocol: kt-token.<host_token>` | L1（WebSocket） |
| WS `?token=<host_token>`（回退） | L1（WebSocket，会进日志） |

### 探测启用了哪些层（无需认证）

```bash
curl http://你的主机:8001/api/auth/capabilities
```

返回每层的启用标志 — 适合 shell 脚本和前端连接状态机。

---

## 可能出错的情况

| 现象 | 原因 | 修复 |
|---|---|---|
| 所有调用都 `401 unauthorized` | 主机令牌错 / 缺 | 重跑 `kt admin show-host-token --yes`，仔细核对 header |
| 保存设置时 `401 admin_required` | L3 启用，没带 `X-Admin-Token` | 加上管理员令牌 header |
| 切换认证后浏览器一直在重连 | localStorage 里的令牌与新配置不匹配 | 清浏览器存储 / 重粘令牌 |
| `/me` 返回 `multi_user_disabled` | L4 没开；`/me` 没意义 | 要么开 L4，要么别调 `/me` |
| 注册时 `invitation_invalid` | 令牌已被用 / 已过期 | 生成新邀请 |
| `kt admin set-host-token` 报 "TOML shape ... cannot preserve" | 你的 `config.toml` 有顶层标量 / 嵌套表 | 把顶层 key 移到 `[section]` 下 |
| 把自己锁在管理员外面 | 降级了唯一的管理员 | 任何 shell 跑 `kt admin users grant <name>` — 离线就能用 |

---

## 接下来

- Vue 前端的认证 UI 还没出（路线图
  [Phase H–K](../../../plans/1.5.0-roadmap/03-frontend-backend-connection/README.md)）。
  在那之前你用 `curl` + cookies，或者 `Authorization: Bearer` 配 API 令牌。
  后端已稳定可用。
- 跨主机会话导入 / 导出、密码重置、2FA — 都推到 1.6+。

架构 / 威胁模型 / 为什么这么设计的阅读，参见
[身份验证指南](../guides/authentication.md)。
