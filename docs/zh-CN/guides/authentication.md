---
title: 身份验证
summary: 四个可选的身份验证层 — 主机令牌、管理员密码、用户账户 — 在 API 服务器边界堆叠。按部署形态配置；默认全部关闭（当前行为）。
tags:
  - guides
  - deployment
  - authentication
---

# 身份验证

KohakuTerrarium 默认以无身份验证模式运行 — 适合桌面应用在 loopback
上运行，操作系统用户即是信任边界。其他场景（局域网主机、家庭服务器、
互联网暴露的部署）有四个可选的身份验证层在 API 服务器上堆叠。每一层
都通过 ``[auth]`` 配置节段选择性启用；默认值保留单用户开放主机行为。

## 四个层级

| 层级 | 关卡 | 使用场景 |
|---|---|---|
| **L1** 主机选择 | 仅前端 — 应用连接哪个后端 | 总是启用（打包应用内置） |
| **L2** 主机令牌 | "客户端是否被允许连接此主机？" | 局域网 / 互联网暴露的主机 |
| **L3** 管理员令牌 | "调用者是否被允许修改主机配置？" | 你希望家人使用主机但不给配置权限 |
| **L4** 用户账户 | "请求作用于哪个用户的会话 / UI 偏好？" | 多用户共享主机 |

各层可以组合。锁定配置的多用户家庭服务器同时启用 L2 + L3 + L4；
单用户局域网主机只启用 L2；默认桌面什么都不启用。

## 架构不变量

身份验证完全位于 API 服务器边界（``api/auth/``）。引擎、Studio、
terrarium runtime 和 session store 对用户、令牌、主机**毫不知情**。
当 L4 启用时，按用户隔离通过引擎池将每个已认证请求路由到一个按用户
分配的 ``Terrarium`` 引擎 — 引擎本身保持单租户。

这意味着 CLI（``kt run``、``kt list``、``kt resume``）和内嵌 TUI
在所有身份验证模式下保持不变；只有 FastAPI 服务器进行多路复用。

## 配置

所有配置都在 ``<config_dir>/config.toml`` 的 ``[auth]`` 节段下：

```toml
[auth]
host_token = ""                   # 空字符串 = 关闭
admin_token = ""                  # 空字符串 = 关闭
multi_user = "off"                # off | optional | required
registration = "admin_only"       # open | invite_only | admin_only
loopback_bypass = true            # 127.0.0.1 跳过 L2
session_expire_hours = 168        # 7 天
session_idle_minutes = 0          # 0 = 不按空闲过期
bcrypt_rounds = 12                # 密码哈希成本因子
```

环境变量覆盖（最高优先级）：

| 环境变量 | 含义 |
|---|---|
| ``KT_AUTH_HOST_TOKEN`` | L2 令牌（内联） |
| ``KT_AUTH_HOST_TOKEN_FILE`` | 从文件读取 L2 令牌（Docker / systemd secrets） |
| ``KT_AUTH_ADMIN_TOKEN`` | L3 令牌（内联） |
| ``KT_AUTH_ADMIN_TOKEN_FILE`` | 从文件读取 L3 令牌 |
| ``KT_AUTH_MULTI_USER`` | ``off`` / ``optional`` / ``required`` |
| ``KT_AUTH_REGISTRATION`` | ``open`` / ``invite_only`` / ``admin_only`` |
| ``KT_AUTH_LOOPBACK_BYPASS`` | ``0`` / ``1`` |

``*_FILE`` 变体的存在是为了让密钥通过 Docker ``secrets:`` 挂载或
systemd ``LoadCredential=`` 指令传递 — 它们永远不会出现在
``/proc/<pid>/environ`` 中。

## 发现主机启用了什么

```
GET /api/auth/capabilities                   （无需认证）
```

前端在任何其他 API 调用之前先访问这个端点，以了解需要提示什么。
响应不包含任何密钥 — 只有启用标志 + 模式元数据：

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

## 第 2 层 — 主机令牌

一个共享密钥控制每一个 ``/api/*`` 和 ``/ws/*`` 请求。

```bash
# 生成 + 存储新的 host_token。
kt admin set-host-token
# 轮换已有的令牌（效果相同；别名）：
kt admin rotate-host-token
```

令牌写入 ``<config_dir>/config.toml``。客户端通过以下方式提供：

- HTTP：``Authorization: Bearer <token>``
- WebSocket：``Sec-WebSocket-Protocol: kt-token.<token>``（首选，
  不会泄漏到代理日志中）**或** ``?token=<token>`` 查询字符串
  （便于 curl 使用的回退方案）。

**Loopback 旁路。** 默认情况下，来自 ``127.0.0.1`` / ``::1`` 的请求
跳过 L2（``loopback_bypass = true``）。这让桌面应用使用顺畅 — 你的
应用不需要知道令牌就能与自己捆绑的主机通信。在反向代理后的生产部署
中禁用此功能：

```toml
[auth]
host_token = "..."
loopback_bypass = false
```

**CORS 预检。** ``OPTIONS`` 请求无条件通过 L2，使跨域浏览器能够在
发送实际（已认证）请求之前完成预检。

## 第 3 层 — 管理员令牌

第二个共享密钥**仅**控制配置修改路由 — 添加 LLM 密钥、注册 MCP
服务器、安装软件包、编辑模型 / 配置文件。读取访问和对话使用不被控制。

```bash
kt admin set-admin-token
```

客户端在相关路由上通过 ``X-Admin-Token: <token>`` 提供。来自 L3
的 401 响应携带 ``X-Auth-Required: admin``，前端据此区分"需要管理员
密码"和"需要登录"。

这就是**家庭服务器**模式：拥有主机令牌的人可以对话 / 读取配置；
只有持有管理员令牌的运营者可以修改 LLM 配置文件或安装软件包。

## 第 4 层 — 用户账户

带隔离会话、UI 偏好和 API 令牌的按用户账户。

```toml
[auth]
multi_user = "required"           # 每个 /api/* 都需要登录
registration = "invite_only"
```

三种注册模式：

| 模式 | 自助注册 | 新用户来自 |
|---|---|---|
| ``open`` | 是 | ``POST /api/auth/register``（任何人） |
| ``invite_only`` | 是，需令牌 | 管理员通过 ``kt admin invitations create`` 生成一次性邀请 |
| ``admin_only`` | 否 | 管理员运行 ``kt admin users add <username>`` |

**身份验证形态：**

- **Web（同源）**：``POST /api/auth/login`` 设置 ``HttpOnly`` +
  ``SameSite=Lax`` Cookie。浏览器在每个后续请求中发送它。
- **打包应用 / CLI / 跨源 Web**：``POST /api/auth/tokens`` 生成长期
  API 令牌（创建时仅显示一次）。客户端通过
  ``Authorization: Bearer <token>`` 提供。

**按用户数据布局：**

```
<config_dir>/
├── auth.db
├── api_keys.yaml          # 共享 — 管理员维护
├── llm_profiles.yaml      # 共享 — 管理员维护
├── mcp_servers.yaml       # 共享 — 管理员维护
└── users/
    └── <user_id>/
        ├── ui_prefs.json
        └── sessions/
            └── <session-name>.kohakutr
```

LLM 密钥、配置文件和 MCP 服务器是**共享的**（管理员只需要管理一次）。
会话和 UI 偏好是**按用户**的。

## 引导配方

新搭建家庭服务器：

```bash
# 1. 生成身份验证密钥。
kt admin set-host-token
kt admin set-admin-token

# 2. 编辑 <config_dir>/config.toml 设置 multi_user + registration：
#    [auth]
#    multi_user = "required"
#    registration = "invite_only"

# 3. 创建第一个管理员用户（交互式密码提示）。
kt admin users add operator --role admin

# 4. 为家庭成员生成邀请。
kt admin invitations create --role user --expires-in-hours 168

# 5. 启动服务器。
kt serve start --host 0.0.0.0
```

通过你选择的渠道把邀请令牌发给每位家庭成员；他们使用令牌调用
``POST /api/auth/register`` 创建账户。

## 把现有单用户主机迁移到多用户

当你在原本单用户的主机上启用 L4 时，现有的 ``ui_prefs.json`` 和
``sessions/`` **不会**被自动移动。请显式声明它们的归属：

```bash
# 把共享状态的 UI 偏好 + .kohakutr 会话移到某用户的命名空间。
kt admin migrate --from-shared-state --to-user operator
```

这是有意为之 — 多用户升级中的自动迁移可能把某人的会话移到错误的
命名空间。

## 加密原语

| 用途 | 原语 |
|---|---|
| 密码哈希 | bcrypt（成本因子可配置；默认 12） |
| API 令牌哈希 | SHA3-512（单向、快速查找） |
| 会话 ID 生成 | ``secrets.token_urlsafe(32)``（256 位） |
| 令牌 / 管理员比较 | ``secrets.compare_digest``（常量时间） |

API 令牌和邀请由 CSPRNG 生成并以哈希形式存储 — DB 泄漏无法被重放。

## 威胁模型

| 威胁 | 防御层 |
|---|---|
| 局域网邻居扫描端口并调用 API | L2 |
| 室友使用共享主机改你的 LLM 配置文件 | L3 或 L4 |
| 两位家庭成员的对话历史互相污染 | L4 |
| URL 中的令牌通过 referer / 代理日志泄漏 | WS 子协议认证 |

身份验证**不**防御：

- 拥有 ``<config_dir>/`` shell 访问权限的用户 — 他们能读取 ``auth.db``
  （bcrypt 哈希；离线破解是标准成本）以及每位用户的会话文件。身份
  验证是 API 边界，不是操作系统边界。
- 侧信道攻击（时序、流量分析） — 不在范围内。

## TLS

框架在 1.5.0 中**不**自己终止 TLS。运营者在前面放置反向代理
（Caddy / nginx / Traefik）来处理 HTTPS。参见
[部署 — 反向代理](deployment-reverse-proxy.md)。当 ``auth`` 启用
但主机 URL 为明文 ``http://`` 且非 loopback 时，前端会显示横幅
*"连接未加密"*。

## 参见

- [部署 — Docker](deployment-docker.md) — 通过 ``secrets:`` 挂载
  传递 ``[auth]`` 配置
- [部署 — systemd](deployment-systemd.md) — 通过 ``LoadCredential=``
  指令传递 ``[auth]`` 配置
- [部署 — 反向代理](deployment-reverse-proxy.md) — TLS 终止 +
  CORS 白名单用于托管的静态前端
