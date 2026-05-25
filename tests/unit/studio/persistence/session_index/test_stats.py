"""Unit tests for ``session_index.stats.aggregate_stats``.

The stats helper aggregates over the sidecar directly — no
``.kohakutr`` file is opened.  Tests construct the sidecar with
synthetic :class:`SessionIndexEntry` rows so the aggregation logic
is exercised in isolation from any actual session files.
"""

from datetime import datetime, timezone

import pytest

from kohakuterrarium.studio.persistence.session_index.entry import SessionIndexEntry
from kohakuterrarium.studio.persistence.session_index.stats import (
    _to_ts,
    aggregate_stats,
)
from kohakuterrarium.studio.persistence.session_index.store import SessionIndex


@pytest.fixture
def idx(tmp_path):
    sidecar = tmp_path / ".kt-index.kvault"
    i = SessionIndex(sidecar)
    try:
        yield i
    finally:
        i.close()


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _row(
    *,
    name: str,
    config_type: str = "agent",
    status: str = "running",
    format_version: int = 2,
    agents: list[str] | None = None,
    last_active: str = "",
    created_at: str = "",
) -> SessionIndexEntry:
    return SessionIndexEntry(
        filename=f"{name}.kohakutr",
        name=name,
        file_mtime=1.0,
        file_size=42,
        preview="",
        config_path="",
        agents=list(agents or []),
        pwd="",
        config_type=config_type,
        status=status,
        last_active=last_active,
        created_at=created_at,
        format_version=format_version,
        node_id="",
    )


# ── _to_ts ──────────────────────────────────────────────────


class TestToTs:
    def test_empty_returns_none(self):
        assert _to_ts("") is None

    def test_iso_string_round_trips(self):
        ts = 1_700_000_000.0
        assert _to_ts(_iso(ts)) == pytest.approx(ts, abs=1)

    def test_z_suffix_is_normalized(self):
        # ``datetime.fromisoformat`` on Python 3.10 does not accept the
        # bare ``Z`` suffix — the helper has to rewrite it to +00:00.
        out = _to_ts("2026-05-25T00:00:00Z")
        assert out is not None
        assert out > 0

    def test_garbage_returns_none(self):
        assert _to_ts("not-a-date") is None


# ── aggregate_stats ─────────────────────────────────────────


class TestAggregateStatsEmpty:
    def test_empty_index_returns_zero_shape(self, idx):
        out = aggregate_stats(idx)
        assert out == {
            "count": 0,
            "by_config_type": {},
            "by_status": {},
            "by_recency": {"1d": 0, "7d": 0, "30d": 0, "older": 0},
            "by_format_version": {},
            "agents_top": [],
            "average_age_seconds": None,
        }


class TestAggregateStatsCounts:
    def test_count_matches_inserted_entries(self, idx):
        for n in ("a", "b", "c"):
            idx.upsert(_row(name=n))
        out = aggregate_stats(idx)
        assert out["count"] == 3

    def test_config_type_bucketed(self, idx):
        idx.upsert(_row(name="a", config_type="agent"))
        idx.upsert(_row(name="b", config_type="agent"))
        idx.upsert(_row(name="c", config_type="terrarium"))
        out = aggregate_stats(idx)
        assert out["by_config_type"] == {"agent": 2, "terrarium": 1}

    def test_empty_config_type_buckets_as_unknown(self, idx):
        # Sidecar default for missing meta is "unknown" already; verify
        # an explicit empty string also collapses to "unknown".
        idx.upsert(_row(name="a", config_type=""))
        out = aggregate_stats(idx)
        assert out["by_config_type"].get("unknown") == 1

    def test_status_bucketed(self, idx):
        idx.upsert(_row(name="a", status="running"))
        idx.upsert(_row(name="b", status="paused"))
        idx.upsert(_row(name="c", status="paused"))
        out = aggregate_stats(idx)
        assert out["by_status"] == {"running": 1, "paused": 2}

    def test_empty_status_buckets_as_unknown(self, idx):
        idx.upsert(_row(name="a", status=""))
        out = aggregate_stats(idx)
        assert out["by_status"].get("unknown") == 1

    def test_format_version_keys_are_strings(self, idx):
        idx.upsert(_row(name="a", format_version=1))
        idx.upsert(_row(name="b", format_version=2))
        idx.upsert(_row(name="c", format_version=2))
        out = aggregate_stats(idx)
        assert out["by_format_version"] == {"1": 1, "2": 2}


class TestAggregateStatsAgents:
    def test_agents_top_counts_per_session(self, idx):
        idx.upsert(_row(name="a", agents=["alice", "bob"]))
        idx.upsert(_row(name="b", agents=["alice"]))
        idx.upsert(_row(name="c", agents=["bob", "carol"]))
        out = aggregate_stats(idx)
        # most_common returns sorted by count desc.
        top = dict((a, n) for a, n in out["agents_top"])
        assert top["alice"] == 2
        assert top["bob"] == 2
        assert top["carol"] == 1

    def test_top_5_cap(self, idx):
        # Seven agents in one session — the result is truncated to 5.
        idx.upsert(
            _row(
                name="a",
                agents=["a1", "a2", "a3", "a4", "a5", "a6", "a7"],
            )
        )
        out = aggregate_stats(idx)
        assert len(out["agents_top"]) == 5

    def test_empty_agent_name_skipped(self, idx):
        idx.upsert(_row(name="a", agents=["", "alice", ""]))
        out = aggregate_stats(idx)
        names = [a for a, _ in out["agents_top"]]
        assert names == ["alice"]

    def test_serialized_as_lists(self, idx):
        # Frontend depends on JSON arrays, not tuples; we explicitly
        # convert via list(p).
        idx.upsert(_row(name="a", agents=["alice"]))
        out = aggregate_stats(idx)
        assert isinstance(out["agents_top"][0], list)


class TestAggregateStatsRecency:
    def test_recency_buckets(self, idx, fixed_clock):
        # Anchor each row's last_active relative to a known ``now`` and
        # check it lands in the right bucket.  ``fixed_clock`` (unit
        # conftest fixture) freezes ``time.time`` so the boundaries are
        # deterministic.
        now = fixed_clock.t
        day = 86400
        idx.upsert(_row(name="a", last_active=_iso(now - day // 2)))  # 1d
        idx.upsert(_row(name="b", last_active=_iso(now - 3 * day)))  # 7d
        idx.upsert(_row(name="c", last_active=_iso(now - 15 * day)))  # 30d
        idx.upsert(_row(name="d", last_active=_iso(now - 40 * day)))  # older

        out = aggregate_stats(idx)
        assert out["by_recency"] == {"1d": 1, "7d": 1, "30d": 1, "older": 1}

    def test_missing_timestamp_no_bucket(self, idx):
        idx.upsert(_row(name="a", last_active=""))
        out = aggregate_stats(idx)
        assert out["by_recency"] == {"1d": 0, "7d": 0, "30d": 0, "older": 0}

    def test_garbage_timestamp_no_bucket(self, idx):
        idx.upsert(_row(name="a", last_active="not-a-date"))
        out = aggregate_stats(idx)
        assert out["by_recency"] == {"1d": 0, "7d": 0, "30d": 0, "older": 0}

    def test_falls_back_to_created_at(self, idx, fixed_clock):
        # Blank ``last_active`` falls back to ``created_at`` for recency.
        idx.upsert(_row(name="a", last_active="", created_at=_iso(fixed_clock.t - 100)))
        out = aggregate_stats(idx)
        assert out["by_recency"]["1d"] == 1

    def test_future_timestamp_skipped(self, idx, fixed_clock):
        # Negative ``age`` (file claims to be from the future) must not
        # crash and must not land in any bucket.
        idx.upsert(_row(name="a", last_active=_iso(fixed_clock.t + 86400)))
        out = aggregate_stats(idx)
        assert out["by_recency"] == {"1d": 0, "7d": 0, "30d": 0, "older": 0}
        assert out["average_age_seconds"] is None


class TestAggregateStatsAverageAge:
    def test_average_age_seconds(self, idx, fixed_clock):
        # Two entries 1 hour and 3 hours old → average 2 hours.
        now = fixed_clock.t
        hour = 3600
        idx.upsert(_row(name="a", last_active=_iso(now - hour)))
        idx.upsert(_row(name="b", last_active=_iso(now - 3 * hour)))
        out = aggregate_stats(idx)
        assert out["average_age_seconds"] == pytest.approx(2 * hour, abs=2)

    def test_average_age_none_when_all_blank(self, idx):
        idx.upsert(_row(name="a", last_active=""))
        out = aggregate_stats(idx)
        assert out["average_age_seconds"] is None


# ── iter_entries ───────────────────────────────────────────


class TestIterEntries:
    def test_yields_every_entry(self, idx):
        idx.upsert(_row(name="a"))
        idx.upsert(_row(name="b"))
        names = sorted(e["name"] for e in idx.iter_entries())
        assert names == ["a", "b"]

    def test_strips_internal_search_rowid(self, idx):
        idx.upsert(_row(name="a"))
        # ``_search_rowid`` is set during upsert but must never leak
        # back through iter_entries to the public callers.
        for entry in idx.iter_entries():
            assert "_search_rowid" not in entry

    def test_empty_index_yields_nothing(self, idx):
        assert list(idx.iter_entries()) == []

    def test_max_mtime_returns_zero_when_main_stat_fails(self, tmp_path):
        # Defensive: ``_max_mtime_with_wal`` returns 0.0 when the main
        # file's ``stat()`` raises (file vanished between the listdir
        # that found it and the stat that fingerprints it).
        from kohakuterrarium.studio.persistence.session_index.entry import (
            _max_mtime_with_wal,
        )

        ghost = tmp_path / "no-such.kohakutr"
        assert _max_mtime_with_wal(ghost) == 0.0

    def test_max_mtime_skips_unreadable_sidecar(self, tmp_path, monkeypatch):
        # When ``-wal`` or ``-shm`` exists but ``os.stat`` on it
        # raises, the helper skips that sidecar and continues with
        # the others.  Without this branch a transient OS error on
        # the WAL would crash the entire fingerprint.
        import os as _os

        from kohakuterrarium.studio.persistence.session_index import entry as entry_mod

        main = tmp_path / "x.kohakutr"
        main.write_bytes(b"x")
        wal = tmp_path / "x.kohakutr-wal"
        wal.write_bytes(b"y")
        real_stat = _os.stat

        def _flaky(path, *a, **k):
            if str(path).endswith("-wal"):
                raise OSError("sidecar stat failed")
            return real_stat(path, *a, **k)

        monkeypatch.setattr(entry_mod.os, "stat", _flaky)
        # Main mtime still surfaces — sidecar failure was swallowed.
        assert entry_mod._max_mtime_with_wal(main) == main.stat().st_mtime

    def test_missing_entry_is_skipped(self, idx, monkeypatch):
        # Defensive race-condition branch: ``all_filenames`` yields a
        # key but ``_entries.get`` returns ``None`` because the row
        # was deleted between the two calls.  The iterator must skip
        # rather than yield ``None``.
        idx.upsert(_row(name="a"))
        idx.upsert(_row(name="b"))

        real_get = idx._entries.get

        def _ghost_get(key, default=None):
            if key == "a.kohakutr":
                return None
            return real_get(key)

        monkeypatch.setattr(idx._entries, "get", _ghost_get)
        names = [e["name"] for e in idx.iter_entries()]
        assert names == ["b"]
