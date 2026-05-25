"""Real-dulwich integration tests for the git-backend ref-checkout path.

The other ``test_git_backend.py`` tests stub ``dulwich.porcelain`` to keep
the unit suite fast and avoid filesystem coupling.  But "passes when
mocked" is not the same as "passes against real dulwich" — and dulwich
is the *only* backend on Chaquopy Android, so silent ref-resolution
bugs in production would land users in the worst place to debug them.

These tests therefore force the dulwich backend and drive it against a
real local bare repo (no network).  Coverage targets the matrix that
matters for marketplace pinning:

  * tag ref     — ``kt install @x@v1.0`` (most common).
  * branch ref  — ``kt install @x@main`` (the kt-biome default).
  * commit SHA  — ``kt install`` with a ``version.commit`` populated.
  * bad ref     — error path: target dir torn down + RuntimeError.

The bare repo is built per-test by ``_build_bare_repo`` so each scenario
gets a known-good origin with v1.txt, v2.txt, a tag at v1, and a
feature branch with feature.txt.
"""

import os
import shutil
from pathlib import Path

import pytest

from kohakuterrarium.packages import git_backend


@pytest.fixture(autouse=True)
def _force_dulwich(monkeypatch):
    """Pin every test in this module to the dulwich backend.

    Without this the suite would use native git on most dev machines
    and silently bypass the code under test.
    """
    monkeypatch.setattr(git_backend.shutil, "which", lambda _: None)
    git_backend._reset_backend_cache_for_tests()
    yield
    git_backend._reset_backend_cache_for_tests()


def _build_bare_repo(tmp_path: Path) -> tuple[str, dict[str, str]]:
    """Create a local bare repo with two commits, a tag, and a branch.

    Returns ``(bare_url, refs)`` where ``refs`` carries the SHAs of
    the two commits keyed by content marker ("v1", "v2").  The bare
    URL is just the absolute path — dulwich's local-clone accepts
    plain paths.
    """
    from dulwich import porcelain
    from dulwich.repo import Repo

    bare = tmp_path / "origin.git"
    porcelain.init(str(bare), bare=True)

    work = tmp_path / "work"
    porcelain.clone(str(bare), str(work))

    author = b"Test <test@example.com>"

    # Commit v1 + push.
    (work / "v1.txt").write_text("version one")
    porcelain.add(str(work), [str(work / "v1.txt")])
    porcelain.commit(str(work), message=b"v1", author=author, committer=author)
    porcelain.push(str(work), str(bare), b"refs/heads/master:refs/heads/master")
    repo = Repo(str(work))
    sha_v1 = repo.head().decode()
    repo.close()

    # Tag v1.0 at v1 + push tag.
    porcelain.tag_create(
        str(work),
        b"v1.0",
        author=author,
        message=b"v1.0 release",
        annotated=True,
    )
    porcelain.push(str(work), str(bare), b"refs/tags/v1.0:refs/tags/v1.0")

    # Commit v2 on master + push.
    (work / "v2.txt").write_text("version two")
    porcelain.add(str(work), [str(work / "v2.txt")])
    porcelain.commit(str(work), message=b"v2", author=author, committer=author)
    porcelain.push(str(work), str(bare), b"refs/heads/master:refs/heads/master")
    repo = Repo(str(work))
    sha_v2 = repo.head().decode()
    repo.close()

    # Feature branch off v1 with its own commit, then push.
    porcelain.branch_create(str(work), "feature", objectish=sha_v1)
    porcelain.update_head(str(work), "feature")
    porcelain.reset(Repo(str(work)), "hard", sha_v1.encode())
    (work / "feature.txt").write_text("on a feature branch")
    porcelain.add(str(work), [str(work / "feature.txt")])
    porcelain.commit(str(work), message=b"feature", author=author, committer=author)
    porcelain.push(str(work), str(bare), b"refs/heads/feature:refs/heads/feature")

    # Drop the working clone — tests only need the bare URL.
    shutil.rmtree(work, ignore_errors=True)
    return str(bare), {"v1": sha_v1, "v2": sha_v2}


class TestDulwichRealRefCheckout:
    def test_clone_at_tag_checks_out_tagged_commit(self, tmp_path):
        bare_url, _ = _build_bare_repo(tmp_path)
        target = tmp_path / "out_tag"

        git_backend.clone_repo(bare_url, target, ref="v1.0")

        assert (target / "v1.txt").read_text() == "version one"
        # Tag was created BEFORE the v2 commit on master — v2.txt
        # must not be in the working tree.
        assert not (target / "v2.txt").exists()

    def test_clone_at_branch_checks_out_branch_tip(self, tmp_path):
        bare_url, _ = _build_bare_repo(tmp_path)
        target = tmp_path / "out_branch"

        git_backend.clone_repo(bare_url, target, ref="feature")

        # feature branch was forked from v1 so v1.txt + feature.txt
        # are present but v2.txt is not.
        assert (target / "feature.txt").read_text() == "on a feature branch"
        assert (target / "v1.txt").exists()
        assert not (target / "v2.txt").exists()

    def test_clone_at_commit_sha_resets_to_sha(self, tmp_path):
        bare_url, refs = _build_bare_repo(tmp_path)
        target = tmp_path / "out_sha"

        # Pin to the v1 SHA — dulwich's ``branch=`` doesn't accept
        # raw SHAs, so this exercises the no-depth-clone + reset
        # fallback explicitly.
        git_backend.clone_repo(bare_url, target, ref=refs["v1"])

        assert (target / "v1.txt").read_text() == "version one"
        # Tree must be at the v1 commit — no v2 content, no feature
        # content (those live on master beyond v1 and on a sibling
        # branch respectively).
        assert not (target / "v2.txt").exists()
        assert not (target / "feature.txt").exists()

    def test_clone_at_bad_ref_raises_and_cleans_up(self, tmp_path):
        bare_url, _ = _build_bare_repo(tmp_path)
        target = tmp_path / "out_bad"

        with pytest.raises(RuntimeError, match="Git (clone|checkout)"):
            git_backend.clone_repo(bare_url, target, ref="no-such-ref-anywhere")

        # Target must be torn down so the next install_package
        # call doesn't see ``target.exists()`` and silently fall
        # through to pull-in-place (skipping the requested ref).
        assert not target.exists(), (
            "bad-ref clone left a poisoned target dir — next install "
            "would silently fall through pull-in-place"
        )

    def test_clone_target_can_be_renamed_immediately(self, tmp_path):
        # AUDIT FIX (round-4): ``porcelain.clone`` returns a live
        # ``Repo`` whose open file handles into ``.git`` block
        # ``os.replace`` of the directory on Windows.  The
        # transactional install path does exactly that immediately
        # after clone, so the backend MUST close the returned Repo.
        # This test fails on Windows under the unclosed-Repo
        # variant of the backend; it stays green on POSIX (where
        # rename ignores handles) by accident.  Either way it pins
        # the contract: clone_repo's caller may rename the target
        # without separately closing anything.
        bare_url, _ = _build_bare_repo(tmp_path)
        target = tmp_path / "out_for_rename"
        git_backend.clone_repo(bare_url, target, ref="v1.0")

        renamed = tmp_path / "renamed"
        os.replace(str(target), str(renamed))
        assert (renamed / "v1.txt").exists()
        assert not target.exists()

    def test_clone_without_ref_takes_default_branch(self, tmp_path):
        # Sanity: no-ref path still works against the real backend —
        # establishes the baseline so the ref-specific tests above
        # are isolating ref behaviour, not a general dulwich
        # regression.
        bare_url, _ = _build_bare_repo(tmp_path)
        target = tmp_path / "out_default"

        git_backend.clone_repo(bare_url, target)

        # master tip carries both commits.
        assert (target / "v1.txt").exists()
        assert (target / "v2.txt").exists()
