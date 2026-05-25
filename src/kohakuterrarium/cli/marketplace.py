"""CLI ``kt marketplace`` subcommands — manage sources, search, info.

Sits between argparse (the parser is built in :mod:`cli.__init__`)
and :mod:`kohakuterrarium.packages.marketplace` (the data layer).
Each function takes the parsed ``args`` Namespace and returns a CLI
exit code; all transport-specific formatting (column alignment,
JSON output, error rendering) lives here.

``kt install @<name>`` uses the marketplace via
:func:`packages.install.install_package_spec` — no separate verb
needed for that.
"""

import json
import sys

from kohakuterrarium.packages import marketplace
from kohakuterrarium.packages.marketplace_types import (
    MarketplaceError,
    MarketplaceUnavailableError,
)


def _render_table(rows: list[dict[str, str]], cols: list[str]) -> None:
    """Plain-text aligned-column table.  ``cols`` is the column order."""
    if not rows:
        return
    widths = {c: max(len(c), max(len(r.get(c, "")) for r in rows)) for c in cols}
    header = "  ".join(c.upper().ljust(widths[c]) for c in cols)
    print(header)
    print("  ".join("-" * widths[c] for c in cols))
    for r in rows:
        print("  ".join(r.get(c, "").ljust(widths[c]) for c in cols))


def list_sources_cli(args) -> int:  # noqa: ARG001 — kept for dispatcher signature
    """``kt marketplace list`` — show configured sources, in lookup order."""
    sources = marketplace.list_sources()
    if not sources:
        print("No marketplace sources configured.")
        return 0
    for i, src in enumerate(sources, 1):
        tag = "  (default)" if src.url == marketplace.DEFAULT_SOURCE_URL else ""
        print(f"{i}. [{src.alias}] {src.url}{tag}")
    return 0


def add_source_cli(args) -> int:
    """``kt marketplace add <url> [--alias <name>]``."""
    try:
        added = marketplace.add_source(args.url, alias=args.alias)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    sources = marketplace.list_sources()
    print(f"Added source [{added.alias}] {added.url}")
    print(f"({len(sources)} source{'s' if len(sources) != 1 else ''} now configured.)")
    return 0


def remove_source_cli(args) -> int:
    """``kt marketplace remove <url-or-alias>``."""
    if marketplace.remove_source(args.target):
        print(f"Removed source: {args.target}")
        return 0
    print(f"No source matches {args.target!r}", file=sys.stderr)
    return 1


def reset_sources_cli(args) -> int:  # noqa: ARG001
    """``kt marketplace reset`` — restore the built-in default list."""
    marketplace.reset_sources()
    print("Marketplace sources reset to default (TerrariumMarket).")
    return 0


def refresh_cli(args) -> int:  # noqa: ARG001
    """``kt marketplace refresh`` — bust cache + re-fetch."""
    try:
        entries = marketplace.fetch_marketplace_sync(force=True)
    except MarketplaceUnavailableError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Refreshed.  {len(entries)} package(s) across all sources.")
    return 0


def search_cli(args) -> int:
    """``kt marketplace search [query] [--tag] [--author] [--json]``."""
    try:
        results = marketplace.search_sync(
            args.query or "", tag=args.tag, author=args.author
        )
    except MarketplaceUnavailableError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(
            json.dumps(
                [_entry_to_dict(e) for e in results], indent=2, ensure_ascii=False
            )
        )
        return 0

    if not results:
        print("No matches.")
        return 0

    rows = []
    for entry in results:
        latest = entry.versions[0].tag if entry.versions else "?"
        tags = ", ".join(entry.tags[:3])
        if len(entry.tags) > 3:
            tags += "…"
        rows.append(
            {
                "name": entry.name,
                "version": latest,
                "tags": tags,
                "author": entry.author,
                "description": _truncate(entry.description, 60),
            }
        )
    _render_table(rows, ["name", "version", "tags", "author", "description"])
    print()
    print(f"{len(results)} package(s).  `kt install @<name>` to install.")
    return 0


def info_cli(args) -> int:
    """``kt marketplace info @<name>`` — detail view for one entry."""
    spec = args.spec if args.spec.startswith("@") else f"@{args.spec}"
    try:
        entry, version = marketplace.resolve_sync(spec)
    except MarketplaceError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    # If the spec carried an explicit source alias (``@fork/x``),
    # preserve it in the printed Install hint — otherwise the user
    # copies back ``kt install @x`` which silently routes to the
    # default source's copy.
    src_alias, _, _ = marketplace.parse_spec(spec)
    install_spec = f"@{src_alias}/{entry.name}" if src_alias else f"@{entry.name}"

    print(f"{entry.name}")
    print("-" * (len(entry.name) + 2))
    print(f"  description: {entry.description}")
    print(f"  author:      {entry.author}")
    print(f"  license:     {entry.license}")
    print(f"  framework:   {entry.framework}")
    print(f"  homepage:    {entry.homepage or entry.repo}")
    print(f"  source:      [{entry.source_alias}] {entry.source_url}")
    print(f"  tags:        {', '.join(entry.tags)}")
    print()
    print(f"  resolved:    {version.tag} (released {version.released})")
    if version.notes:
        print(f"  notes:       {version.notes}")
    print()
    print("  Versions:")
    for v in entry.versions:
        tag_str = v.tag.ljust(14)
        flag = " [yanked]" if v.yanked else ""
        print(f"    {tag_str}{v.released}{flag}")
    print()
    print(f"  Install: kt install {install_spec}")
    return 0


def _truncate(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return s[: n - 1].rstrip() + "…"


def _entry_to_dict(entry: marketplace.MarketplaceEntry) -> dict[str, object]:
    return {
        "name": entry.name,
        "repo": entry.repo,
        "description": entry.description,
        "tags": list(entry.tags),
        "author": entry.author,
        "license": entry.license,
        "framework": entry.framework,
        "homepage": entry.homepage,
        "source_alias": entry.source_alias,
        "source_url": entry.source_url,
        "versions": [
            {
                "tag": v.tag,
                "released": v.released,
                "framework": v.framework,
                "notes": v.notes,
                "notes_url": v.notes_url,
                "yanked": v.yanked,
            }
            for v in entry.versions
        ],
    }


# Top-level dispatcher — wired from cli/__init__.py's COMMANDS dict
def marketplace_cli(args) -> int:
    """Dispatch ``kt marketplace <subcommand>`` based on ``args.marketplace_command``."""
    sub = getattr(args, "marketplace_command", None)
    match sub:
        case "list" | None:
            return list_sources_cli(args)
        case "add":
            return add_source_cli(args)
        case "remove":
            return remove_source_cli(args)
        case "reset":
            return reset_sources_cli(args)
        case "refresh":
            return refresh_cli(args)
        case "search":
            return search_cli(args)
        case "info":
            return info_cli(args)
        case _:
            print(f"Unknown marketplace subcommand: {sub}", file=sys.stderr)
            return 2
