---
title: 部署 — systemd
summary: 通过自带的 `kt service install` 命令将 KohakuTerrarium 安装为 systemd 服务。
tags:
  - guides
  - deployment
  - systemd
  - linux
---

# 部署 — systemd

对于不需要 Docker 的 Linux 主机，KohakuTerrarium 自带可立即使用的 systemd unit。`kt service` 子命令会根据打包的模板渲染 unit 文件、安装到 `/etc/systemd/system/`、可选地启用，并 reload systemd。

## 前置条件

- Linux（systemd ≥ 240 的任意发行版 — Ubuntu 20.04+、Debian 11+、Fedora 36+、RHEL 9+ 均可）。
- Python ≥ 3.10，并已安装 `kohakuterrarium` 与 `kt` / `kt-aio` 命令脚本（在 `PATH` 中）：

  ```bash
  sudo pip install --break-system-packages kohakuterrarium==1.5.0
  # 或
  python3 -m venv /opt/kohakuterrarium
  /opt/kohakuterrarium/bin/pip install kohakuterrarium==1.5.0
  sudo ln -s /opt/kohakuterrarium/bin/kt /usr/local/bin/kt
  sudo ln -s /opt/kohakuterrarium/bin/kt-aio /usr/local/bin/kt-aio
  ```

- root 权限（install / uninstall 会写入 `/etc/systemd/system/` 与 `/etc/kohakuterrarium/`）。

## 三种部署模式

同 Docker 指南：AIO、host + worker、分布式。任选其一。

### 模式 1 — AIO

一个跑 `kt-aio` 的服务 — 等价于 AIO Docker 镜像：

```bash
sudo kt service install --all \
  --home-dir /var/lib/kohakuterrarium \
  --host-token "$(openssl rand -hex 24)"
sudo systemctl enable --now kohakuterrarium-all.service
sudo systemctl status kohakuterrarium-all.service
```

安装器会写入：

- `/etc/systemd/system/kohakuterrarium-all.service` — unit 文件
- `/etc/kohakuterrarium/all.env` — `KT_HOST_TOKEN` + `KT_CONFIG_DIR`

两者均归 root、权限 `0600` — token 不会出现在进程参数中，只存在于受保护的 `EnvironmentFile` 中。

确认健康端点：

```bash
curl http://localhost:8001/healthz
```

### 模式 2 — host + 同机 N 个 worker

先安装一次 host unit，然后为每个 worker 安装一个 client 实例。client unit 是 instance template (`@.service`) — 一份模板，多个实例。

```bash
# 1. 安装 host unit
sudo kt service install --host \
  --home-dir /var/lib/kohakuterrarium/host \
  --host-token "$(openssl rand -hex 24)"
sudo systemctl enable --now kohakuterrarium-host.service

# 2. 读取安装器使用的 token（也在 /etc/kohakuterrarium/host.env 中）
HOST_TOKEN=$(sudo grep KT_HOST_TOKEN /etc/kohakuterrarium/host.env | cut -d= -f2-)

# 3. 安装两个 worker 实例
sudo kt service install --client \
  --home-dir-base /var/lib/kohakuterrarium/workers \
  --host-url ws://127.0.0.1:8100 \
  --host-token "$HOST_TOKEN" \
  --name worker-a
sudo systemctl enable --now kohakuterrarium-client@worker-a.service

sudo kt service install --client \
  --home-dir-base /var/lib/kohakuterrarium/workers \
  --host-url ws://127.0.0.1:8100 \
  --host-token "$HOST_TOKEN" \
  --name worker-b
sudo systemctl enable --now kohakuterrarium-client@worker-b.service
```

安装器会写入：

- `/etc/systemd/system/kohakuterrarium-host.service`
- `/etc/systemd/system/kohakuterrarium-client@.service`（模板）
- `/etc/kohakuterrarium/host.env`
- `/etc/kohakuterrarium/client.env` — 共享（URL + token）
- `/etc/kohakuterrarium/client.worker-a.env` — per-instance
- `/etc/kohakuterrarium/client.worker-b.env` — per-instance

共享 `client.env` 承载 `KT_HOST_URL` + `KT_HOST_TOKEN`；per-instance 文件承载 `KT_CLIENT_NAME` 及任何 worker 专属覆盖。

### 模式 3 — 分布式（host 在边缘 VPS，worker 在别处）

命令与模式 2 相同，只是部署在不同机器上。host VPS 上仅安装 `--host` unit；每个 worker 机器上仅安装 `--client` 实例，并把 `--host-url wss://your-host/lab` 与共享 token 传进去。

用 nginx 在 host 的 `8001`（与 `8100` 如果直接对外）前面做反向代理 — 详见[反向代理指南](deployment-reverse-proxy.md)。

## 加固 — 模板自带的设置

随模板出货的 unit 已应用 systemd 最佳实践：

```ini
[Service]
Type=simple
DynamicUser=yes
StateDirectory=kohakuterrarium-host
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictRealtime=yes
LockPersonality=yes
MemoryDenyWriteExecute=yes
NoNewPrivileges=yes
SystemCallArchitectures=native
```

`DynamicUser=yes` 会在启动时分配临时 UID；`StateDirectory` 即是该 user 的可写 home（`/var/lib/kohakuterrarium-host`）。与 `ProtectSystem=strict` 配合，该服务在其状态目录之外没有任何写权限 — 即便被攻破，也无法篡改系统其余部分。

如需调整（例如为共享数据集目录加 `ReadWritePaths=`），请用 `sudo systemctl edit kohakuterrarium-host.service` — 不要修改安装器写出的文件，否则下次 `kt service install --host` 会覆盖你的修改。

## 自定义渲染

安装器支持 `--no-install` 做 dry-run：

```bash
kt service install --host --no-install \
  --home-dir /var/lib/kohakuterrarium \
  --host-token TOKEN \
  --output ./kohakuterrarium-host.service
cat ./kohakuterrarium-host.service
```

用它审计安装器会写什么，再把文件提交到你自己的配置管理 repo 里，通过 Ansible / Chef / Salt 分发，避免在每台机器跑安装器。

## 状态 + 日志

```bash
sudo systemctl status kohakuterrarium-host.service
sudo journalctl -u kohakuterrarium-host.service -f --output cat
```

`--output cat` 会去掉 systemd 的逐行元数据，让 KohakuTerrarium 的日志原样输出。[token 屏蔽过滤器](../reference/cli.md)会在内容进 journal 前将 `?token=...` query 串与 JSON `"token"` key 替换为遮罩 — 因此 `journalctl` 可以直接分享给协助排查的人。

## 卸载

```bash
# 停用并禁用，然后移除
sudo systemctl disable --now kohakuterrarium-client@worker-b.service
sudo systemctl disable --now kohakuterrarium-client@worker-a.service
sudo systemctl disable --now kohakuterrarium-host.service
sudo kt service uninstall --client --name worker-b
sudo kt service uninstall --client --name worker-a
sudo kt service uninstall --host
```

client-instance 卸载会感知实例名：移除 *最后一个* 实例时也会移除 `@.service` 模板；移除中间实例则保留模板，其他 worker 不受影响。

## 故障排查

- **unit 启动报 "executable not found"** → `kt` / `kt-aio` 不在 root 的 `PATH` 上。要么系统级安装，要么按上文创建 `/usr/local/bin/` 软链接。安装器在 install 时解析 `kt` / `kt-aio` 的绝对路径，所以 venv-on-PATH 的安装用户也可用。
- **`/healthz` 200 但 `/readyz` 503 超过 30s** → Lab 传输没绑定。`journalctl -u kohakuterrarium-host -e` 看是否出现 `address already in use` — 端口 `8100` 可能被占用。
- **worker 实例连不上** → 查 per-instance env 文件：`sudo cat /etc/kohakuterrarium/client.<name>.env`。token 与 URL 必须与 host 一致。

## 锁定 API — 通过 systemd credentials 启用 `[auth]`

通过 `packaging/systemd/` 提供的 auth-secrets drop-in，任何 host
unit 都可以变成加锁主机：

```bash
sudo mkdir -p /etc/systemd/system/kohakuterrarium-host.service.d
sudo cp packaging/systemd/auth-secrets.example.conf \
    /etc/systemd/system/kohakuterrarium-host.service.d/auth.conf

# 提供 credential 文件（root 所有，权限 0400）。
sudo mkdir -p /etc/kohakuterrarium/credentials
python -c "import secrets;print(secrets.token_hex(32))" | \
    sudo install -m 0400 /dev/stdin /etc/kohakuterrarium/credentials/host_token
python -c "import secrets;print(secrets.token_hex(32))" | \
    sudo install -m 0400 /dev/stdin /etc/kohakuterrarium/credentials/admin_token

sudo systemctl daemon-reload
sudo systemctl restart kohakuterrarium-host

# 创建第一个管理员（交互式密码提示）。
sudo -u kohakuterrarium-host kt admin users add operator --role admin
```

drop-in 使用 systemd 的 ``LoadCredential=`` 指令 — 密钥读入 unit
运行时凭据目录（``%d/...``）并通过 ``KT_AUTH_*_FILE`` 环境变量暴露。
它们永远不会出现在 ``/proc/<pid>/environ`` 中。

完整的四层模型以及每个 ``kt admin`` 动词参见
[身份验证](authentication.md)。

## 另请参阅

- [身份验证](authentication.md) — 四层认证模型 + ``kt admin`` 运营者命令。
- [部署 — Docker](deployment-docker.md) — 三种模式的容器化版本。
- [部署 — 反向代理](deployment-reverse-proxy.md) — 在 `8001` / `8100` 前做 TLS 终止。
- [Laboratory](laboratory.md) — unit 运行起来后，lab-host / lab-client 角色实际做什么。
