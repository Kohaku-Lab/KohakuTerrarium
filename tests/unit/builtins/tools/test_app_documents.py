"""Unit tests for :mod:`kohakuterrarium.builtins.tools.app_documents`."""

import pytest

from kohakuterrarium.builtins.tools.app_documents import (
    AppDocumentStore,
    PatchError,
    app_spec,
    apply_patch,
    digest,
)
from kohakuterrarium.session.store import SessionStore


class TestPatch:
    def test_edits_apply_in_order(self):
        html = "<p>a</p><p>b</p>"
        out = apply_patch(
            html,
            [
                {"find": "<p>a</p>", "replace": "<p>A</p>"},
                {"find": "A", "replace": "Z"},
            ],
        )
        assert out == "<p>Z</p><p>b</p>"

    @pytest.mark.parametrize(
        "edits, message",
        [
            ([], "non-empty list"),
            ("x", "non-empty list"),
            (["x"], "must be an object"),
            ([{"find": ""}], "non-empty 'find'"),
            ([{"find": "zzz", "replace": ""}], "not found"),
            ([{"find": "p", "replace": "q"}], "matches 4 places"),
        ],
    )
    def test_bad_edits_fail_without_partial_changes(self, edits, message):
        with pytest.raises(PatchError, match=message):
            apply_patch("<p>a</p><p>b</p>", edits)


class TestStore:
    def test_without_session_store_spec_carries_inline_html(self):
        docs = AppDocumentStore()
        doc = docs.put("<b>x</b>", None, {"n": 1})
        docs.bind("w_1", doc, None)
        assert docs.load("w_1", None) is doc
        spec = app_spec(doc, ("esm.sh",))
        assert spec == {
            "sha256": digest("<b>x</b>"),
            "allowed_hosts": ["esm.sh"],
            "data": {"n": 1},
            "html": "<b>x</b>",
        }

    def test_session_store_keeps_documents_and_reloads_after_restart(self, tmp_path):
        store = SessionStore(str(tmp_path / "run.kohakutr"))
        try:
            docs = AppDocumentStore()
            doc = docs.put("<h1>hi</h1>", store, {"k": [1, 2]})
            assert doc.url.startswith("/api/sessions/run/artifacts/apps/docs/")
            assert "html" not in app_spec(doc, ())
            docs.bind("w_abc", doc, store)

            fresh = AppDocumentStore()
            again = fresh.load("w_abc", store)
            assert again is not None
            assert (again.html, again.sha256, again.data, again.url) == (
                "<h1>hi</h1>",
                doc.sha256,
                {"k": [1, 2]},
                doc.url,
            )
            assert fresh.load("w_missing", store) is None
        finally:
            store.close()
