---
title: Authentication
summary: Four optional auth layers — host token, admin password, user accounts — stack at the API server boundary.  Configure per deployment shape; defaults are everything off (current behaviour).
tags:
  - guides
  - deployment
  - authentication
---

# Authentication

KohakuTerrarium runs unauthenticated by default — perfect for the
desktop app on loopback, where the OS user is your trust boundary.
For everything else (LAN host, family server, internet-exposed
deployment) four optional auth layers stack at the API server.
Each is opt-in via the ``[auth]`` config section; defaults preserve
single-user open-host behaviour.

## The four layers

| Layer | Gate | Use when |
|---|---|---|
| **L1** Host selection | Frontend-only — which backend the app talks to | Always (built into bundled apps) |
| **L2** Host token | "Is this client allowed to reach the host at all?" | LAN / internet-exposed host |
| **L3** Admin token | "Is this caller allowed to mutate host config?" | You want family members to use the host without giving them config rights |
| **L4** User accounts | "Whose sessions / UI prefs is this request scoped to?" | Multi-user shared host |

The layers compose.  A multi-user family server with locked-down config
turns L2 + L3 + L4 on; a single-user LAN host turns just L2 on; the
default desktop turns nothing on.

## Architectural invariant

Auth lives entirely at the API server boundary (``api/auth/``).  The
engine, Studio, terrarium runtime, and session store have **zero**
knowledge of users, tokens, or hosts.  When L4 is enabled, per-user
isolation is enforced by routing each authenticated request to a
per-user ``Terrarium`` engine via an engine pool — the engine itself
stays single-tenant.

This means the CLI (``kt run``, ``kt list``, ``kt resume``) and the
embedded TUI work unchanged in every auth mode; only the FastAPI
server multiplexes.

## Configuration

Everything goes in ``<config_dir>/config.toml`` under ``[auth]``:

```toml
[auth]
host_token = ""                   # off if empty
admin_token = ""                  # off if empty
multi_user = "off"                # off | optional | required
registration = "admin_only"       # open | invite_only | admin_only
loopback_bypass = true            # 127.0.0.1 skips L2 only
session_expire_hours = 168        # 7 days
session_idle_minutes = 0          # 0 = no idle expiry
bcrypt_rounds = 12                # password hash cost factor
```

Environment-variable overrides (highest precedence):

| Env var | Meaning |
|---|---|
| ``KT_AUTH_HOST_TOKEN`` | L2 token (inline) |
| ``KT_AUTH_HOST_TOKEN_FILE`` | L2 token from file (Docker / systemd secrets) |
| ``KT_AUTH_ADMIN_TOKEN`` | L3 token (inline) |
| ``KT_AUTH_ADMIN_TOKEN_FILE`` | L3 token from file |
| ``KT_AUTH_MULTI_USER`` | ``off`` / ``optional`` / ``required`` |
| ``KT_AUTH_REGISTRATION`` | ``open`` / ``invite_only`` / ``admin_only`` |
| ``KT_AUTH_LOOPBACK_BYPASS`` | ``0`` / ``1`` |

The ``*_FILE`` variants exist so secrets land via Docker
``secrets:`` mounts or systemd ``LoadCredential=`` directives — they
never appear in ``/proc/<pid>/environ``.

## Discovering what a host has enabled

```
GET /api/auth/capabilities                   (no auth required)
```

The frontend hits this BEFORE any other API call to know what to
prompt for.  The response carries no secrets — only the enabled
flags + mode metadata:

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

## Layer 2 — host token

A single shared secret gates every ``/api/*`` and ``/ws/*`` request.

```bash
# Generate + store a new host_token.
kt admin set-host-token
# rotates an existing one (same effect; alias):
kt admin rotate-host-token
```

The token lands in ``<config_dir>/config.toml``.  Clients present it as:

- HTTP: ``Authorization: Bearer <token>``
- WebSocket: ``Sec-WebSocket-Protocol: kt-token.<token>`` (preferred,
  doesn't leak into proxy logs) **or** ``?token=<token>`` query
  string (curl-friendly fallback).

**Loopback bypass.**  Requests from ``127.0.0.1`` / ``::1`` skip L2
by default (``loopback_bypass = true``).  This keeps the desktop app
friction-free — your app doesn't need to know the token to talk to
its own bundled host.  Disable for production deployments behind a
reverse proxy:

```toml
[auth]
host_token = "..."
loopback_bypass = false
```

**CORS preflight.**  ``OPTIONS`` requests pass through L2 unconditionally
so cross-origin browsers can complete preflight before sending the
real (authenticated) request.

## Layer 3 — admin token

A second shared secret gates **config-mutating routes only** —
adding LLM keys, registering MCP servers, installing packages,
editing models / profiles.  Read access and chat use are not gated.

```bash
kt admin set-admin-token
```

Clients present it as ``X-Admin-Token: <token>`` on the relevant
routes.  A 401 from L3 carries ``X-Auth-Required: admin`` so the
frontend can distinguish "need admin pswd" from "need login."

This is the **family server** pattern: anyone with the host token can
chat / read configs; only the operator with the admin token can
change LLM profiles or install packages.

## Layer 4 — user accounts

Per-user accounts with isolated sessions, UI prefs, and API tokens.

```toml
[auth]
multi_user = "required"           # require login on every /api/*
registration = "invite_only"
```

Three registration modes:

| Mode | Self-registration | New users come from |
|---|---|---|
| ``open`` | yes | ``POST /api/auth/register`` (anyone) |
| ``invite_only`` | yes, with token | Admin generates a one-shot invite via ``kt admin invitations create`` |
| ``admin_only`` | no | Admin runs ``kt admin users add <username>`` |

**Authentication shapes:**

- **Web (same-origin)**: ``POST /api/auth/login`` sets an
  ``HttpOnly`` + ``SameSite=Lax`` cookie.  The browser sends it on
  every subsequent request.
- **Bundled apps / CLI / cross-origin web**: ``POST /api/auth/tokens``
  generates a long-lived API token (shown ONCE at creation).  Clients
  present it as ``Authorization: Bearer <token>``.

**Per-user data layout:**

```
<config_dir>/
├── auth.db
├── api_keys.yaml          # SHARED — admin-managed
├── llm_profiles.yaml      # SHARED — admin-managed
├── mcp_servers.yaml       # SHARED — admin-managed
└── users/
    └── <user_id>/
        ├── ui_prefs.json
        └── sessions/
            └── <session-name>.kohakutr
```

LLM keys, profiles, and MCP servers are **shared** (admin manages
them once).  Sessions and UI prefs are **per-user**.

## Bootstrap recipe

For a fresh family server:

```bash
# 1. Generate auth secrets.
kt admin set-host-token
kt admin set-admin-token

# 2. Edit <config_dir>/config.toml to set multi_user + registration:
#    [auth]
#    multi_user = "required"
#    registration = "invite_only"

# 3. Create the first admin user (interactive password prompt).
kt admin users add operator --role admin

# 4. Generate invitations for family members.
kt admin invitations create --role user --expires-in-hours 168

# 5. Start the server.
kt serve start --host 0.0.0.0
```

Each family member receives an invitation token via your channel of
choice; they call ``POST /api/auth/register`` with the token to
create their account.

## Migrating an existing single-user host to multi-user

When you enable L4 on a host that's been used in single-user mode,
existing ``ui_prefs.json`` and ``sessions/`` are NOT auto-moved.
Claim them explicitly:

```bash
# Move shared-state UI prefs + .kohakutr sessions into a user's namespace.
kt admin migrate --from-shared-state --to-user operator
```

This is deliberate — automatic migration in a multi-user upgrade could
move someone else's session into the wrong namespace.

## Cryptography

| Use | Primitive |
|---|---|
| Password hashing | bcrypt (cost factor configurable; default 12) |
| API token hashing | SHA3-512 (one-way, fast lookup) |
| Session ID generation | ``secrets.token_urlsafe(32)`` (256 bits) |
| Token / admin compare | ``secrets.compare_digest`` (constant-time) |

API tokens and invitations are generated CSPRNG and stored hashed —
a DB leak cannot be replayed.

## Threat model

| Threat | Defended by |
|---|---|
| LAN neighbour scanning a port and calling the API | L2 |
| Roommate using the shared host to swap your LLM profile | L3 or L4 |
| Two family members' chat histories cross-contaminating | L4 |
| Token in URL leaking via referer / proxy logs | WS sub-protocol auth |

What auth does **not** defend against:

- A user with shell access to ``<config_dir>/`` — they can read
  ``auth.db`` (bcrypt hashes; offline crack is the standard cost)
  AND every user's session files.  Auth is an API boundary, not an
  OS boundary.
- Side-channel attacks (timing, traffic analysis) — out of scope.

## TLS

The framework does NOT terminate TLS itself in 1.5.0.  Operators put
a reverse proxy (Caddy / nginx / Traefik) in front for HTTPS.  See
[Deployment — Reverse proxy](deployment-reverse-proxy.md).  The
frontend nags with a banner *"connection is not encrypted"* when
``auth`` is on but the host URL is plain ``http://`` and non-loopback.

## See also

- [Deployment — Docker](deployment-docker.md) — ``[auth]`` config via
  ``secrets:`` mounts
- [Deployment — systemd](deployment-systemd.md) — ``[auth]`` config
  via ``LoadCredential=`` directives
- [Deployment — Reverse proxy](deployment-reverse-proxy.md) — TLS
  termination + CORS allowlist for hosted static frontends
