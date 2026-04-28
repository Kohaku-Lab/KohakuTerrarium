"""Session-tree pane payload — fork lineage + attached agents.

Verbatim port of ``api/routes/_session_viewer.py:build_tree_payload``.
Returns ``{nodes, edges}`` for the viewer's tree pane: parent stub
(if forked), direct children, attached agents.
"""

from typing import Any

from kohakuterrarium.session.store import SessionStore


def build_tree_payload(store: SessionStore, session_name: str) -> dict[str, Any]:
    """Return ``{nodes, edges}`` for the session-tree pane.

    One hop in each direction: parent (if forked) and direct
    forked-children. Attached agents are always included recursively
    because they live in this same store. Walking the full fork tree
    would require opening every child file, which we defer to client-
    side navigation (the user clicks a child node, the frontend calls
    ``/tree`` again on that session).
    """
    meta = store.load_meta()
    session_id = str(meta.get("session_id") or session_name)
    lineage = meta.get("lineage") or {}
    fork_meta = lineage.get("fork") if isinstance(lineage, dict) else None
    forked_children = meta.get("forked_children") or []
    attached = store.discover_attached_agents()

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []

    # Root node = the session being viewed.
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

    # Parent stub — only id is reliable without opening the file.
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

    # Direct forked children — metadata-only nodes, no file opens.
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

    # Attached agents — full nodes (they share the store).
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
