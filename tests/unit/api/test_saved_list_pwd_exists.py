"""Unit tests for the saved-session list ``pwd_exists`` annotation."""

from kohakuterrarium.api.routes.persistence import saved as saved_mod


class _Page:
    def __init__(self, rows):
        self._rows = rows

    def to_dict(self):
        return {
            "sessions": self._rows,
            "total": len(self._rows),
            "offset": 0,
            "limit": 20,
        }


class _Index:
    def __init__(self, rows):
        self._rows = rows

    def list(self, **kwargs):
        return _Page(self._rows)


class TestListPwdExists:
    def test_local_rows_annotated_remote_rows_left_alone(self, monkeypatch, tmp_path):
        rows = [
            {"name": "here", "pwd": str(tmp_path), "node_id": ""},
            {"name": "gone", "pwd": str(tmp_path / "missing"), "node_id": ""},
            {"name": "nopwd", "pwd": "", "node_id": ""},
            {"name": "remote", "pwd": "/worker/only", "node_id": "w1"},
        ]
        monkeypatch.setattr(saved_mod, "_session_dir", lambda: tmp_path)
        monkeypatch.setattr(
            saved_mod, "get_session_index_default", lambda d: _Index(rows)
        )
        out = saved_mod._list_via_index(
            search="",
            sort="last_active",
            order="desc",
            status=None,
            config_type=None,
            node_id=None,
            limit=20,
            offset=0,
            refresh=False,
            full_rescan=False,
        )
        by_name = {r["name"]: r for r in out["sessions"]}
        assert by_name["here"]["pwd_exists"] is True
        assert by_name["gone"]["pwd_exists"] is False
        # No saved pwd → nothing to be missing.
        assert by_name["nopwd"]["pwd_exists"] is True
        # Worker-hosted row: this host cannot stat the worker's disk.
        assert "pwd_exists" not in by_name["remote"]
