"""Branch coverage for :mod:`kohakuterrarium.studio.editors.workspace_manifest`
— the IO-classification de-dup paths and the resolve/find edge
branches that the happy-path manifest tests don't reach.

Contract source: the docstrings of ``sync_manifest_entry``,
``resolve_manifest_path``, and ``find_module_file`` — inputs and
outputs share one ``io:`` list, so name-matching MUST also respect the
input/output classification, and ``resolve_manifest_path`` must reject
anything that isn't a real file inside the workspace root.
"""

from types import SimpleNamespace

from kohakuterrarium.studio.editors.workspace_manifest import (
    find_module_file,
    resolve_manifest_path,
    sync_manifest_entry,
)

KNOWN_KINDS = ("tools", "subagents", "triggers", "plugins", "inputs", "outputs")


class TestResolveManifestPath:
    def test_none_module_returns_none(self, tmp_path):
        assert resolve_manifest_path(tmp_path, None) is None

    def test_non_string_module_returns_none(self, tmp_path):
        assert resolve_manifest_path(tmp_path, 123) is None  # type: ignore[arg-type]

    def test_nonexistent_module_file_returns_none(self, tmp_path):
        # The dotted module resolves to a path *under* root, but no .py
        # file exists there — the ``is_file()`` guard returns None.
        assert resolve_manifest_path(tmp_path, "no.such.module") is None

    def test_existing_module_file_resolves(self, tmp_path):
        (tmp_path / "pkg").mkdir()
        target = tmp_path / "pkg" / "mod.py"
        target.write_text("x = 1\n", encoding="utf-8")
        assert resolve_manifest_path(tmp_path, "pkg.mod") == target.resolve()


class TestSyncManifestEntryIODedup:
    def test_same_name_opposite_io_class_is_not_a_duplicate(self, tmp_path):
        """inputs and outputs share the one ``io:`` list. An existing
        io entry that classifies as *input* must NOT shadow a newly
        synced *output* of the same name — they are distinct entries.
        """
        (tmp_path / "kohaku.yaml").write_text(
            "io:\n  - name: shared\n    class: SharedInput\n",
            encoding="utf-8",
        )
        out_dir = tmp_path / "outputs"
        out_dir.mkdir()
        py_path = out_dir / "shared.py"
        py_path.write_text("class SharedOutput: ...\n", encoding="utf-8")

        result = sync_manifest_entry(
            tmp_path, "outputs", "shared", py_path, KNOWN_KINDS
        )
        # The existing entry classified as "input" — so syncing the
        # "output" is a genuine ADD, not a no-op dedupe.
        assert result["added"] is True
        assert result["entry"]["name"] == "shared"


class TestFindModuleFileIOClassify:
    def test_manifest_entry_with_wrong_io_class_is_skipped(self, tmp_path):
        """``find_module_file`` for an *input* must skip a manifest
        ``io:`` entry of the same name that classifies as an *output*
        — name match alone is not enough."""
        (tmp_path / "kohaku.yaml").write_text(
            "io:\n"
            "  - name: shared\n"
            "    class: SharedOutput\n"
            "    module: outputs.shared\n",
            encoding="utf-8",
        )
        # The output module file exists, but we are looking for an INPUT.
        (tmp_path / "outputs").mkdir()
        (tmp_path / "outputs" / "shared.py").write_text(
            "class SharedOutput: ...\n", encoding="utf-8"
        )
        kind_dir = tmp_path / "inputs"
        kind_dir.mkdir()
        ws = SimpleNamespace(root_path=tmp_path)

        # No inputs/shared.py on disk, and the only manifest match
        # classifies as an output → the io-class guard skips it → None.
        assert find_module_file(tmp_path, kind_dir, "inputs", "shared", ws) is None

    def test_manifest_entry_with_matching_io_class_resolves(self, tmp_path):
        """The positive control: an ``io:`` entry whose classification
        matches the requested kind DOES resolve to its module file."""
        (tmp_path / "kohaku.yaml").write_text(
            "io:\n"
            "  - name: shared\n"
            "    class: SharedInput\n"
            "    module: inputs.shared\n",
            encoding="utf-8",
        )
        in_dir = tmp_path / "inputs"
        in_dir.mkdir()
        target = in_dir / "shared.py"
        # Named differently from the manifest's module path so the
        # direct-file lookup misses and the manifest branch is taken.
        (in_dir / "shared.py").write_text("class SharedInput: ...\n", encoding="utf-8")
        ws = SimpleNamespace(root_path=tmp_path)
        # kind_dir has no "wanted.py"; the manifest entry "shared"
        # (module inputs.shared) classifies as input and resolves.
        out = find_module_file(
            tmp_path, tmp_path / "nonexistent", "inputs", "shared", ws
        )
        assert out == target.resolve()
