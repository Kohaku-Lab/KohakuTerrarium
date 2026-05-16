"""Behavior tests for :mod:`kohakuterrarium.studio.persistence.viewer.paths`.

This module is the single canonical session-file path resolver. The
contract under test:

* ``normalize_session_stem`` strips ``.kohakutr`` / ``.kt`` / ``.kohakutr.vN``
  to recover the stable session name;
* ``all_session_files`` enumerates every on-disk session file form;
* ``resolve_session_path`` prefers the highest version when a v1 rollback
  and a vN live file coexist, then exact-name, then unique-stem, then a
  unique fuzzy substring match — and returns ``None`` on miss / ambiguity;
* ``all_versions_for_session`` returns every file for one logical name;
* ``pick_canonical_per_session`` returns one path per session, the
  highest-versioned when both are present.
"""

from kohakuterrarium.studio.persistence.viewer import paths as p


class TestNormalizeSessionStem:
    def test_strips_kohakutr(self, tmp_path):
        assert p.normalize_session_stem(tmp_path / "alice.kohakutr") == "alice"

    def test_strips_kt(self, tmp_path):
        assert p.normalize_session_stem(tmp_path / "bob.kt") == "bob"

    def test_strips_versioned_suffix(self, tmp_path):
        # foo.kohakutr.v2 → foo (the suffix branch, not the .kohakutr one).
        assert p.normalize_session_stem(tmp_path / "foo.kohakutr.v2") == "foo"

    def test_unrecognised_extension_falls_back_to_stem(self, tmp_path):
        # No known suffix → Path.stem.
        assert p.normalize_session_stem(tmp_path / "weird.bak") == "weird"


class TestAllSessionFiles:
    def test_missing_dir_returns_empty(self, tmp_path):
        assert p.all_session_files(tmp_path / "ghost") == []

    def test_enumerates_every_form(self, tmp_path):
        (tmp_path / "a.kohakutr").write_bytes(b"x")
        (tmp_path / "b.kohakutr.v2").write_bytes(b"x")
        (tmp_path / "c.kt").write_bytes(b"x")
        (tmp_path / "ignored.txt").write_bytes(b"x")
        names = {f.name for f in p.all_session_files(tmp_path)}
        assert names == {"a.kohakutr", "b.kohakutr.v2", "c.kt"}


class TestResolveSessionPath:
    def test_missing_dir_returns_none(self, tmp_path):
        assert p.resolve_session_path("alice", tmp_path / "ghost") is None

    def test_prefers_highest_version(self, tmp_path):
        (tmp_path / "alice.kohakutr").write_bytes(b"v1")
        (tmp_path / "alice.kohakutr.v2").write_bytes(b"v2")
        (tmp_path / "alice.kohakutr.v3").write_bytes(b"v3")
        # v3 wins over v2 and the bare rollback.
        assert p.resolve_session_path("alice", tmp_path).name == "alice.kohakutr.v3"

    def test_exact_name_when_no_versioned_file(self, tmp_path):
        (tmp_path / "alice.kohakutr").write_bytes(b"x")
        assert p.resolve_session_path("alice", tmp_path).name == "alice.kohakutr"

    def test_exact_kt_extension(self, tmp_path):
        (tmp_path / "alice.kt").write_bytes(b"x")
        assert p.resolve_session_path("alice", tmp_path).name == "alice.kt"

    def test_unique_stem_match_via_normalize(self, tmp_path):
        # No exact-extension hit, but exactly one file normalizes to the name.
        (tmp_path / "alice.kohakutr.v5").write_bytes(b"x")
        resolved = p.resolve_session_path("alice", tmp_path)
        assert resolved.name == "alice.kohakutr.v5"

    def test_fuzzy_substring_match_when_unique(self, tmp_path):
        # Name is a substring of exactly one stem → fuzzy resolves it.
        (tmp_path / "my-alice-run.kohakutr").write_bytes(b"x")
        resolved = p.resolve_session_path("alice", tmp_path)
        assert resolved.name == "my-alice-run.kohakutr"

    def test_ambiguous_fuzzy_returns_none(self, tmp_path):
        (tmp_path / "alice-one.kohakutr").write_bytes(b"x")
        (tmp_path / "alice-two.kohakutr").write_bytes(b"x")
        # Two fuzzy candidates → refuse to guess.
        assert p.resolve_session_path("alice", tmp_path) is None

    def test_no_match_returns_none(self, tmp_path):
        (tmp_path / "bob.kohakutr").write_bytes(b"x")
        assert p.resolve_session_path("alice", tmp_path) is None

    def test_multiple_stem_matches_returns_a_path(self, tmp_path):
        # Two files normalize to "alice" but neither is the exact
        # "alice.kohakutr" / "alice.kt" form, and their version tails
        # are non-numeric so the versioned-glob branch is bypassed too.
        # The stem-match branch then resolves to one of them (highest
        # _version_rank) rather than returning None.
        (tmp_path / "alice.kohakutr.vx").write_bytes(b"x")
        (tmp_path / "alice.kohakutr.vbeta").write_bytes(b"x")
        resolved = p.resolve_session_path("alice", tmp_path)
        assert resolved is not None
        assert p.normalize_session_stem(resolved) == "alice"

    def test_no_fuzzy_match_returns_none(self, tmp_path):
        # A name that is neither an exact file, a unique stem, nor a
        # substring of any stem → the fuzzy branch finds nothing → None.
        (tmp_path / "completely-different.kohakutr").write_bytes(b"x")
        assert p.resolve_session_path("zzz", tmp_path) is None


class TestAllVersionsForSession:
    def test_returns_both_v1_and_v2(self, tmp_path):
        (tmp_path / "alice.kohakutr").write_bytes(b"x")
        (tmp_path / "alice.kohakutr.v2").write_bytes(b"x")
        (tmp_path / "bob.kohakutr").write_bytes(b"x")
        versions = {f.name for f in p.all_versions_for_session("alice", tmp_path)}
        assert versions == {"alice.kohakutr", "alice.kohakutr.v2"}


class TestPickCanonicalPerSession:
    def test_one_path_per_logical_session_highest_version(self, tmp_path):
        (tmp_path / "alice.kohakutr").write_bytes(b"x")
        (tmp_path / "alice.kohakutr.v2").write_bytes(b"x")
        (tmp_path / "bob.kohakutr").write_bytes(b"x")
        canonical = {f.name for f in p.pick_canonical_per_session(tmp_path)}
        # alice collapses to its v2; bob stands alone.
        assert canonical == {"alice.kohakutr.v2", "bob.kohakutr"}

    def test_empty_dir_returns_empty(self, tmp_path):
        assert p.pick_canonical_per_session(tmp_path) == []
