"""B3/B4 — remote-creature ``_meta`` model cache helpers.

The studio's lifecycle layer caches ``model`` / ``llm_name`` /
``running`` / ``is_privileged`` on every remote-hosted ``_meta`` entry
so tab-reopen and ``get_session`` reads survive a brief worker
disconnect without flipping the UI to "No model". The cache is
refreshed lazily on read (``get_session_async``) and eagerly after a
``switch_model`` mutation.

This module owns the cache schema + read/write helpers. Keeping them
out of :mod:`lifecycle` keeps that module under the 1000-line hard cap
mandated by ``tests/unit/test_file_sizes.py`` while leaving the cache
state (``lifecycle._meta``) where every other reader expects it.

The helpers are pure functions over the shared mutable ``_meta`` dict
exported by :mod:`lifecycle`; importing here would be a cycle, so each
function takes the registry as an argument from the lifecycle caller.
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
    """B4: refresh ``_meta`` model cache after switch_model so a tab
    reopen racing a worker disconnect still surfaces the choice."""
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
    """B3/B4: refresh cached model/llm_name from the worker for every
    creature tracked under ``session_id`` (single + cluster members).

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
    """S6-2: refresh cached model/llm_name for EVERY remote-hosted meta
    entry via a single ``service.list_creatures()`` fan-out.

    Worker-side switch_model paths that do not call the host's
    ``/creatures/{cid}/model`` route (the ``/model`` slash command,
    ``PluginContext.switch_model``, and the compact-LLM swap) update
    only the worker's ``Agent.llm`` and never notify the host's
    ``_meta`` cache. Sync read paths — ``lifecycle.list_sessions``,
    ``lifecycle.list_creatures``, and the legacy ``GET /agents``
    aliases — return the stale cached identifier.

    This fan-out brings every cached entry back in sync from a single
    Protocol call, then the sync read paths emit the fresh model.
    Empty worker replies MUST NOT clobber cached values; the user's
    selection is the source of truth in that race.
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
