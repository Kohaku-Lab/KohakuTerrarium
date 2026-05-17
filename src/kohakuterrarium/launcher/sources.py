"""Resolve a :class:`SourceConfig` to a concrete ``pip install`` arg list.

``resolve_pip_args(source)`` returns the arguments to pass to
``pip install`` (without the ``pip install`` prefix itself).  Caller
runs ``[<venv-python>, "-m", "pip", "install", *args]``.

See ``plans/1.5.0-roadmap/06-app-update/design.md`` §4 for the
mapping table.
"""

from kohakuterrarium.launcher.paths import bundled_wheels_dir
from kohakuterrarium.launcher.settings import SourceConfig

PACKAGE_NAME = "kohakuterrarium"


def _with_extras(name: str, extras: list[str]) -> str:
    if not extras:
        return name
    return f"{name}[{','.join(extras)}]"


def is_auto_update_allowed(source: SourceConfig) -> bool:
    """``local`` editable installs are user-managed; auto-update disabled."""
    return source.kind in ("pypi", "git", "bundled")


def resolve_pip_args(source: SourceConfig) -> list[str]:
    """Return the ``pip install`` args for ``source``.

    Raises :class:`ValueError` on an unknown ``source.kind`` (validation
    layer in :mod:`launcher.settings` should make this unreachable).
    Raises :class:`FileNotFoundError` for ``kind=bundled`` when no
    bundled wheels directory is present (dev install).
    """
    extras = list(source.extras or [])
    spec = source.spec

    if source.kind == "pypi":
        pinned = (spec or "").strip()
        target = _with_extras(PACKAGE_NAME, extras) + pinned
        return ["-U", target]

    if source.kind == "git":
        if not spec:
            raise ValueError("source.spec is required for kind='git'")
        # ``pip install -U "git+<url>#egg=name[extras]"`` works across
        # branches / tags / commits; the ``#egg=`` fragment is the
        # canonical way to attach extras to a VCS install.
        suffix = ""
        if extras:
            suffix = f"#egg={_with_extras(PACKAGE_NAME, extras)}"
        full = (
            f"git+{spec}{suffix}" if not spec.startswith("git+") else f"{spec}{suffix}"
        )
        return ["-U", full]

    if source.kind == "local":
        if not spec:
            raise ValueError("source.spec is required for kind='local'")
        # Editable install — the trailing extras attach to the path.
        path_with_extras = _with_extras(spec, extras)
        return ["-e", path_with_extras]

    if source.kind == "bundled":
        wheels = bundled_wheels_dir()
        if wheels is None:
            raise FileNotFoundError(
                "bundled wheels directory not found; this Briefcase "
                "build did not include wheels-bundle/.  See "
                "scripts/build_wrapper_wheels.py."
            )
        target = _with_extras(PACKAGE_NAME, extras)
        return [
            "--no-index",
            "--find-links",
            str(wheels),
            target,
        ]

    raise ValueError(f"unknown source.kind {source.kind!r}")


__all__ = [
    "PACKAGE_NAME",
    "is_auto_update_allowed",
    "resolve_pip_args",
]
