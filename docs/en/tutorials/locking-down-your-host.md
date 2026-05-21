---
title: Locking down your host
summary: Add auth to your KohakuTerrarium host step-by-step — from "anyone on my LAN can use it" to "my family logs in, only I can change settings."
tags:
  - tutorials
  - auth
  - deployment
---

# Locking down your host

**Problem:** you started running `kt serve` and now anyone on your
LAN can hit `http://your-ip:8001` and chat with your LLM (= burn
your API keys).

**End state:** four ascending levels of lockdown, each one a 30-second
copy-paste away.  You pick the level that matches your situation.

**Prerequisites:** `kt` installed, can run `kt serve start` already.

If you just want the reference docs (four-layer model, threat model,
cryptography), see [Authentication](../guides/authentication.md).
This tutorial skips the theory and shows the commands.

---

## Pick your level

| You want | Your level |
|---|---|
| Desktop app on my own machine — no setup, no nagging | **Level 0** (default — do nothing) |
| Only people who know a shared password can connect | **Level 1** — host token |
| Friends can chat / use the host; only I can change LLM keys + install packages | **Level 2** — admin password |
| Each family member has their own login + isolated chat sessions | **Level 3** — multi-user |

Each level builds on the previous.  Stop wherever your need is met.

---

## Level 0 — Desktop app, defaults are fine

**Do nothing.**  The desktop app binds to `127.0.0.1`; nothing on the
network can reach it.  Your OS user is the trust boundary.

Test:

```bash
kt app                    # opens the desktop window
# from another machine on your LAN:
curl http://YOUR_LAN_IP:8001/api/auth/capabilities
# → connection refused (the desktop never bound to LAN)
```

If you ever start running `kt serve --host 0.0.0.0`, jump to Level 1.

---

## Level 1 — Host token (5 minutes)

The host now requires `Authorization: Bearer <token>` on every API
call.  Loopback (`127.0.0.1`) still bypasses by default so the
desktop app keeps working without typing the token.

### Step 1 — generate the token

```bash
kt admin set-host-token
# host_token saved (length 64 chars).
# written to: /home/you/.kohakuterrarium/config.toml
```

This generates 32 random bytes and writes them into `[auth]
host_token` in `config.toml`.

### Step 2 — restart the server

```bash
kt serve restart
# (or just kt serve start --host 0.0.0.0 if not running yet)
```

### Step 3 — verify

From another machine:

```bash
curl http://YOUR_LAN_IP:8001/api/version
# → 401 Unauthorized
```

With the right token:

```bash
TOKEN=$(kt admin show-host-token --yes)
curl -H "Authorization: Bearer $TOKEN" http://YOUR_LAN_IP:8001/api/version
# → 200 OK
```

### Step 4 — give friends the token

Share `$TOKEN` over a secure channel (Signal / 1Password share / not
WhatsApp).  Anyone with the token can connect via the web frontend
or `curl`.

### Disable loopback bypass (production behind a reverse proxy)

If you're behind nginx / Caddy and your "loopback" traffic is
actually proxied from the internet, edit `~/.kohakuterrarium/config.toml`:

```toml
[auth]
host_token = "..."          # already there
loopback_bypass = false     # add this
```

Restart.  Now even `127.0.0.1` requires the token.

### Rotate the token if it leaks

```bash
kt admin rotate-host-token   # generates a new one
kt serve restart             # existing clients drop, need the new token
```

---

## Level 2 — Admin password (adds another 5 minutes)

Friends with the host token can now chat — but they can also click
"Save" on the Models page and burn through your OpenAI key.  Add a
second password for config changes.

### Step 1 — generate the admin token

```bash
kt admin set-admin-token
# admin_token saved (length 64 chars).
```

Restart the server.

### Step 2 — what's now gated

These routes refuse without `X-Admin-Token: <admin_token>`:

- `POST /api/settings/keys` — adding / changing LLM API keys
- `POST /api/settings/profiles` — LLM model profiles
- `POST /api/settings/mcp` — MCP server registration
- `POST /api/registry/install` — installing packages
- `PUT /api/settings/config-files/{name}/content` — raw config edits

These keep working without it (read-only / chat / sessions are
unaffected):

- Anything under `/api/auth/capabilities`, `/me`, `/sessions/*`, chat WS

### Step 3 — verify

```bash
HOST=$(kt admin show-host-token --yes)
ADMIN=$(... save it from set-admin-token output)

# Friend trying to set a key → 401
curl -X POST http://localhost:8001/api/settings/keys \
  -H "Authorization: Bearer $HOST" \
  -H "Content-Type: application/json" \
  -d '{"provider":"openai","key":"sk-..."}'
# → 401 {"detail": {"error": "admin_required", ...}}

# You with the admin token → 200
curl -X POST http://localhost:8001/api/settings/keys \
  -H "Authorization: Bearer $HOST" \
  -H "X-Admin-Token: $ADMIN" \
  -H "Content-Type: application/json" \
  -d '{"provider":"openai","key":"sk-..."}'
```

### Step 4 — share the host token, keep the admin token

Give friends `$HOST`.  Don't give them `$ADMIN`.  When the frontend
asks them to log in they paste the host token; when they try to
edit config, the UI greys out and (when the Vue admin UI lands)
prompts for the admin password — only you have it.

---

## Level 3 — Multi-user (adds another 10 minutes)

Now each person logs in with their own username + password.  Their
chat sessions, tabs, and UI prefs are scoped to their account.
Shared resources (LLM keys, profiles, MCP servers, installed
packages) stay shared because the admin manages them once.

### Step 1 — edit config.toml

```toml
[auth]
host_token = "..."              # already set
admin_token = "..."             # already set
multi_user = "required"         # ← new
registration = "invite_only"    # ← new (or "admin_only" / "open")
loopback_bypass = false         # turn off if behind a proxy
```

Restart the server.

### Step 2 — create the first admin user

```bash
kt admin users add operator --role admin
# Password: ************
# Confirm password: ************
# user created: id=1 username=operator role=admin
```

This writes to `~/.kohakuterrarium/auth.db` (sqlite, bcrypt-hashed).

### Step 3 — invite family members

Generate an invitation token per person:

```bash
kt admin invitations create --role user --expires-in-hours 168
# invitation created (id=1, role=user):
#   token: 9f3a8b7e2c1d4f5a6b9c8e7d2f1a4b5c6d7e8f9a0b1c2d3e4f5a6b7c8d9e0f1a
#   expires_at: 2026-05-29T12:00:00+00:00
```

Send each token over a secure channel.  Each token is single-use,
optionally time-bounded.

### Step 4 — family members register

Each person POSTs once with their invite token:

```bash
curl -X POST http://YOUR_HOST:8001/api/auth/register \
  -H "Authorization: Bearer $HOST" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "alice",
    "password": "their-chosen-password",
    "invitation_token": "9f3a8b..."
  }'
```

Response sets the session cookie + returns user info.  From then on
they log in with username + password:

```bash
curl -X POST http://YOUR_HOST:8001/api/auth/login \
  -H "Authorization: Bearer $HOST" \
  -H "Content-Type: application/json" \
  -d '{"username":"alice","password":"their-chosen-password"}' \
  -c alice-cookies.txt
```

### Step 5 — verify isolation

Each user gets their own slice of disk:

```
~/.kohakuterrarium/
├── auth.db
├── api_keys.yaml           # shared — admin sees + manages
├── llm_profiles.yaml       # shared
├── mcp_servers.yaml        # shared
└── users/
    ├── 1/                  # operator
    │   ├── ui_prefs.json
    │   └── sessions/
    │       └── *.kohakutr  # operator's chats
    └── 2/                  # alice
        ├── ui_prefs.json
        └── sessions/
            └── *.kohakutr  # alice's chats — operator can't see them
```

### Migrating existing sessions to your user namespace

If you've been using the host single-user and want to keep your
existing chats under your new account:

```bash
kt admin migrate --from-shared-state --to-user operator --dry-run
# (shows what would move; safe — no changes)

kt admin migrate --from-shared-state --to-user operator
# moves <config_dir>/ui_prefs.json + <config_dir>/sessions/*.kohakutr
# into users/<operator-id>/
```

Run this once before any other user starts using the host so their
empty namespace isn't accidentally seeded with your data.

---

## Common operations

### Disable a user (e.g., their kid found the password)

```bash
kt admin users disable alice
# user 'alice' disabled
#   (dropped 2 active session(s))
```

Their sessions are revoked immediately; future logins fail until you
re-enable with `kt admin users enable alice`.

### Delete a user

```bash
kt admin users delete alice --yes
# user 'alice' deleted (id=2).
# note: per-user dir users/2/ kept (rm -rf to discard the user's sessions / prefs).
```

The disk directory is intentionally NOT deleted — `rm -rf` it
yourself if you want their data gone.

### Promote / demote admins

```bash
kt admin users grant alice    # alice → admin
kt admin users demote alice   # alice → user
```

The CLI refuses to demote / disable the last active admin so you
can't lock yourself out.

### List who's on the host

```bash
kt admin users list
# ID    USERNAME                  ROLE      ACTIVE    LAST_LOGIN
# ----------------------------------------------------------------------
# 1     operator                  admin     yes       2026-05-22T14:32:01+00:00
# 2     alice                     user      yes       2026-05-22T13:50:11+00:00
# 3     bob                       user      no        -
```

### Reset a password (admin)

There's no "reset" verb — admin re-issues the user via the API:

```bash
# kt admin doesn't have password-reset yet; for now, the admin
# uses the auth API directly with their own session:
curl -X PATCH http://localhost:8001/api/auth/users/2 \
  -H "Authorization: Bearer $HOST" \
  -H "X-Admin-Token: $ADMIN" \
  -c admin-cookies.txt \
  -b admin-cookies.txt \
  -d '{"is_active": false}'
# Then delete + recreate, or wait for a future kt admin users reset-password.
```

A `kt admin users reset-password` verb is on the roadmap.

---

## Deployment-specific recipes

The four levels above are the same logical setup; how you provision
the tokens differs by deployment.

### Docker compose — secrets via files

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
      - "127.0.0.1:8001:8001"   # reverse proxy in front for TLS
secrets:
  host_token:  { file: ./secrets/host_token }
  admin_token: { file: ./secrets/admin_token }
volumes:
  kt-config:
```

Provision the secret files once:

```bash
mkdir -p secrets
python -c "import secrets;print(secrets.token_hex(32))" > secrets/host_token
python -c "import secrets;print(secrets.token_hex(32))" > secrets/admin_token
chmod 600 secrets/*
docker compose up -d
docker compose exec kohakuterrarium kt admin users add operator --role admin
```

See [Deployment — Docker](../guides/deployment-docker.md) for the
full Compose example.

### systemd — secrets via `LoadCredential=`

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

The drop-in uses `LoadCredential=` so secrets never appear in
`/proc/<pid>/environ`.  See [Deployment — systemd](../guides/deployment-systemd.md).

---

## Cheat sheet

### Env var overrides (highest precedence, override config.toml)

```bash
export KT_AUTH_HOST_TOKEN="..."           # or KT_AUTH_HOST_TOKEN_FILE=/path
export KT_AUTH_ADMIN_TOKEN="..."          # or KT_AUTH_ADMIN_TOKEN_FILE=/path
export KT_AUTH_MULTI_USER=required        # off | optional | required
export KT_AUTH_REGISTRATION=invite_only   # open | invite_only | admin_only
export KT_AUTH_LOOPBACK_BYPASS=0          # 0 = always require token
```

### What the frontend sends

| Wire shape | Carries |
|---|---|
| `Authorization: Bearer <host_token>` | L1 (host token) |
| `Cookie: kt_session=<id>` | L4 (user session, HTTP route) |
| `Authorization: Bearer <api_token>` | L4 (user API token, CLI / mobile) |
| `X-Admin-Token: <admin_token>` | L3 (admin op) |
| WS `Sec-WebSocket-Protocol: kt-token.<host_token>` | L1 over WebSocket |
| WS `?token=<host_token>` (fallback) | L1 over WebSocket (logs-visible) |

### Probe what's enabled (no auth required)

```bash
curl http://YOUR_HOST:8001/api/auth/capabilities
```

Returns the enabled-flag for each layer — useful for shell scripts
and the frontend's connection state machine.

---

## What can go wrong

| Symptom | Cause | Fix |
|---|---|---|
| `401 unauthorized` on every call | Wrong / missing host token | Re-run `kt admin show-host-token --yes`, double-check the header |
| `401 admin_required` on a setting save | L3 enabled, no `X-Admin-Token` | Add the admin token header |
| Browser shows endless reconnect after toggling auth | Token in localStorage doesn't match new config | Clear browser storage / re-paste token |
| `multi_user_disabled` on `/me` | L4 is off; `/me` makes no sense | Either enable L4 or stop calling `/me` |
| `invitation_invalid` on register | Token already used or expired | Generate a new invitation |
| `kt admin set-host-token` fails with "TOML shape ... cannot preserve" | Your `config.toml` has a top-level scalar or nested table | Move stray top-level keys into a `[section]` |
| Locked myself out of admin | Demoted the only admin | `kt admin users grant <name>` from any shell — works offline |

---

## What's next

- The Vue frontend hasn't shipped the auth UI yet (Phases H–K in the
  [roadmap](../../../plans/1.5.0-roadmap/03-frontend-backend-connection/README.md)).
  Until then, you use `curl` + cookies, or the API tokens via
  `Authorization: Bearer`.  The backend is stable and ready.
- Cross-host session import / export, password reset, 2FA — all
  deferred to 1.6+.

For the architecture / threat model / why-it-works-this-way reading,
see the [Authentication guide](../guides/authentication.md).
