"""Maintain cached status for remote creatures in session metadata.

Model, LLM, running, and privilege fields let Studio render remote sessions during
brief worker outages. Callers provide their runtime-scoped metadata registry;
reads refresh lazily, while model switches can update the cache eagerly.
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kohakuterrarium.terrarium import TerrariumService


def update_remote_creature_model_meta(
    meta_registry: dict[str, dict[str, Any]],
    creature_id: str,
    *,
    model: str = "",
    llm_name: str = "",
) -> None:
    """Cache a remote creature's model choice after a successful switch."""
    if not creature_id:
        return
    for meta in meta_registry.values():
        if meta.get("creature_id") != creature_id:
            continue
        if model:
            meta["model"] = str(model)
        if llm_name:
            meta["llm_name"] = str(llm_name)


async def refresh_remote_creature_meta(
    meta_registry: dict[str, dict[str, Any]],
    service: "TerrariumService",
    session_id: str,
    *,
    cluster_members: list[str] | None = None,
) -> None:
    """Refresh cached worker status for a session and its cluster members.

    ``cluster_members`` is the list of sids belonging to ``session_id``'s
    cluster (caller resolves via :mod:`cluster_fold`); we always include
    ``session_id`` itself so the single-session case behaves identically.

    Empty worker responses MUST NOT clobber cached values — the user's
    switch_model selection is the source of truth in that race.
    """
    sids: list[str] = list(cluster_members or [])
    if session_id not in sids:
        sids.append(session_id)
    get_info = getattr(service, "get_creature_info", None)
    if not callable(get_info):
        return
    for sid in sids:
        meta = meta_registry.get(sid)
        if meta is None or not meta.get("on_node"):
            continue
        cid = meta.get("creature_id") or sid
        try:
            info = await get_info(cid)
        except Exception:  # pragma: no cover - defensive
            info = None
        if info is None:
            continue
        new_model = str(getattr(info, "model", "") or "")
        new_llm_name = str(getattr(info, "llm_name", "") or "")
        if new_model:
            meta["model"] = new_model
        if new_llm_name:
            meta["llm_name"] = new_llm_name
        meta["running"] = bool(getattr(info, "is_running", meta.get("running", True)))
        meta["is_privileged"] = bool(
            getattr(info, "is_privileged", meta.get("is_privileged", False))
        )


async def refresh_all_remote_creature_meta(
    meta_registry: dict[str, dict[str, Any]],
    service: "TerrariumService",
) -> None:
    """Refresh every remote metadata entry from one creature-list fan-out.

    Worker-side switch_model paths that do not call the host's
    ``/creatures/{cid}/model`` route (the ``/model`` slash command,
    ``PluginContext.switch_model``, and the compact-LLM swap) update
    only the worker's ``Agent.llm`` and never notify the host's
    ``_meta`` cache. Sync read paths — ``lifecycle.list_sessions``,
    ``lifecycle.list_creatures``, and the legacy ``GET /agents``
    aliases — return the stale cached identifier.

    One service call synchronizes all cached entries before synchronous readers
    use them. Empty worker values never replace a previously confirmed selection.
    """
    list_creatures_fn = getattr(service, "list_creatures", None)
    if not callable(list_creatures_fn):
        return
    try:
        infos = await list_creatures_fn()
    except Exception:  # pragma: no cover - defensive
        return
    by_cid: dict[str, Any] = {}
    for info in infos or ():
        cid = getattr(info, "creature_id", "") or ""
        if cid:
            by_cid[cid] = info
    for meta in meta_registry.values():
        if not meta.get("on_node"):
            continue
        cid = meta.get("creature_id") or ""
        info = by_cid.get(cid)
        if info is None:
            continue
        new_model = str(getattr(info, "model", "") or "")
        new_llm_name = str(getattr(info, "llm_name", "") or "")
        if new_model:
            meta["model"] = new_model
        if new_llm_name:
            meta["llm_name"] = new_llm_name
        meta["running"] = bool(getattr(info, "is_running", meta.get("running", True)))
        meta["is_privileged"] = bool(
            getattr(info, "is_privileged", meta.get("is_privileged", False))
        )
