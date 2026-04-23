"""Extra manifest-slot resolvers — skills, commands, user_commands, prompts.

Cluster 1 of the extension-point decisions adds four new manifest
fields to ``kohaku.yaml`` beyond the existing
``tools / plugins / llm_presets / io / triggers``:

- ``skills:``         — shared procedural skill bundles (name + path)
- ``commands:``       — controller ``##xxx##`` command handlers
- ``user_commands:``  — user-facing slash commands
- ``prompts:`` / ``templates:``
                      — shared prompt fragment files (include target)

These resolvers scan every installed package, enforce the cross-cutting
collision policy (hard error when two packages declare the same name —
spec §1.1), and return the structured entry so the rest of the
framework can wire it up.

The actual consumers live elsewhere — this module only ships the
manifest-slot plumbing:

- skill discovery reads ``SKILL.md`` from each ``skills:`` entry via
  :mod:`kohakuterrarium.skills.discovery`.
- controller command registration loads each ``commands:`` /
  ``user_commands:`` entry via :mod:`kohakuterrarium.core.controller_plugins`.
- Jinja ``{% include %}`` of ``prompts:`` fragments is wired through
  :func:`resolve_package_prompt` and the helper in
  :mod:`kohakuterrarium.prompt.template`.
"""

from pathlib import Path

from kohakuterrarium.packages import list_packages
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Generic collision-aware scanner (parallels packages._resolve_manifest_entry
# for io/triggers). Returns the full entry dict + owning package name so
# callers can pick which fields they care about.
# ---------------------------------------------------------------------------


def _scan_manifest_field(
    kind: str,
    entry_name: str,
) -> tuple[str, dict] | None:
    """Find a single entry across installed packages.

    Args:
        kind: Manifest field to scan (``"skills"``, ``"commands"``, …).
        entry_name: The entry's ``name`` value.

    Returns:
        ``(package_name, entry_dict)`` if exactly one package declares
        ``entry_name`` under ``kind``. ``None`` if no package declares
        it.

    Raises:
        ValueError: If two or more installed packages declare the same
            ``entry_name`` under ``kind``. Matches the cross-cutting
            collision policy (decisions §1.1).
    """
    matches: list[tuple[str, dict]] = []
    for pkg in list_packages():
        for entry in pkg.get(kind, []) or []:
            if not isinstance(entry, dict):
                continue
            if entry.get("name") != entry_name:
                continue
            matches.append((pkg.get("name", "?"), entry))

    if not matches:
        return None
    if len(matches) > 1:
        conflicting = ", ".join(sorted({m[0] for m in matches}))
        raise ValueError(
            f"Collision for {kind} name {entry_name!r}: declared by "
            f"packages [{conflicting}]. Uninstall one or rename the entry "
            f"in its kohaku.yaml to resolve the conflict."
        )
    return matches[0]


def _list_manifest_field(kind: str) -> dict[str, dict]:
    """Return every declared entry under ``kind`` keyed by ``name``.

    Raises :class:`ValueError` if two packages declare the same name —
    the cross-cutting collision policy applies to bulk enumeration as
    well as single-name lookup so callers don't quietly drop one of
    the conflicting entries.
    """
    out: dict[str, tuple[str, dict]] = {}
    for pkg in list_packages():
        pkg_name = pkg.get("name", "?")
        for entry in pkg.get(kind, []) or []:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if not name:
                continue
            if name in out:
                other_pkg = out[name][0]
                raise ValueError(
                    f"Collision for {kind} name {name!r}: declared by "
                    f"packages [{other_pkg}, {pkg_name}]. Uninstall one "
                    "or rename the entry to resolve the conflict."
                )
            out[name] = (pkg_name, entry)
    return {name: entry for name, (_pkg, entry) in out.items()}


# ---------------------------------------------------------------------------
# A.2 — skills:
# ---------------------------------------------------------------------------


def resolve_package_skills(name: str) -> list[dict] | None:
    """Return the ``skills:`` entries for a single package.

    Args:
        name: Package name (what ``list_packages()`` reports).

    Returns:
        List of skill dicts (each with keys like ``name``, ``path``,
        ``description``), or ``None`` if the package isn't installed.
        An installed package with no skills returns an empty list.
    """
    for pkg in list_packages():
        if pkg.get("name") != name:
            continue
        entries = pkg.get("skills") or []
        return [e for e in entries if isinstance(e, dict)]
    return None


def list_package_skills() -> dict[str, dict]:
    """Return every declared skill across all packages, keyed by name.

    Raises :class:`ValueError` if two packages declare a skill with the
    same name — collision policy §1.1.

    Note: per decisions §1.1 the collision policy differs for skills
    (last-wins documentation) vs commands/user_commands (hard error).
    The *bulk-enumeration* helper here still hard-errors because the
    runtime skill discovery layer (δ, Wave 2) needs a consistent view
    — it can relax this in its consumer if last-wins semantics are
    actually desired at that layer.
    """
    return _list_manifest_field("skills")


# ---------------------------------------------------------------------------
# A.3 — commands: (controller ##xxx## commands)
# ---------------------------------------------------------------------------


def resolve_package_command(name: str) -> dict | None:
    """Resolve a controller command entry by ``name`` across packages.

    Returns the full entry dict — expected keys: ``name``, ``module``,
    ``class`` (or ``class_name``), ``description``, and optional
    ``override: true`` flag (required to shadow a built-in command per
    decisions §3.1).
    """
    found = _scan_manifest_field("commands", name)
    if found is None:
        return None
    return found[1]


def list_package_commands() -> dict[str, dict]:
    """Return every declared controller command across installed packages."""
    return _list_manifest_field("commands")


# ---------------------------------------------------------------------------
# A.4 — user_commands: (slash /xxx commands)
# ---------------------------------------------------------------------------


def resolve_package_user_command(name: str) -> dict | None:
    """Resolve a slash-command entry by name across packages.

    Same shape as :func:`resolve_package_command`. No built-in
    overrides are allowed in v1 — decisions §A.4.
    """
    found = _scan_manifest_field("user_commands", name)
    if found is None:
        return None
    return found[1]


def list_package_user_commands() -> dict[str, dict]:
    """Return every declared slash command across installed packages."""
    return _list_manifest_field("user_commands")


# ---------------------------------------------------------------------------
# A.5 — prompts: / templates: (shared Jinja fragments)
# ---------------------------------------------------------------------------


def resolve_package_prompt(name: str) -> Path | None:
    """Resolve a prompt fragment by name to an absolute file path.

    The manifest declares each fragment under ``prompts:`` (preferred)
    or ``templates:`` (alias) with a ``path`` relative to the package
    root:

    .. code-block:: yaml

        prompts:
          - name: git-safety
            path: prompts/git-safety.md
            description: "Shared git-safety rules"

    Callers (typically :mod:`kohakuterrarium.prompt.template`) feed the
    resolved path into a Jinja ``{% include %}``. Returns ``None`` if no
    package declares the fragment.
    """
    # Prompts use a unified namespace; accept both ``prompts`` and
    # ``templates`` for the same fragment (decisions §1.4 ships just
    # string fragments, the manifest key is interchangeable).
    for kind in ("prompts", "templates"):
        found = _scan_manifest_field(kind, name)
        if found is None:
            continue
        pkg_name, entry = found
        rel_path = entry.get("path")
        if not rel_path:
            logger.warning(
                "Prompt fragment has no path",
                package=pkg_name,
                fragment=name,
            )
            continue
        pkg_root = _package_root_for(pkg_name)
        if pkg_root is None:
            continue
        abs_path = (pkg_root / rel_path).resolve()
        if not abs_path.exists():
            logger.warning(
                "Prompt fragment file missing",
                package=pkg_name,
                fragment=name,
                path=str(abs_path),
            )
            return None
        return abs_path
    return None


def list_package_prompts() -> dict[str, Path]:
    """Return every declared prompt fragment across installed packages.

    Keys are fragment names; values are absolute file paths. Collisions
    across packages raise :class:`ValueError`. Missing files are logged
    and dropped from the result.
    """
    merged: dict[str, Path] = {}
    for kind in ("prompts", "templates"):
        for name, entry in _list_manifest_field(kind).items():
            if name in merged:
                # Cross-key collision (e.g. one package uses ``prompts``
                # and another ``templates`` with the same name).
                raise ValueError(
                    f"Collision for prompt fragment {name!r}: declared "
                    "under both 'prompts:' and 'templates:' manifest "
                    "fields across packages. Pick one key."
                )
            # Re-resolve to an absolute path via the single-entry helper
            # so logging of missing files stays consistent.
            abs_path = resolve_package_prompt(name)
            if abs_path is None:
                continue
            _ = entry  # suppress unused-warning in case path lookup fails
            merged[name] = abs_path
    return merged


def _package_root_for(pkg_name: str) -> Path | None:
    """Locate the installed-package root directory for ``pkg_name``.

    Delegates to :func:`kohakuterrarium.packages.get_package_path` but
    imported lazily to keep the circular-import graph flat (this
    module is imported from ``packages.py`` via re-exports below).
    """
    # Local import avoids a circular at module load — packages.py
    # imports from here at the end of its module body.
    from kohakuterrarium.packages import get_package_path

    return get_package_path(pkg_name)
