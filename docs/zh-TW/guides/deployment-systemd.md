---
title: 部署 — systemd
summary: 透過內附的 `kt service install` 指令把 KohakuTerrarium 安裝為 systemd 服務。
tags:
  - guides
  - deployment
  - systemd
  - linux
---

# 部署 — systemd

對於不需要 Docker 的 Linux 主機,KohakuTerrarium 內附可立即使用的 systemd unit。`kt service` 子指令會依據打包的範本渲染 unit 檔、安裝到 `/etc/systemd/system/`、選擇性啟用,並 reload systemd。

## 前置條件

- Linux（systemd ≥ 240 的任意發行版 — Ubuntu 20.04+、Debian 11+、Fedora 36+、RHEL 9+ 均可）。
- Python ≥ 3.10,並已安裝 `kohakuterrarium` 與 `kt` / `kt-aio` 指令腳本（在 `PATH` 中）：

  ```bash
  sudo pip install --break-system-packages kohakuterrarium==1.5.0
  # 或
  python3 -m venv /opt/kohakuterrarium
  /opt/kohakuterrarium/bin/pip install kohakuterrarium==1.5.0
  sudo ln -s /opt/kohakuterrarium/bin/kt /usr/local/bin/kt
  sudo ln -s /opt/kohakuterrarium/bin/kt-aio /usr/local/bin/kt-aio
  ```

- root 權限（install / uninstall 會寫入 `/etc/systemd/system/` 與 `/etc/kohakuterrarium/`）。

## 三種部署模式

同 Docker 指南：AIO、host + worker、分散式。任選其一。

### 模式 1 — AIO

一個跑 `kt-aio` 的服務 — 等價於 AIO Docker 映像：

```bash
sudo kt service install --all \
  --home-dir /var/lib/kohakuterrarium \
  --host-token "$(openssl rand -hex 24)"
sudo systemctl enable --now kohakuterrarium-all.service
sudo systemctl status kohakuterrarium-all.service
```

安裝器會寫入：

- `/etc/systemd/system/kohakuterrarium-all.service` — unit 檔
- `/etc/kohakuterrarium/all.env` — `KT_HOST_TOKEN` + `KT_CONFIG_DIR`

兩者均屬 root、權限 `0600` — token 不會出現在行程參數中,只存在於受保護的 `EnvironmentFile` 中。

確認健康端點：

```bash
curl http://localhost:8001/healthz
```

### 模式 2 — host + 同機 N 個 worker

先安裝一次 host unit,然後為每個 worker 安裝一個 client 實例。client unit 是 instance template（`@.service`）— 一份範本,多個實例。

```bash
# 1. 安裝 host unit
sudo kt service install --host \
  --home-dir /var/lib/kohakuterrarium/host \
  --host-token "$(openssl rand -hex 24)"
sudo systemctl enable --now kohakuterrarium-host.service

# 2. 讀取安裝器使用的 token(也在 /etc/kohakuterrarium/host.env)
HOST_TOKEN=$(sudo grep KT_HOST_TOKEN /etc/kohakuterrarium/host.env | cut -d= -f2-)

# 3. 安裝兩個 worker 實例
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

安裝器會寫入：

- `/etc/systemd/system/kohakuterrarium-host.service`
- `/etc/systemd/system/kohakuterrarium-client@.service`（範本）
- `/etc/kohakuterrarium/host.env`
- `/etc/kohakuterrarium/client.env` — 共享（URL + token）
- `/etc/kohakuterrarium/client.worker-a.env` — per-instance
- `/etc/kohakuterrarium/client.worker-b.env` — per-instance

共享 `client.env` 承載 `KT_HOST_URL` + `KT_HOST_TOKEN`;per-instance 檔案承載 `KT_CLIENT_NAME` 及任何 worker 專屬覆寫。

### 模式 3 — 分散式（host 在邊緣 VPS、worker 在他處）

指令同模式 2,只是部署在不同機器。host VPS 上僅安裝 `--host` unit;每個 worker 機器上僅安裝 `--client` 實例,並把 `--host-url wss://your-host/lab` 與共享 token 傳進去。

用 nginx 在 host 的 `8001`（與 `8100` 若直接對外）前面做反向代理 — 詳見[反向代理指南](deployment-reverse-proxy.md)。

## 強化 — 範本自帶設定

隨範本出貨的 unit 已套用 systemd 最佳實踐強化：

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

`DynamicUser=yes` 會在啟動時配發暫時 UID;`StateDirectory` 即是該 user 的可寫 home（`/var/lib/kohakuterrarium-host`）。配合 `ProtectSystem=strict`,該服務在其狀態目錄外沒有任何寫權限 — 即便被攻破,也無法竄改系統其餘部分。

如需偏離（例如為共用資料集目錄加 `ReadWritePaths=`),請用 `sudo systemctl edit kohakuterrarium-host.service` — 不要修改安裝器寫出的檔案,下次 `kt service install --host` 會覆寫你的修改。

## 自訂渲染

安裝器支援 `--no-install` 做 dry-run：

```bash
kt service install --host --no-install \
  --home-dir /var/lib/kohakuterrarium \
  --host-token TOKEN \
  --output ./kohakuterrarium-host.service
cat ./kohakuterrarium-host.service
```

用它稽核安裝器會寫什麼,再把檔案放進你自己的配置管理 repo 中,透過 Ansible / Chef / Salt 散佈,避免在每台機器跑安裝器。

## 狀態 + log

```bash
sudo systemctl status kohakuterrarium-host.service
sudo journalctl -u kohakuterrarium-host.service -f --output cat
```

`--output cat` 會剝掉 systemd 的逐行 metadata,讓 KohakuTerrarium 的 log 原樣輸出。[token 遮罩過濾器](../reference/cli.md)會在內容進 journal 前將 `?token=...` query 字串與 JSON `"token"` 鍵替換為遮罩 — 因此 `journalctl` 可直接分享給協助排查的人。

## 解除安裝

```bash
# 停用並解除,然後移除
sudo systemctl disable --now kohakuterrarium-client@worker-b.service
sudo systemctl disable --now kohakuterrarium-client@worker-a.service
sudo systemctl disable --now kohakuterrarium-host.service
sudo kt service uninstall --client --name worker-b
sudo kt service uninstall --client --name worker-a
sudo kt service uninstall --host
```

client-instance 解除安裝會感知實例名稱：移除 *最後一個* 實例時也會移除 `@.service` 範本;移除中間實例則保留範本,其他 worker 不受影響。

## 故障排除

- **unit 啟動報「executable not found」** → `kt` / `kt-aio` 不在 root 的 `PATH` 上。要麼系統級安裝,要麼依上文建立 `/usr/local/bin/` 連結。安裝器在 install 時解析 `kt` / `kt-aio` 的絕對路徑,所以 venv-on-PATH 的安裝使用者也可用。
- **`/healthz` 200 但 `/readyz` 503 超過 30s** → Lab 傳輸沒綁定。`journalctl -u kohakuterrarium-host -e` 看是否出現 `address already in use` — 連接埠 `8100` 可能被佔用。
- **worker 實例連不上** → 查 per-instance env 檔案：`sudo cat /etc/kohakuterrarium/client.<name>.env`。token 與 URL 必須與 host 一致。

## 鎖定 API — 透過 systemd credentials 啟用 `[auth]`

透過 `packaging/systemd/` 提供的 auth-secrets drop-in,任何 host
unit 都可以變成加鎖主機:

```bash
sudo mkdir -p /etc/systemd/system/kohakuterrarium-host.service.d
sudo cp packaging/systemd/auth-secrets.example.conf \
    /etc/systemd/system/kohakuterrarium-host.service.d/auth.conf

# 提供 credential 檔案(root 所有,權限 0400)。
sudo mkdir -p /etc/kohakuterrarium/credentials
python -c "import secrets;print(secrets.token_hex(32))" | \
    sudo install -m 0400 /dev/stdin /etc/kohakuterrarium/credentials/host_token
python -c "import secrets;print(secrets.token_hex(32))" | \
    sudo install -m 0400 /dev/stdin /etc/kohakuterrarium/credentials/admin_token

sudo systemctl daemon-reload
sudo systemctl restart kohakuterrarium-host

# 建立第一個管理員(互動式密碼提示)。
sudo -u kohakuterrarium-host kt admin users add operator --role admin
```

drop-in 使用 systemd 的 ``LoadCredential=`` 指令 — 密鑰讀入 unit
執行時憑證目錄(``%d/...``)並透過 ``KT_AUTH_*_FILE`` 環境變數暴露。
它們永遠不會出現在 ``/proc/<pid>/environ`` 中。

完整的四層模型以及每個 ``kt admin`` 動詞參見
[身份驗證](authentication.md)。

## 另請參閱

- [身份驗證](authentication.md) — 四層認證模型 + ``kt admin`` 營運者命令。
- [部署 — Docker](deployment-docker.md) — 三種模式的容器化版本。
- [部署 — 反向代理](deployment-reverse-proxy.md) — 在 `8001` / `8100` 前做 TLS 終止。
- [Laboratory](laboratory.md) — unit 跑起來後,lab-host / lab-client 角色實際做什麼。
