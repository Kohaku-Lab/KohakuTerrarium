"""Resolve retired session files only when resuming execution."""

from pathlib import Path
from typing import Any

from kohakuterrarium.errors import SessionNotResumableError
from kohakuterrarium.session.readonly_view import SessionReadView
from kohakuterrarium.session.version import FORMAT_VERSION

SUCCESSOR_KEY = "resume_successor"


def _latest_resume_file(path: Path) -> Path:
    """Select a format companion without copying live SQLite/WAL files."""
    stem, separator, suffix = path.name.rpartition(".v")
    bare = path.with_name(stem) if separator and suffix.isdigit() else path
    candidates = []
    for candidate in bare.parent.glob(f"{bare.name}.v*"):
        version = candidate.name.rsplit(".v", 1)[-1]
        if version.isdigit() and candidate.is_file():
            candidates.append((int(version), 1, candidate))
    if not candidates:
        return path
    if bare.is_file():
        with SessionReadView(bare) as view:
            try:
                version = int(view.get("meta", "format_version", 1))
            except (TypeError, ValueError):
                version = 1
        candidates.append((version, 0, bare))
    readable = [item for item in candidates if item[0] <= FORMAT_VERSION]
    return max(readable or candidates)[2]


def resolve_resume_path(
    path: str | Path, *, session_dir: str | Path | None = None
) -> Path:
    """Follow explicit merge successors without changing historical lookups.

    A missing, unfinished, or ambiguous successor never falls back to the
    retired file. API callers supply their authorized session directory.
    """
    current = Path(path).expanduser()
    root = Path(session_dir).expanduser().resolve() if session_dir is not None else None
    seen: set[Path] = set()
    expected_identity: str | None = None
    required_agents: set[str] = set()
    for _ in range(64):
        if root is not None and not current.resolve().is_relative_to(root):
            raise SessionNotResumableError(
                "Resume successor is outside the session directory"
            )
        current = _latest_resume_file(current).resolve()
        if root is not None and not current.is_relative_to(root):
            raise SessionNotResumableError(
                "Resume successor is outside the session directory"
            )
        if current in seen:
            raise SessionNotResumableError("Resume successor chain contains a cycle")
        seen.add(current)
        if not current.is_file():
            if len(seen) == 1:
                raise FileNotFoundError(f"Session is missing: {current}")
            raise SessionNotResumableError(f"Resume successor is missing: {current}")
        with SessionReadView(current) as view:
            meta = {
                key: view.get("meta", key)
                for key in (
                    "conversation_id",
                    SUCCESSOR_KEY,
                    "live_graph_manifest",
                    "agents",
                )
            }
        if expected_identity and meta.get("conversation_id") != expected_identity:
            raise SessionNotResumableError(
                "Resume successor conversation identity changed"
            )
        successor = meta.get(SUCCESSOR_KEY)
        if successor is None:
            manifest = meta.get("live_graph_manifest")
            agents = (
                {
                    item.get("name")
                    for item in manifest.get("creatures", [])
                    if isinstance(item, dict)
                }
                if isinstance(manifest, dict)
                else set(meta.get("agents") or [])
            )
            if required_agents - agents:
                raise SessionNotResumableError(
                    "Resume successor membership changed after a split or rename; choose the current session explicitly"
                )
            return current
        if not isinstance(successor, dict):
            raise SessionNotResumableError("Invalid resume successor metadata")
        if successor.get("state") != "ready":
            raise SessionNotResumableError(
                "Session retirement is pending a durable graph checkpoint"
            )
        targets = successor.get("targets")
        if (
            successor.get("kind") != "merge"
            or not isinstance(targets, list)
            or len(targets) != 1
        ):
            raise SessionNotResumableError(
                "Session has split successors; choose a current session explicitly"
            )
        target = targets[0]
        if (
            not isinstance(target, dict)
            or not isinstance(target.get("path"), str)
            or not target["path"]
        ):
            raise SessionNotResumableError("Invalid resume successor path")
        identity = target.get("conversation_id")
        if not isinstance(identity, str) or not identity:
            raise SessionNotResumableError(
                "Invalid resume successor conversation identity"
            )
        names = successor.get("agents", [])
        if not isinstance(names, list) or any(
            not isinstance(name, str) for name in names
        ):
            raise SessionNotResumableError("Invalid resume successor agent namespaces")
        required_agents.update(names)
        expected_identity = identity
        next_path = Path(target["path"])
        current = next_path if next_path.is_absolute() else current.parent / next_path
    raise SessionNotResumableError("Resume successor chain is too long")


def reject_retired_writer(store: Any) -> None:
    """Revalidate retirement after acquiring a live resume's writer lock."""
    if store.meta.get(SUCCESSOR_KEY) is not None:
        raise SessionNotResumableError(
            "Session was superseded during resume; retry using its current successor"
        )


def active_resume_graph(engine: Any, path: Path) -> str | None:
    """Find an already-adopted canonical file without rebuilding its agents."""
    for graph_id, store in engine._session_stores.items():
        if (
            Path(store.path).resolve() == path.resolve()
            and graph_id in engine._topology.graphs
        ):
            with SessionReadView(path) as view:
                identity = view.get("meta", "conversation_id")
            if store.meta.get("conversation_id") != identity:
                raise SessionNotResumableError(
                    "Already-running session conversation identity changed"
                )
            return graph_id
    return None
