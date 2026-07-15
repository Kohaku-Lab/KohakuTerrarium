"""Session-tree pane payload — fork lineage + attached agents.

Builds the session viewer's tree payload from lineage metadata and
agents attached to the same store.
"""

from typing import Any

from kohakuterrarium.session.store import SessionStore


def build_tree_payload(store: SessionStore, session_name: str) -> dict[str, Any]:
    """Return the focused session, adjacent forks, and attached agents.

    Fork lineage is limited to the parent and direct children because deeper
    traversal would require opening other session files. Attached agents are
    included because their records live in the focused session's store.
    """
    meta = store.load_meta()
    session_id = str(meta.get("session_id") or session_name)
    lineage = meta.get("lineage") or {}
    fork_meta = lineage.get("fork") if isinstance(lineage, dict) else None
    forked_children = meta.get("forked_children") or []
    attached = store.discover_attached_agents()

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    nodes.append(
        {
            "id": session_id,
            "type": "session",
            "label": session_name,
            "format_version": meta.get("format_version"),
            "status": meta.get("status"),
            "created_at": meta.get("created_at"),
            "last_active": meta.get("last_active"),
            "is_focus": True,
        }
    )

    # Parent metadata is a stub because only its ID is reliable locally.
    if isinstance(fork_meta, dict):
        parent_id = fork_meta.get("parent_session_id")
        fork_point = fork_meta.get("fork_point")
        if parent_id:
            nodes.append(
                {
                    "id": str(parent_id),
                    "type": "session",
                    "label": str(parent_id),
                    "is_parent_stub": True,
                }
            )
            edges.append(
                {
                    "from": str(parent_id),
                    "to": session_id,
                    "type": "fork",
                    "at": fork_point,
                }
            )

    # Child stubs avoid opening every forked session file.
    for child in forked_children:
        if not isinstance(child, dict):
            continue
        child_id = child.get("session_id")
        if not child_id:
            continue
        nodes.append(
            {
                "id": str(child_id),
                "type": "session",
                "label": str(child_id),
                "fork_point": child.get("fork_point"),
                "fork_created_at": child.get("fork_created_at"),
                "is_child_stub": True,
            }
        )
        edges.append(
            {
                "from": session_id,
                "to": str(child_id),
                "type": "fork",
                "at": child.get("fork_point"),
            }
        )

    # Attached-agent records are complete because they share this store.
    for entry in attached:
        ns = entry.get("namespace")
        if not ns:
            continue
        nodes.append(
            {
                "id": ns,
                "type": "attached",
                "label": entry.get("role") or ns,
                "host": entry.get("host"),
                "role": entry.get("role"),
                "attach_seq": entry.get("attach_seq"),
            }
        )
        edges.append(
            {
                "from": entry.get("host") or session_id,
                "to": ns,
                "type": "attach",
            }
        )

    return {
        "session_name": session_name,
        "session_id": session_id,
        "nodes": nodes,
        "edges": edges,
    }
