#!/bin/sh
# KohakuTerrarium installer for Linux and macOS.
#
#   curl -fsSL https://github.com/Kohaku-Lab/KohakuTerrarium/releases/download/manifests-stable/install.sh | sh
#
# Installs, for this user only (no root):
#   ~/.kohakuterrarium/runtime/terminal/   a Python of its own and the launcher
#   ~/.kohakuterrarium/runtime/versions/   the release, shared with the desktop app
#   ~/.local/bin/                          kt, kt-ui, kt-cli, kt-tui
#
# Options (after `sh -s --`), or the same as environment variables:
#   --channel stable|beta|nightly   KT_CHANNEL       (default stable)
#   --base URL                      KT_INSTALL_BASE  where the runtime archive lives
#   --feed-url URL                  KT_FEED_URL      a custom release feed (<channel>.json)
#   KT_CONFIG_DIR                   another home than ~/.kohakuterrarium
# Update later with `kt self-update`; remove with `kt self-uninstall`.

# Everything is in functions and runs from the last line, so a download cut short runs
# nothing.

set -eu

say() { printf '%s\n' "$*"; }
fail() { printf 'install: %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
KohakuTerrarium installer (Linux, macOS)
  curl -fsSL .../install.sh | sh -s -- [--channel stable|beta|nightly] [--base URL] [--feed-url URL]
Environment: KT_CHANNEL, KT_INSTALL_BASE, KT_FEED_URL, KT_CONFIG_DIR
EOF
}

detect_platform() {
    os="$(uname -s)"
    arch="$(uname -m)"
    case "$os" in
        Linux) os=linux ;;
        Darwin) os=macos ;;
        *) fail "unsupported system: $os (Windows: use install.ps1)" ;;
    esac
    case "$arch" in
        x86_64|amd64) arch=x64 ;;
        aarch64|arm64) arch=arm64 ;;
        *) fail "unsupported processor: $arch" ;;
    esac
    case "$os-$arch" in
        linux-x64|linux-arm64|macos-arm64) printf '%s' "$os-$arch" ;;
        *) fail "no KohakuTerrarium build for $os-$arch (pip install kohakuterrarium instead)" ;;
    esac
}

fetch() {
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL "$1" -o "$2"
    elif command -v wget >/dev/null 2>&1; then
        wget -q "$1" -O "$2"
    else
        fail "needs curl or wget"
    fi
}

sha256() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | cut -d' ' -f1
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | cut -d' ' -f1
    else
        fail "needs sha256sum or shasum"
    fi
}

main() {
    repo="Kohaku-Lab/KohakuTerrarium"
    channel="${KT_CHANNEL:-stable}"
    base="${KT_INSTALL_BASE:-}"
    feed_url="${KT_FEED_URL:-}"
    while [ $# -gt 0 ]; do
        case "$1" in
            --channel) channel="${2:?--channel needs a value}"; shift 2 ;;
            --base) base="${2:?--base needs a value}"; shift 2 ;;
            --feed-url) feed_url="${2:?--feed-url needs a value}"; shift 2 ;;
            -h|--help) usage; exit 0 ;;
            *) fail "unknown option: $1" ;;
        esac
    done
    case "$channel" in
        stable|beta|nightly) ;;
        *) fail "channel must be stable, beta or nightly (got '$channel')" ;;
    esac
    base="${base:-https://github.com/$repo/releases/download/manifests-$channel}"
    base="${base%/}"
    platform="$(detect_platform)"

    home_dir="${KT_CONFIG_DIR:-$HOME/.kohakuterrarium}"
    runtime="$home_dir/runtime"
    terminal="$runtime/terminal"
    name="kt-runtime-$platform.tar.gz"
    mkdir -p "$runtime"
    # downloaded and unpacked beside the runtime, swapped in once complete
    staging="$runtime/terminal.new"
    rm -rf "$staging"
    mkdir -p "$staging"
    trap 'rm -rf "$staging"' EXIT
    trap 'rm -rf "$staging"; exit 130' INT TERM

    say "Downloading the KohakuTerrarium runtime ($channel, $platform)..."
    fetch "$base/$name" "$staging/$name" || fail "could not download $base/$name"
    fetch "$base/$name.sha256" "$staging/$name.sha256" || fail "could not download the checksum"
    wanted="$(cut -d' ' -f1 < "$staging/$name.sha256")"
    actual="$(sha256 "$staging/$name")"
    [ "$wanted" = "$actual" ] || fail "checksum mismatch for $name (expected $wanted, got $actual)"
    tar -xzf "$staging/$name" -C "$staging"

    rm -rf "$runtime/terminal.old"
    had=0
    if [ -d "$terminal" ]; then
        mv "$terminal" "$runtime/terminal.old"
        had=1
    fi
    if ! mv "$staging/kt-runtime" "$terminal"; then
        [ "$had" = 1 ] && mv "$runtime/terminal.old" "$terminal"  # the previous one stays
        fail "could not put the runtime in place"
    fi
    rm -rf "$runtime/terminal.old"

    python="$terminal/python/bin/python3"
    bootstrap="$terminal/bootstrap"
    export PYTHONPATH="$bootstrap" PYTHONNOUSERSITE=1 KT_PYTHON="$python" \
        KT_BOOTSTRAP_DIR="$bootstrap" KT_LAUNCHER_TERMINAL=1

    say "Installing the newest $channel release..."
    # stdin is not theirs: under `curl | sh` it is the rest of this script
    if [ -n "$feed_url" ]; then
        "$python" -m kohakuterrarium.launcher --install --channel "$channel" \
            --feed-url "$feed_url" </dev/null || fail "the release did not install (see above)"
    else
        "$python" -m kohakuterrarium.launcher --install --channel "$channel" \
            </dev/null || fail "the release did not install (see above)"
    fi
    "$python" -m kohakuterrarium.launcher shims install </dev/null \
        || fail "the kt commands were not created"

    say ""
    say "KohakuTerrarium is installed. Start with:  kt-ui"
}

main "$@"
