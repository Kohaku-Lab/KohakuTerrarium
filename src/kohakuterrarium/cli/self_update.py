"""``kt self-update`` — CLI parity for the launcher's update flow.

The 06b launcher is the *only* thing that updates this install. This
verb is a thin client over :mod:`launcher.update_runner`:

- **Launcher install** (active pointer + interpreter under
  ``runtime/versions/``) → call :func:`launcher.update_runner.run_update`
  with optional one-shot overrides (`--channel`, `--feed-url`, `--pin`).
- **Anything else** (dev install via ``pip install -e .``, lab worker
  node, system package, pipx) → refuse with a one-line explanation.
  No more "fall back to pip" — the framework process running outside
  the launcher is by definition not something we manage.
"""

import argparse
import importlib.metadata
import sys

from kohakuterrarium.launcher import settings as _launcher_settings
from kohakuterrarium.launcher.feeds import (
    FeedError,
    resolve_feed,
)
from kohakuterrarium.launcher.migration import is_launcher_install
from kohakuterrarium.launcher.tree_ops import read_active_pointer
from kohakuterrarium.launcher.update_runner import (
    probe_only,
    rollback,
    run_update,
)


def _print(line: str) -> None:
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _current_version() -> str | None:
    try:
        return importlib.metadata.version("kohakuterrarium")
    except importlib.metadata.PackageNotFoundError:
        return None


def _apply_overrides(args: argparse.Namespace) -> None:
    """Apply ``--channel``, ``--feed-url``, ``--pin`` overrides to settings.

    Persisted so the next launcher launch inherits the new choice —
    matches the GUI's behaviour where the dropdown is sticky.
    """
    cfg = _launcher_settings.load()
    touched = False
    if args.channel is not None:
        cfg.channel = args.channel
        touched = True
    if args.feed_url is not None:
        cfg.feed.kind = "custom"
        cfg.feed.url = args.feed_url
        touched = True
    if args.pin is not None:
        cfg.pinned_version = args.pin or None
        touched = True
    if touched:
        _launcher_settings.save(cfg)


def _refuse_non_launcher() -> int:
    _print("kt self-update: this process is not running inside a launcher install.")
    _print(
        "  - Developer checkout?  Run `git pull` (or `pip install -e .`) in your tree."
    )
    _print("  - Worker node?         Update the host's launcher install separately.")
    _print("  - pipx / system pip?   Use those tools' own update commands.")
    return 2


def self_update_cli(args: argparse.Namespace) -> int:
    cur = _current_version() or "unknown"

    if not is_launcher_install():
        if args.check_only:
            _print(
                f"kt self-update: current={cur}; not running inside a launcher install"
            )
            return 2
        return _refuse_non_launcher()

    _apply_overrides(args)

    if args.check_only:
        result = probe_only()
        if not result.ok:
            _print(f"kt self-update: probe failed — {result.error}")
            return 2
        _print(f"kt self-update: current={cur}; latest={result.version}")
        return 0 if result.skipped_reason != "up-to-date" else 1

    if args.rollback:
        result = rollback()
        if not result.ok:
            _print(f"kt self-update: rollback failed — {result.error}")
            return 1
        _print(
            f"kt self-update: pointer reverted to {result.version}; restart to use it"
        )
        return 0

    if args.dry_run:
        cfg = _launcher_settings.load()
        try:
            target = resolve_feed(cfg, force_refresh=True)
        except FeedError as e:
            _print(f"kt self-update: dry-run resolution failed — {e}")
            return 2
        ptr = read_active_pointer()
        active = ptr.version if ptr else "<none>"
        _print(
            f"kt self-update: --dry-run; would install {target.version} "
            f"(active={active}, feed={cfg.feed.kind}, channel={cfg.channel})"
        )
        return 0

    result = run_update()
    if not result.ok:
        _print(f"kt self-update: failed — {result.error}")
        return 1
    if result.skipped_reason == "up-to-date":
        _print(f"kt self-update: already on {result.version}; no change")
        return 0
    _print(f"kt self-update: updated to {result.version}; restart the app to use it")
    return 0


def add_self_update_subparser(subparsers) -> None:
    parser = subparsers.add_parser(
        "self-update",
        help="Update KohakuTerrarium via the launcher's release feed",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Resolve the feed and print latest available; exit 0 if newer, 1 if up-to-date.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve the feed and print what would be installed; do not install.",
    )
    parser.add_argument(
        "--rollback",
        action="store_true",
        help="Revert the active pointer to the previous installed version.",
    )
    parser.add_argument(
        "--channel",
        choices=("stable", "beta", "nightly"),
        default=None,
        help="Persist a new channel before updating (sticky, mirrors the GUI dropdown).",
    )
    parser.add_argument(
        "--feed-url",
        default=None,
        help="Switch to a custom feed URL before updating (sticky).",
    )
    parser.add_argument(
        "--pin",
        default=None,
        help='Persist a pinned version (e.g. "1.5.1"); empty string clears.',
    )


__all__ = ["add_self_update_subparser", "self_update_cli"]
