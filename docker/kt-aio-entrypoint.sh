#!/usr/bin/env bash
# AIO container entry: spawn lab-host + embedded local lab-client in
# one container, forward signals so SIGTERM cleanly stops both.
#
# Environment:
#   KT_HOST_TOKEN       — shared lab token; auto-generated if absent
#   KT_HOST_TOKEN_FILE  — optional path to read token from (takes
#                         precedence over KT_HOST_TOKEN)
#   KT_HTTP_HOST        — http bind host (default 0.0.0.0)
#   KT_HTTP_PORT        — http port (default 8001)
#   KT_LAB_BIND         — lab bind (default 0.0.0.0:8100)
#   KT_CLIENT_NAME      — embedded worker name (default local-1)
#   KT_CONFIG_DIR       — config home (default /home/kt/.kohakuterrarium)
#
# Logged once on startup: the resolved lab token (also written to
# $KT_CONFIG_DIR/host-token mode 0600) so the operator can attach
# additional external workers by running kohaku/client elsewhere.

set -euo pipefail

: "${KT_CONFIG_DIR:?KT_CONFIG_DIR must be set}"
: "${KT_HTTP_HOST:=0.0.0.0}"
: "${KT_HTTP_PORT:=8001}"
: "${KT_LAB_BIND:=0.0.0.0:8100}"
: "${KT_CLIENT_NAME:=local-1}"

mkdir -p "$KT_CONFIG_DIR"

# Resolve token: file > env > generated.
if [ -n "${KT_HOST_TOKEN_FILE:-}" ] && [ -r "$KT_HOST_TOKEN_FILE" ]; then
    KT_HOST_TOKEN="$(cat "$KT_HOST_TOKEN_FILE")"
fi
if [ -z "${KT_HOST_TOKEN:-}" ]; then
    KT_HOST_TOKEN="$(openssl rand -hex 24)"
    echo "[aio] No KT_HOST_TOKEN provided — generated one." >&2
fi

# Persist for the operator to discover via `docker logs` / volume mount.
TOKEN_FILE="$KT_CONFIG_DIR/host-token"
umask 077
echo "$KT_HOST_TOKEN" > "$TOKEN_FILE"
umask 022

echo "[aio] Lab token: $KT_HOST_TOKEN" >&2
echo "[aio] Token also written to: $TOKEN_FILE" >&2
echo "[aio] To add external workers, run kohaku/client with:" >&2
echo "[aio]   KT_HOST_URL=ws://<this-host>:${KT_LAB_BIND##*:} KT_HOST_TOKEN=$KT_HOST_TOKEN" >&2

# Extract lab port for the readiness probe below.
LAB_PORT="${KT_LAB_BIND##*:}"

# 1. Start the lab-host in background.
kt serve start --mode lab-host --foreground \
    --host "$KT_HTTP_HOST" --port "$KT_HTTP_PORT" \
    --lab-bind "$KT_LAB_BIND" \
    --lab-token "$KT_HOST_TOKEN" \
    --home-dir "$KT_CONFIG_DIR" &
HOST_PID=$!

# 2. Wait for the lab port to accept connections (max 30s).
echo "[aio] Waiting for lab port $LAB_PORT to be ready..." >&2
for _ in $(seq 1 60); do
    if nc -z 127.0.0.1 "$LAB_PORT" 2>/dev/null; then
        echo "[aio] Lab port ready." >&2
        break
    fi
    sleep 0.5
done
if ! nc -z 127.0.0.1 "$LAB_PORT" 2>/dev/null; then
    echo "[aio] Lab port did NOT become ready within 30s — aborting." >&2
    kill -TERM "$HOST_PID" 2>/dev/null || true
    wait "$HOST_PID" 2>/dev/null || true
    exit 1
fi

# 3. Start the embedded worker against loopback lab.
kt lab-client \
    --host "ws://127.0.0.1:$LAB_PORT" \
    --token "$KT_HOST_TOKEN" \
    --name "$KT_CLIENT_NAME" \
    --home-dir "$KT_CONFIG_DIR/worker-$KT_CLIENT_NAME" &
WORKER_PID=$!

# 4. Forward SIGTERM/SIGINT to both children for clean shutdown.
_term() {
    echo "[aio] Stopping..." >&2
    kill -TERM "$WORKER_PID" 2>/dev/null || true
    kill -TERM "$HOST_PID" 2>/dev/null || true
    wait
    exit 0
}
trap _term SIGTERM SIGINT

# 5. Wait — if either child dies, exit with its status (so docker
# restart-policy can do its job).
wait -n
EXIT=$?
echo "[aio] A child process exited with status $EXIT — shutting down siblings." >&2
kill -TERM "$WORKER_PID" 2>/dev/null || true
kill -TERM "$HOST_PID" 2>/dev/null || true
wait
exit "$EXIT"
