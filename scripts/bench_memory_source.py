"""Opt-in source-prefix benchmark. Run from the repository root.

    python scripts/bench_memory_source.py --turns 48 144 320 --samples 5

Uses only throwaway SessionStore files and prints JSONL. The canonical
linear history intentionally has cumulative ancestry, not production traffic.
"""

from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

from kohakuterrarium.session.memory.index import SessionMemory
from kohakuterrarium.session.memory.reconcile import SessionIndexReconciler
from kohakuterrarium.session.store import SessionStore


def _timed(fn):
    started = time.perf_counter_ns()
    result = fn()
    return (time.perf_counter_ns() - started) / 1_000_000, result


def _p50(fn, samples):
    return round(statistics.median(_timed(fn)[0] for _ in range(samples)), 3)


def _run(turns, samples, append_count):
    with tempfile.TemporaryDirectory(prefix="kt-memory-proof-") as directory:
        path = Path(directory) / "session.kohakutr"
        store = SessionStore(path)
        try:
            store.init_meta("bench-session", "agent", "", directory, ["alice"])

            def append(turn):
                store.append_event(
                    "alice",
                    "user_input",
                    {"content": f"needle session turn {turn}"},
                    turn_index=turn,
                    branch_id=1,
                    parent_branch_path=[(t, 1) for t in range(1, turn)],
                )

            for turn in range(1, turns + 1):
                append(turn)

            memory = SessionMemory(str(path), index_all_branches=True)
            try:
                reconciler = SessionIndexReconciler(store, memory)
                reconciler.build(reconciler.capture([], all_agents=True))
                catalogue = memory._state.get("alice:memory_source_catalog")
                catalogue_bytes = len(
                    json.dumps(catalogue, ensure_ascii=False).encode("utf-8")
                )
                branches = memory._state.get("alice:memory_branch_catalog")
                branch_bytes = len(
                    json.dumps(branches, ensure_ascii=False).encode("utf-8")
                )
                cursor_bytes = len(
                    json.dumps(
                        memory._state.get("alice:cursor:keywords"),
                        ensure_ascii=False,
                    ).encode("utf-8")
                )
                counts = {"key_scans": 0, "full_source_reads": 0}
                original_keys = store._event_key_names
                original_events = store.get_memory_events

                def keys():
                    counts["key_scans"] += 1
                    return original_keys()

                def events(agent):
                    counts["full_source_reads"] += 1
                    return original_events(agent)

                store._event_key_names = keys
                store.get_memory_events = events
                selection = {turn: 1 for turn in range(1, turns + 1)}
                steady = {
                    "catalogue_read_p50_ms": _p50(
                        lambda: memory._state.get("alice:memory_source_catalog"),
                        samples,
                    ),
                    "proof_p50_ms": _p50(
                        lambda: reconciler.probe_source_prefix(["alice"]), samples
                    ),
                }
                source = reconciler.probe_source_prefix(["alice"])
                assert source is not None and source.published
                steady["branch_projection_p50_ms"] = _p50(
                    lambda: reconciler.selected_views(
                        source, ["alice"], {"alice": selection}
                    ),
                    samples,
                )

                def prepare():
                    checked, views = reconciler.prepare_keywords(
                        ["alice"], {"alice": selection}
                    )
                    assert checked.published and len(views["alice"]) == len(selection)
                    return views

                steady["prepare_keywords_p50_ms"] = _p50(prepare, samples)
                views = prepare()
                steady["fts_query_p50_ms"] = _p50(
                    lambda: memory.search(
                        "needle", mode="fts", k=3, branch_views=views
                    ),
                    samples,
                )
                steady.update(counts)
                assert counts == {"key_scans": 0, "full_source_reads": 0}

                appends, refreshes = [], []
                for turn in range(turns + 1, turns + append_count + 1):
                    duration, _ = _timed(lambda: append(turn))
                    appends.append(duration)
                    selection[turn] = 1
                    duration, (checked, _) = _timed(
                        lambda: reconciler.prepare_keywords(
                            ["alice"], {"alice": selection}
                        )
                    )
                    assert not checked.published
                    refreshes.append(duration)
                incremental = {
                    "writer_append_p50_ms": round(statistics.median(appends), 3),
                    "index_refresh_p50_ms": round(statistics.median(refreshes), 3),
                    **counts,
                }
                assert counts == {"key_scans": 0, "full_source_reads": 0}

                # Failed Append consumes a sequence without publishing a row.
                with patch.object(
                    store._branch_maxima,
                    "append",
                    side_effect=OSError("injected write failure"),
                ):
                    try:
                        append(turns + append_count + 1)
                    except OSError:
                        pass
                    else:
                        raise AssertionError("failure injection did not run")
                next_turn = turns + append_count + 2
                append(next_turn)
                selection[next_turn] = 1
                before = dict(counts)
                duration, (checked, views) = _timed(
                    lambda: reconciler.prepare_keywords(["alice"], {"alice": selection})
                )
                assert not checked.published
                assert memory.search("needle", mode="fts", k=3, branch_views=views)
                recovery = {
                    "refresh_ms": round(duration, 3),
                    "key_scans": counts["key_scans"] - before["key_scans"],
                    "full_source_reads": (
                        counts["full_source_reads"] - before["full_source_reads"]
                    ),
                }
                assert recovery["key_scans"] and recovery["full_source_reads"]
                return {
                    "turns": turns,
                    "samples": samples,
                    "append_count": append_count,
                    "catalogue_bytes": catalogue_bytes,
                    "branch_candidates_bytes": branch_bytes,
                    "keyword_cursor_bytes": cursor_bytes,
                    "steady": steady,
                    "append": incremental,
                    "gap_recovery": recovery,
                }
            finally:
                memory.close()
        finally:
            store.close(update_status=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--turns", type=int, nargs="+", default=[48, 144, 320])
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--append-count", type=int, default=3)
    args = parser.parse_args()
    if args.samples < 1 or args.append_count < 1 or any(n < 2 for n in args.turns):
        parser.error("turns >= 2, samples >= 1, append-count >= 1 required")
    for turns in args.turns:
        print(json.dumps(_run(turns, args.samples, args.append_count)), flush=True)


if __name__ == "__main__":
    main()
