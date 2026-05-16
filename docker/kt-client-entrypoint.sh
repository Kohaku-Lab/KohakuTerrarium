#!/usr/bin/env bash
# kohaku/client entry: validate required env vars then exec `kt lab-client`.
#
# Required:
#   KT_HOST_URL        — ws:// or wss:// URL of the lab transport
#   KT_HOST_TOKEN      — shared token (or use KT_HOST_TOKEN_FILE)
#   KT_CLIENT_NAME     — unique name for this worker
#
# Optional:
#   KT_HOST_TOKEN_FILE — path; takes precedence over KT_HOST_TOKEN
#   KT_CONFIG_DIR      — worker's config home

set -euo pipefail

: "${KT_HOST_URL:?KT_HOST_URL is required (ws:// or wss:// URL)}"
: "${KT_CLIENT_NAME:?KT_CLIENT_NAME is required (unique worker name)}"
: "${KT_CONFIG_DIR:=/home/kt/.kohakuterrarium}"

# Resolve token: file > env. Refuse to start without one.
if [ -n "${KT_HOST_TOKEN_FILE:-}" ] && [ -r "$KT_HOST_TOKEN_FILE" ]; then
    KT_HOST_TOKEN="$(cat "$KT_HOST_TOKEN_FILE")"
fi
if [ -z "${KT_HOST_TOKEN:-}" ]; then
    echo "[client] KT_HOST_TOKEN or KT_HOST_TOKEN_FILE is required" >&2
    exit 1
fi

exec kt lab-client \
    --host "$KT_HOST_URL" \
    --token "$KT_HOST_TOKEN" \
    --name "$KT_CLIENT_NAME" \
    --home-dir "$KT_CONFIG_DIR"
