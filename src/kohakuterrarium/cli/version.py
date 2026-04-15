import importlib.metadata
import os
import platform
import site
import subprocess
import sys
from pathlib import Path

from kohakuterrarium import __version__


def detect_install_source() -> str:
    """Best-effort detection of how KohakuTerrarium is installed."""
    try:
        dist = importlib.metadata.distribution("KohakuTerrarium")
    except importlib.metadata.PackageNotFoundError:
        return "source checkout (not installed as a distribution)"

    direct_url = None
    try:
        direct_url = dist.read_text("direct_url.json")
    except FileNotFoundError:
        direct_url = None

    if direct_url:
        direct_url_lower = direct_url.lower()
        if '"editable": true' in direct_url_lower:
            return "editable install"
        if '"url": "file://' in direct_url_lower:
            return "local path install"
        if '"vcs_info"' in direct_url_lower:
            return "vcs install"

    package_path = Path(__file__).resolve()
    site_roots = []
    try:
        site_roots.extend(Path(p).resolve() for p in site.getsitepackages())
    except Exception:
        pass
    user_site = site.getusersitepackages()
    if user_site:
        site_roots.append(Path(user_site).resolve())

    if any(root in package_path.parents for root in site_roots):
        return "installed distribution"
    return "source checkout"


def get_package_version() -> str:
    """Return the best available package version string."""
    try:
        return importlib.metadata.version("KohakuTerrarium")
    except importlib.metadata.PackageNotFoundError:
        return __version__


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def get_git_info() -> dict[str, str | bool]:
    """Return best-effort git identity for a source checkout."""
    repo_root = _repo_root()
    git_dir = repo_root / ".git"
    if not git_dir.exists():
        return {
            "available": False,
            "commit": "",
            "short_commit": "",
            "branch": "",
            "dirty": False,
            "summary": "not available in installed artifact",
        }

    try:
        commit = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        short_commit = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        branch = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = (
            subprocess.run(
                ["git", "-C", str(repo_root), "status", "--porcelain"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            != ""
        )
        summary = short_commit or commit or "unknown"
        if dirty and summary:
            summary = f"{summary} (dirty)"
        return {
            "available": True,
            "commit": commit or "",
            "short_commit": short_commit or "",
            "branch": branch or "",
            "dirty": dirty,
            "summary": summary or "unknown",
        }
    except Exception:
        return {
            "available": False,
            "commit": "",
            "short_commit": "",
            "branch": "",
            "dirty": False,
            "summary": "unknown",
        }


def format_version_report(verbose: bool = False) -> str:
    package_path = Path(__file__).resolve().parents[1]
    git = get_git_info()
    lines = [
        "KohakuTerrarium",
        f"  version:      {get_package_version()}",
        f"  install:      {detect_install_source()}",
        f"  package path: {package_path}",
        f"  python:       {platform.python_version()}",
        f"  executable:   {sys.executable}",
        f"  git commit:   {git['summary']}",
    ]

    if git.get("available") and git.get("branch"):
        lines.append(f"  git branch:   {git['branch']}")

    if verbose:
        lines.extend(
            [
                f"  full commit:  {git['commit'] or 'n/a'}",
                f"  platform:     {platform.platform()}",
                f"  system:       {platform.system()} {platform.release()} ({platform.machine()})",
                f"  processor:    {platform.processor() or 'unknown'}",
                f"  cwd:          {Path.cwd()}",
            ]
        )
        virtual_env = os.environ.get("VIRTUAL_ENV")
        if virtual_env:
            lines.append(f"  venv:         {virtual_env}")

    return "\n".join(lines)
