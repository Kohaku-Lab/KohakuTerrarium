---
title: Deployment — systemd
summary: Install KohakuTerrarium as a systemd service via the bundled `kt service install` command.
tags:
  - guides
  - deployment
  - systemd
  - linux
---

# Deployment — systemd

For Linux hosts where Docker is overkill, KohakuTerrarium ships
ready-to-install systemd units. The `kt service` subcommand renders
unit files from packaged templates, installs them into
`/etc/systemd/system/`, optionally enables them, and reloads
systemd.

## Prerequisites

- Linux (any distro using systemd ≥ 240 — Ubuntu 20.04+, Debian 11+,
  Fedora 36+, RHEL 9+ all work).
- A Python ≥ 3.10 install with the `kohakuterrarium` package and
  the `kt` / `kt-aio` console scripts on `PATH`. Install the wheel
  system-wide or into a venv on `PATH`:

  ```bash
  sudo pip install --break-system-packages kohakuterrarium==1.5.0
  # OR
  python3 -m venv /opt/kohakuterrarium
  /opt/kohakuterrarium/bin/pip install kohakuterrarium==1.5.0
  sudo ln -s /opt/kohakuterrarium/bin/kt /usr/local/bin/kt
  sudo ln -s /opt/kohakuterrarium/bin/kt-aio /usr/local/bin/kt-aio
  ```

- Root, for the install / uninstall paths (they write to
  `/etc/systemd/system/` and `/etc/kohakuterrarium/`).

## Three shapes, mirroring the Docker guide

The same three deployment shapes apply: AIO, host + workers, or
distributed. Pick one.

### Shape 1 — AIO

One service running `kt-aio` — equivalent to the AIO Docker image:

```bash
sudo kt service install --all \
  --home-dir /var/lib/kohakuterrarium \
  --host-token "$(openssl rand -hex 24)"
sudo systemctl enable --now kohakuterrarium-all.service
sudo systemctl status kohakuterrarium-all.service
```

The installer writes:

- `/etc/systemd/system/kohakuterrarium-all.service` — the unit
- `/etc/kohakuterrarium/all.env` — `KT_HOST_TOKEN` + `KT_CONFIG_DIR`

Both are owned by root mode `0600` — the token never appears in
process arguments, only in the protected `EnvironmentFile`.

Curl the health endpoint to confirm:

```bash
curl http://localhost:8001/healthz
```

### Shape 2 — host + N workers on the same box

Install the host unit once, then install one client instance per
worker. The client unit is an instance template (`@.service`) — one
template, many instances.

```bash
# 1. Install the host unit.
sudo kt service install --host \
  --home-dir /var/lib/kohakuterrarium/host \
  --host-token "$(openssl rand -hex 24)"
sudo systemctl enable --now kohakuterrarium-host.service

# 2. Note the token the installer used (also in /etc/kohakuterrarium/host.env).
HOST_TOKEN=$(sudo grep KT_HOST_TOKEN /etc/kohakuterrarium/host.env | cut -d= -f2-)

# 3. Install two worker instances.
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

The installer writes:

- `/etc/systemd/system/kohakuterrarium-host.service`
- `/etc/systemd/system/kohakuterrarium-client@.service` (template)
- `/etc/kohakuterrarium/host.env`
- `/etc/kohakuterrarium/client.env` — shared (URL + token)
- `/etc/kohakuterrarium/client.worker-a.env` — per-instance
- `/etc/kohakuterrarium/client.worker-b.env` — per-instance

The shared `client.env` carries `KT_HOST_URL` + `KT_HOST_TOKEN`; the
per-instance file carries `KT_CLIENT_NAME` and any worker-specific
overrides.

### Shape 3 — distributed (host on edge VPS, workers elsewhere)

Same commands as Shape 2, on different boxes. On the host VPS,
install only the `--host` unit. On each worker box, install only
the `--client` instance with `--host-url wss://your-host/lab` and
the shared token.

Front the host's port `8001` (and `8100` if exposing the Lab WS
directly) with nginx — see the
[reverse-proxy guide](deployment-reverse-proxy.md).

## Hardening — what the templates already do

The shipped unit templates apply systemd best-practice hardening
out of the box:

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

`DynamicUser=yes` allocates a transient UID at start; `StateDirectory`
becomes the user's writable home (`/var/lib/kohakuterrarium-host`).
Combined with `ProtectSystem=strict`, the service has no filesystem
write access beyond its own state directory — even if compromised,
it cannot tamper with the rest of the system.

If you need to deviate (e.g., add `ReadWritePaths=` for a
shared dataset directory), use `sudo systemctl edit
kohakuterrarium-host.service` — never modify the file the installer
wrote, since the next `kt service install --host` would overwrite
your changes.

## Customising rendered units

The installer accepts `--no-install` for dry-run rendering:

```bash
kt service install --host --no-install \
  --home-dir /var/lib/kohakuterrarium \
  --host-token TOKEN \
  --output ./kohakuterrarium-host.service
cat ./kohakuterrarium-host.service
```

Use this to review what the installer would write, copy the file
into your own configuration-management repo, and apply it via
Ansible / Chef / Salt instead of running the installer on each box.

## Status + logs

```bash
sudo systemctl status kohakuterrarium-host.service
sudo journalctl -u kohakuterrarium-host.service -f --output cat
```

The `--output cat` flag drops systemd's per-line metadata so the
KohakuTerrarium logs render as-is. The
[token-masking filter](../reference/cli.md) redacts any
`?token=...` query strings and JSON `"token"` keys before they hit
the journal — so `journalctl` is safe to share when troubleshooting.

## Uninstall

```bash
# Stop and disable, then remove.
sudo systemctl disable --now kohakuterrarium-client@worker-b.service
sudo systemctl disable --now kohakuterrarium-client@worker-a.service
sudo systemctl disable --now kohakuterrarium-host.service
sudo kt service uninstall --client --name worker-b
sudo kt service uninstall --client --name worker-a
sudo kt service uninstall --host
```

The client-instance uninstall is name-aware: removing the *last*
instance also removes the `@.service` template; uninstalling
intermediate instances leaves the template in place so other
workers keep running.

## Troubleshooting

- **Unit fails to start with "executable not found"** → `kt` /
  `kt-aio` aren't on root's `PATH`. Either install system-wide, or
  symlink them into `/usr/local/bin/` as shown above. The
  installer resolves the absolute path of `kt` / `kt-aio` at install
  time, so a venv on `PATH` for the installer user works too.
- **`/healthz` 200 but `/readyz` 503 for >30s** → the Lab transport
  did not bind. Check `journalctl -u kohakuterrarium-host -e` for
  `address already in use` — port `8100` may be taken.
- **Worker instance won't connect** → check the per-instance env
  file: `sudo cat /etc/kohakuterrarium/client.<name>.env`. The
  token and URL must match the host's.

## Locking down the API — `[auth]` via systemd credentials

Any host unit becomes a locked-down host by installing the
auth-secrets drop-in shipped under `packaging/systemd/`:

```bash
sudo mkdir -p /etc/systemd/system/kohakuterrarium-host.service.d
sudo cp packaging/systemd/auth-secrets.example.conf \
    /etc/systemd/system/kohakuterrarium-host.service.d/auth.conf

# Provision credential files (root-owned, mode 0400).
sudo mkdir -p /etc/kohakuterrarium/credentials
python -c "import secrets;print(secrets.token_hex(32))" | \
    sudo install -m 0400 /dev/stdin /etc/kohakuterrarium/credentials/host_token
python -c "import secrets;print(secrets.token_hex(32))" | \
    sudo install -m 0400 /dev/stdin /etc/kohakuterrarium/credentials/admin_token

sudo systemctl daemon-reload
sudo systemctl restart kohakuterrarium-host

# Create the first admin (interactive password prompt).
sudo -u kohakuterrarium-host kt admin users add operator --role admin
```

The drop-in uses systemd's ``LoadCredential=`` directive — secrets
are read into the unit's runtime credential directory (``%d/...``)
and exposed via ``KT_AUTH_*_FILE`` env vars.  They never appear in
``/proc/<pid>/environ``.

See [Authentication](authentication.md) for the full four-layer
model + every ``kt admin`` verb.

## See also

- [Authentication](authentication.md) — the four-layer auth model
  + ``kt admin`` operator surface.
- [Deployment — Docker](deployment-docker.md) — the containerised
  equivalent of these three shapes.
- [Deployment — reverse proxy](deployment-reverse-proxy.md) — TLS
  termination in front of `8001` / `8100`.
- [Laboratory](laboratory.md) — what the lab-host / lab-client roles
  do once the units are running.
