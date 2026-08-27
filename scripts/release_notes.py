"""Render release notes as a plain commit list.

Both the tagged-release and the nightly workflow publish notes that are
just the commits since the previous release plus a compare link.
GitHub's ``generate_release_notes`` groups by pull request instead,
which hides direct pushes and collapses a branch into a single line.

Usage::

    python scripts/release_notes.py \
        --since auto --until v2.1.1 \
        --github-repo Kohaku-Lab/KohakuTerrarium \
        --out notes.md
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

# Only final releases anchor a commit list; dev / rc / a / b tags would
# truncate it to the last pre-release instead of the last release.
STABLE_TAG_RE = re.compile(r"^v\d+\.\d+\.\d+$")

# Unit separator: a commit subject can contain any printable byte, so the
# usual tab/pipe delimiters are not safe to split on.
FIELD_SEP = "\x1f"


def parse_args() -> argparse.Namespace:
    """Parse commit-range and rendering options."""
    p = argparse.ArgumentParser(description="Render release notes as a commit list.")
    p.add_argument(
        "--since",
        default="auto",
        help=(
            "Exclusive start of the range: a ref, or 'auto' to use the newest "
            "stable tag reachable from --until, or '' for no lower bound."
        ),
    )
    p.add_argument("--until", default="HEAD", help="Inclusive end of the range.")
    p.add_argument(
        "--repo-path",
        type=Path,
        default=Path("."),
        help="Git checkout to read history from.",
    )
    p.add_argument(
        "--github-repo",
        default="",
        help="owner/name used to build the compare link (omit to skip it).",
    )
    p.add_argument("--intro", default="", help="Text placed above the commit list.")
    p.add_argument("--heading", default="### Commits", help="Commit-list heading.")
    p.add_argument(
        "--include-merges",
        action="store_true",
        help="Keep merge commits (dropped by default: they restate their branch).",
    )
    p.add_argument("--max-commits", type=int, default=300)
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="File to write (default: stdout).",
    )
    return p.parse_args()


def _git(repo: Path, *args: str) -> str:
    """Run git in ``repo`` and return stdout, raising on a non-zero exit."""
    out = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return out.stdout


def _git_quiet(repo: Path, *args: str) -> str | None:
    """Run git in ``repo``, returning None instead of raising on failure."""
    try:
        return _git(repo, *args)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def rev_of(repo: Path, ref: str) -> str | None:
    """Resolve ``ref`` to a commit SHA, or None when it does not exist."""
    out = _git_quiet(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    return out.strip() if out else None


def previous_stable_tag(repo: Path, until: str) -> str | None:
    """Return the newest stable tag reachable from ``until`` but not equal to it."""
    listing = _git_quiet(
        repo, "tag", "--merged", until, "--list", "v*", "--sort=-v:refname"
    )
    if not listing:
        return None
    head_rev = rev_of(repo, until)
    for tag in (line.strip() for line in listing.splitlines()):
        if not STABLE_TAG_RE.match(tag):
            continue
        if head_rev is not None and rev_of(repo, tag) == head_rev:
            continue
        return tag
    return None


def resolve_since(repo: Path, since: str, until: str) -> str | None:
    """Turn the ``--since`` option into a ref that exists, or None for no bound."""
    if since == "auto":
        return previous_stable_tag(repo, until)
    if not since:
        return None
    if rev_of(repo, since) is None:
        # A pruned or force-pushed anchor must not fail the release; fall back
        # to the last stable tag so the notes stay useful.
        print(f"[release-notes] unknown ref {since!r}; falling back to last tag")
        return previous_stable_tag(repo, until)
    return since


def collect_commits(
    repo: Path,
    since: str | None,
    until: str,
    *,
    include_merges: bool,
    max_commits: int,
) -> tuple[list[tuple[str, str]], bool]:
    """Return ``(short_sha, subject)`` pairs plus whether the list was truncated."""
    rng = f"{since}..{until}" if since else until
    args = ["log", f"--pretty=format:%h{FIELD_SEP}%s", f"--max-count={max_commits + 1}"]
    if not include_merges:
        args.append("--no-merges")
    args.append(rng)
    raw = _git(repo, *args)
    commits: list[tuple[str, str]] = []
    for line in raw.splitlines():
        if FIELD_SEP not in line:
            continue
        sha, subject = line.split(FIELD_SEP, 1)
        commits.append((sha.strip(), subject.strip()))
    truncated = len(commits) > max_commits
    return commits[:max_commits], truncated


def render(
    commits: list[tuple[str, str]],
    *,
    truncated: bool,
    since: str | None,
    until: str,
    intro: str,
    heading: str,
    github_repo: str,
) -> str:
    """Render the commit list as GitHub-flavoured markdown."""
    parts: list[str] = []
    if intro:
        parts.append(intro.strip())
    parts.append(heading)
    if commits:
        span = f" since `{since}`" if since else ""
        plural = "" if len(commits) == 1 else "s"
        parts.append(f"{len(commits)} commit{plural}{span}:")
        parts.append("\n".join(f"- `{sha}` {subject}" for sha, subject in commits))
        if truncated:
            parts.append("_(list truncated)_")
    else:
        parts.append("_No commits in this range._")
    if github_repo and since:
        compare = f"https://github.com/{github_repo}/compare/{since}...{until}"
        parts.append(f"**Full changelog**: {compare}")
    return "\n\n".join(parts) + "\n"


def main() -> int:
    """Resolve the commit range, render the notes, and emit them."""
    args = parse_args()
    since = resolve_since(args.repo_path, args.since, args.until)
    commits, truncated = collect_commits(
        args.repo_path,
        since,
        args.until,
        include_merges=args.include_merges,
        max_commits=args.max_commits,
    )
    text = render(
        commits,
        truncated=truncated,
        since=since,
        until=args.until,
        intro=args.intro,
        heading=args.heading,
        github_repo=args.github_repo,
    )
    if args.out is None:
        sys.stdout.write(text)
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"[release-notes] wrote {args.out} ({len(commits)} commits)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
