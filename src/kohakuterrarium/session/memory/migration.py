"""Explicit offline migration of legacy FTS/vector physical source metadata.

The caller must stop all other writers and own a quiescent SessionStore and
Memory index. No Search request performs this migration implicitly.
"""

from dataclasses import dataclass
from pathlib import Path

from kohakuvault import TextVault

from kohakuterrarium.session.memory.blocks import _anchor, extract_batch
from kohakuterrarium.session.memory.navigation import _certify
from kohakuterrarium.session.vault.io import key_text, scan_keys


class UnprovableIndexSource(ValueError):
    """Do not patch index metadata when old block IDs cannot be proved."""


@dataclass(frozen=True)
class BackfillReport:
    fts_patched: int
    vector_patched: int
    fts_present: int
    vector_present: int


def backfill_legacy_sources(store, memory, agent: str) -> BackfillReport:
    """Correlate selected-index block IDs with real keys; never re-embed.

    Store and index must be quiescent under the operator's exclusive control;
    no concurrent rebuild or external SQL mutation is admitted. A partial
    failure leaves independently valid per-row facts, never a global READY
    or complete-source certificate.
    """
    if Path(store.path).resolve() != Path(memory._path).resolve():
        raise UnprovableIndexSource("SessionStore and Memory index use different files")
    store._flush_events_cache()
    source_sequence_before_read = store._event_seq.get(agent)
    keys = scan_keys(store.events, f"{agent}:e")
    events = store.get_events(agent)
    if store._event_seq.get(agent) != source_sequence_before_read:
        raise UnprovableIndexSource("Source changed while capturing backfill input")
    if len(keys) != len(events):
        raise UnprovableIndexSource("Unreadable source event or inconsistent keyset")
    _certify(events)

    # This is an explicit offline operation. Main's ordinary Memory writer
    # does not participate in a separate backfill lock, so a new lock alone
    # would give false cross-instance ownership guarantees. Instead, record
    # the already published per-Agent index cursors and local source frontier.
    # Every mutation is additionally checked against those same snapshots.
    keyword_key = f"{agent}:cursor:keywords"
    keyword_cursor = memory._state.get(keyword_key)
    if (
        not isinstance(keyword_cursor, dict)
        or keyword_cursor.get("count") != len(events)
        or keyword_cursor.get("anchor") != (_anchor(events[-1]) if events else "")
        or memory._state.get(f"{keyword_key}:pending", False)
    ):
        raise UnprovableIndexSource(
            "Keyword cursor does not certify the complete indexed source"
        )

    # The vector table is shared across agents. Its global count is not proof
    # that *this* agent has vectors or a valid vector cursor. Enumerate the
    # target rows once and demand a matching cursor only if they exist.
    vector_rows = []
    dimensions = None
    if memory._vec is not None and memory._vec.count():
        dimensions = memory._state.get("vec_dimensions", memory._embedder.dimensions)
        reader = memory._resources.open(
            TextVault, memory._path, table=f"memory_vec_{dimensions}d"
        )
        try:
            ids = reader.keys(limit=memory._vec.count())
        finally:
            memory._resources.drop(reader)
        for row in ids:
            _, meta = memory._vec.get_by_id(row)
            if not isinstance(meta, dict):
                raise UnprovableIndexSource("Vector row has invalid metadata")
            if meta.get("agent") == agent:
                vector_rows.append((row, meta))

    vector_key = None
    if vector_rows:
        vector_key = f"{agent}:cursor:vectors:{dimensions}"
        vector_cursor = memory._state.get(vector_key)
        if (
            not isinstance(vector_cursor, dict)
            or vector_cursor.get("count") != keyword_cursor.get("count")
            or vector_cursor.get("anchor") != keyword_cursor.get("anchor")
            or memory._state.get(f"{vector_key}:pending", False)
        ):
            raise UnprovableIndexSource(
                "Vector cursor is incomplete or inconsistent with keyword source"
            )

    # Keep the original capture boundary, not a newer sequence observed
    # after vector-row preflight. Appends between source capture and index
    # preparation must invalidate this migration, too.
    next_sequence = source_sequence_before_read
    if store._event_seq.get(agent) != next_sequence:
        raise UnprovableIndexSource(
            "Source or index cursor changed during metadata backfill"
        )
    indexed_generation = memory._state.get(f"{agent}:generation", 0)
    state_keys = [
        keyword_key,
        f"{keyword_key}:revision",
        f"{keyword_key}:pending",
        f"{agent}:indexed_keywords",
    ]
    if vector_key:
        state_keys.extend(
            [
                vector_key,
                f"{vector_key}:revision",
                f"{vector_key}:pending",
                f"{agent}:indexed_vectors:{dimensions}",
            ]
        )
    state_snapshot = {key: memory._state.get(key) for key in state_keys}
    # Large branch cursors need comparison only at operation boundaries. The
    # short per-row fence checks monotonic source sequence, generation,
    # revision, pending and indexed count without unpacking long cursors N times.
    cursor_keys = {keyword_key, vector_key}
    fast_keys = tuple(key for key in state_keys if key not in cursor_keys)
    source_anchor = _anchor(events[-1]) if events else ""

    def assert_unchanged(*, full: bool = False) -> None:
        changed = (
            store._event_seq.get(agent) != next_sequence
            or memory._state.get(f"{agent}:generation", 0) != indexed_generation
            or any(memory._state.get(key) != state_snapshot[key] for key in fast_keys)
        )
        if not changed and full:
            changed = any(
                memory._state.get(key) != state_snapshot[key]
                for key in cursor_keys
                if key is not None
            )
        if changed:
            raise UnprovableIndexSource(
                "Source or index cursor changed during metadata backfill"
            )

    assert_unchanged(full=True)
    refs: list[str] = []
    for key, event in zip(keys, events):
        ref = key_text(key)
        prefix = f"{agent}:e"
        suffix = ref[len(prefix) :] if ref.startswith(prefix) else ""
        if not suffix.isascii() or not suffix.isdecimal():
            raise UnprovableIndexSource("Noncanonical physical event key")
        # The old block ID is based on event ordinal. Re-check its exact key
        # independently rather than assuming ordinal == physical sequence.
        if _anchor(store.events[key]) != _anchor(event):
            raise UnprovableIndexSource("Physical key and selected source disagree")
        refs.append(ref)
    blocks, _ = extract_batch(agent, events)
    proof = {}
    for block in blocks:
        try:
            ordinal = int(block.block_id.split(":e", 1)[1].split(":p", 1)[0])
        except (IndexError, ValueError) as exc:
            raise UnprovableIndexSource("Invalid historical block ID") from exc
        if not 0 <= ordinal < len(events):
            raise UnprovableIndexSource("Block is outside physical source")
        proof[block.block_id] = (
            block.content,
            block.block_type,
            refs[ordinal],
            _anchor(events[ordinal]),
        )

    def candidate(meta, content):
        ident = meta.get("block_id")
        if ident not in proof:
            raise UnprovableIndexSource(f"Indexed block {ident!r} is not selected")
        expected, kind, ref, fingerprint = proof[ident]
        if content != expected or meta.get("type") != kind:
            raise UnprovableIndexSource(f"Source text/type differs for {ident!r}")
        if meta.get("source_ref") not in (None, ref) or meta.get(
            "source_fingerprint"
        ) not in (None, fingerprint):
            raise UnprovableIndexSource(f"Existing provenance differs for {ident!r}")
        return ref, fingerprint

    # Stage *all* checks before mutating either table. A separate full-index
    # ready marker is deliberately not introduced by this experiment.
    fts_updates = []
    fts_present = 0
    for row in memory._fts.keys(limit=memory._fts.count()):
        text, meta = memory._fts.get_by_id(row)
        if meta.get("agent") != agent:
            continue
        ref, fingerprint = candidate(meta, text)
        if (
            meta.get("source_ref") == ref
            and meta.get("source_fingerprint") == fingerprint
        ):
            fts_present += 1
        else:
            fts_updates.append(
                (row, {**meta, "source_ref": ref, "source_fingerprint": fingerprint})
            )

    vec_updates = []
    vec_present = 0
    for row, meta in vector_rows:
        ref, fingerprint = candidate(meta, meta.get("content"))
        if (
            meta.get("source_ref") == ref
            and meta.get("source_fingerprint") == fingerprint
        ):
            vec_present += 1
        else:
            vec_updates.append(
                (row, {**meta, "source_ref": ref, "source_fingerprint": fingerprint})
            )

    # No session-wide READY marker is written. A failure may leave some
    # individually verified rows patched, but does not claim that *all* rows
    # are usable as source anchors. Re-running after the interruption patches
    # only missing rows; no embedding model is invoked.
    assert_unchanged(full=True)
    if events and _anchor(store.events[refs[-1]]) != source_anchor:
        raise UnprovableIndexSource("Physical source changed during preparation")

    for row, meta in fts_updates:
        assert_unchanged()
        expected_text, expected_meta = memory._fts.get_by_id(row)
        old_text = proof[meta["block_id"]][0]
        if expected_text != old_text or (
            {
                k: v
                for k, v in meta.items()
                if k not in {"source_ref", "source_fingerprint"}
            }
            != expected_meta
        ):
            raise UnprovableIndexSource("Keyword row changed during metadata backfill")
        memory._fts.update(row, value=meta)
    for row, meta in vec_updates:
        assert_unchanged()
        _, expected_meta = memory._vec.get_by_id(row)
        if {
            k: v
            for k, v in meta.items()
            if k not in {"source_ref", "source_fingerprint"}
        } != expected_meta:
            raise UnprovableIndexSource("Vector row changed during metadata backfill")
        memory._vec.update(row, value=meta)
    assert_unchanged(full=True)
    return BackfillReport(len(fts_updates), len(vec_updates), fts_present, vec_present)
