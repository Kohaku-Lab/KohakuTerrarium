"""Procedural-skill discovery across project / user / creature / packages.

Priority order (spec 1.3 "more limited scope = higher priority"):

1. project (``.kt/skills``, ``.claude/skills``, ``.agents/skills``)
2. user    (``~/.kohakuterrarium/skills``, ``~/.claude/skills``,
            ``~/.agents/skills``)
3. creature (``<agent>/prompts/skills``)
4. package  (``resolve_package_skills`` / ``list_package_skills``
             from :mod:`kohakuterrarium.packages_manifest`)

Skills are **last-wins** across origins (spec 1.1 skills exception).
The registry is populated in *reverse* priority order — packages first,
project last — so higher-priority origins overwrite lower ones.

Within a single roots-list, the folder form (``<name>/SKILL.md``) wins
over the flat form (``<name>.md``) when both exist. Collisions *within*
a single origin's root sequence are still last-wins because the user
who dropped two conflicting files locally is expected to understand
the override. A DEBUG log trace is emitted for every supersede.
"""

from pathlib import Path

from kohakuterrarium.packages import get_package_path, list_packages
from kohakuterrarium.skill_docs import parse_frontmatter, read_skill_text
from kohakuterrarium.skills.registry import Skill
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


# Relative roots, scanned under cwd for project and under the home
# directory for user.  Ordering within each group matches the spec's
# discovery-path list in Cluster 4 (native first, Claude-Code interop
# next, .agents ecosystem last).
PROJECT_SKILL_ROOTS: tuple[str, ...] = (
    ".kt/skills",
    ".claude/skills",
    ".agents/skills",
)
USER_SKILL_ROOTS: tuple[str, ...] = (
    ".kohakuterrarium/skills",
    ".claude/skills",
    ".agents/skills",
)
# Creature folder: <agent>/prompts/skills/<name>/SKILL.md is NEW; the
# pre-existing <agent>/prompts/tools/<name>.md convention is *not*
# re-used because Qc explicitly distinguishes procedural skills from
# tool references.
CREATURE_SKILL_SUBDIR: str = "prompts/skills"


# ---------------------------------------------------------------------------
# Low-level loader
# ---------------------------------------------------------------------------


def load_skill_from_path(
    skill_md: Path,
    *,
    origin: str,
    default_name: str | None = None,
) -> Skill | None:
    """Load one ``SKILL.md`` file into a :class:`Skill`.

    Returns ``None`` (with a warning) when the file can't be read or
    parsed — never raises. Bad SKILL.md files in one creature must not
    crash ``kt run`` / ``kt serve``; they're skipped with a log line so
    the user can see what's wrong without losing the whole agent.

    ``default_name`` is used when the frontmatter lacks a ``name`` —
    typically the parent directory name for the folder form, or the
    file stem for the flat form.
    """
    if not skill_md.exists():
        return None

    try:
        text = read_skill_text(skill_md)
        if text is None:
            return None

        frontmatter, body = parse_frontmatter(text)
        if not isinstance(frontmatter, dict):
            frontmatter = {}

        name = str(frontmatter.get("name") or default_name or skill_md.stem).strip()
        if not name:
            logger.warning(
                "Skill file has no usable name; skipping",
                path=str(skill_md),
            )
            return None
        description = str(frontmatter.get("description") or "").strip()
        disable_model = bool(frontmatter.get("disable-model-invocation", False))

        paths = _as_string_list(frontmatter.get("paths"))
        allowed_tools = _as_string_list(frontmatter.get("allowed-tools"))

        return Skill(
            name=name,
            description=description,
            body=body,
            frontmatter=dict(frontmatter),
            base_dir=skill_md.parent,
            origin=origin,
            disable_model_invocation=disable_model,
            paths=paths,
            allowed_tools=allowed_tools,
        )
    except Exception as exc:  # noqa: BLE001 — defensive: never crash the loader
        logger.warning(
            "Failed to load skill; skipping",
            path=str(skill_md),
            origin=origin,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return None


def _as_string_list(value: object) -> list[str]:
    """Normalise YAML-ish ``foo`` / ``foo, bar`` / ``[foo, bar]`` to list.

    Defensive against malformed frontmatter — a dict, set, or other
    surprise type yields an empty list rather than raising.
    """
    if value is None:
        return []
    if isinstance(value, list):
        out: list[str] = []
        for v in value:
            if v is None:
                continue
            try:
                s = str(v).strip()
            except Exception:  # noqa: BLE001
                continue
            if s:
                out.append(s)
        return out
    if isinstance(value, str):
        if "," in value:
            return [part.strip() for part in value.split(",") if part.strip()]
        return [value.strip()] if value.strip() else []
    if isinstance(value, (dict, set)):
        return []
    try:
        return [str(value)]
    except Exception:  # noqa: BLE001
        return []


# ---------------------------------------------------------------------------
# Roots → Skill[]
# ---------------------------------------------------------------------------


def _scan_root(root: Path, *, origin: str) -> list[Skill]:
    """Load every ``<name>/SKILL.md`` or ``<name>.md`` under ``root``.

    Folder form shadows flat form (spec 4.1) when both exist for the
    same name. Per-entry failures are logged and skipped — one corrupt
    SKILL.md must never block the rest of the scan.
    """
    try:
        if not root.exists() or not root.is_dir():
            return []
    except OSError as exc:  # weird filesystem / permission edge cases
        logger.warning("Failed to stat skill root", path=str(root), error=str(exc))
        return []

    # First pass: folder form.
    folder_names: set[str] = set()
    skills: list[Skill] = []
    try:
        entries = sorted(root.iterdir(), key=lambda p: p.name)
    except OSError as exc:
        logger.warning(
            "Failed to iterate skill root",
            path=str(root),
            error=str(exc),
        )
        return []
    for entry in entries:
        try:
            is_dir = entry.is_dir()
        except OSError:
            continue
        if is_dir:
            skill_md = entry / "SKILL.md"
            if not skill_md.exists():
                continue
            skill = load_skill_from_path(
                skill_md, origin=origin, default_name=entry.name
            )
            if skill is None:
                continue
            folder_names.add(skill.name)
            skills.append(skill)

    # Second pass: flat form (shadowed by folder form).
    for entry in entries:
        try:
            is_file = entry.is_file()
        except OSError:
            continue
        if is_file and entry.suffix == ".md" and entry.name != "SKILL.md":
            stem = entry.stem
            skill = load_skill_from_path(entry, origin=origin, default_name=stem)
            if skill is None:
                continue
            if skill.name in folder_names:
                logger.debug(
                    "Flat skill shadowed by folder form",
                    skill_name=skill.name,
                    root=str(root),
                )
                continue
            skills.append(skill)
    return skills


# ---------------------------------------------------------------------------
# Public: walk every origin in the right order.
# ---------------------------------------------------------------------------


def discover_skills(
    *,
    cwd: Path | None = None,
    home: Path | None = None,
    agent_path: Path | None = None,
    declared_package_skills: list[str] | None = None,
    default_enabled_origins: tuple[str, ...] = (
        "project",
        "user",
        "creature",
    ),
) -> list[Skill]:
    """Return skills from every origin in *registration order*.

    Registration order is lowest-priority first (package → creature →
    user → project), so :meth:`SkillRegistry.add` can be called in the
    returned order and higher-priority origins overwrite lower ones
    thanks to the last-wins semantics.

    Args:
        cwd: Project root; defaults to :func:`Path.cwd`.
        home: User home; defaults to :func:`Path.home`.
        agent_path: Creature folder; skills under
            ``<agent>/prompts/skills/`` are loaded if provided.
        declared_package_skills: Names from the current creature's
            ``skills: []`` opt-in list. Packaged skills that are not
            declared here are returned *disabled by default*
            (spec Qa: "packaged skills default disabled=True unless
            the creature config explicitly enables them"). Passing
            ``"*"`` as one of the entries enables *every* discovered
            package skill — opt-in-to-all shorthand.
        default_enabled_origins: Origins that default to enabled=True
            when no scratchpad override exists. Creature / user /
            project are enabled by default; ``package`` is not.
    """
    cwd = cwd or Path.cwd()
    home = home or Path.home()
    declared = set(declared_package_skills or [])
    # ``*`` is a shorthand the creature config can use to opt in to every
    # discovered package skill. It cannot collide with a real skill name
    # because skill names match ``[a-z][a-z0-9-]*``.
    enable_all_packages = "*" in declared
    declared.discard("*")

    collected: list[Skill] = []

    def _safe_extend(
        origin: str, source: str, scan: "callable[[], list[Skill]]"
    ) -> None:
        """Run *scan* and absorb any unexpected failure with a warning.

        ``_scan_root`` and ``_load_package_skills`` already swallow
        per-skill errors; this is the outer guardrail so a busted
        scratchpad / unicode-mangled filesystem path / etc. cannot
        crash the whole agent boot.
        """
        try:
            new_skills = scan()
        except Exception as exc:  # noqa: BLE001 — last-resort catch
            logger.warning(
                "Failed to scan skill source",
                origin=origin,
                source=source,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return
        for skill in new_skills:
            if origin in {"creature", "user", "project"}:
                skill.enabled = origin in default_enabled_origins
            collected.append(skill)

    # 4 — packages (lowest).
    _safe_extend(
        "package",
        "package-manifest",
        lambda: _load_package_skills(
            declared_names=declared,
            enable_all_packages=enable_all_packages,
            default_enabled_origins=default_enabled_origins,
        ),
    )

    # 3 — creature.
    if agent_path is not None:
        creature_root = Path(agent_path) / CREATURE_SKILL_SUBDIR
        _safe_extend(
            "creature",
            str(creature_root),
            lambda: _scan_root(creature_root, origin="creature"),
        )

    # 2 — user.
    for rel in USER_SKILL_ROOTS:
        user_root = home / rel
        _safe_extend(
            "user",
            str(user_root),
            lambda root=user_root: _scan_root(root, origin="user"),
        )

    # 1 — project (highest).
    for rel in PROJECT_SKILL_ROOTS:
        project_root = cwd / rel
        _safe_extend(
            "project",
            str(project_root),
            lambda root=project_root: _scan_root(root, origin="project"),
        )

    return collected


def _load_package_skills(
    *,
    declared_names: set[str],
    enable_all_packages: bool = False,
    default_enabled_origins: tuple[str, ...] = (),
) -> list[Skill]:
    """Resolve every packaged skill into a :class:`Skill` instance.

    Skills are *last-wins* across packages (spec 1.1 skills exception),
    so we iterate every ``(package, entry)`` pair instead of using
    :func:`packages_manifest.list_package_skills` — which hard-errors
    on name collisions.
    """
    try:
        pairs = list_package_skills_with_owner()
    except Exception as exc:
        logger.warning(
            "Failed to enumerate package skills", error=str(exc), exc_info=True
        )
        return []

    skills: list[Skill] = []
    for pkg_name, entry in pairs:
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        path_str = entry.get("path")
        if not path_str:
            logger.debug("Package skill has no path", skill_name=name)
            continue
        pkg_root = _resolve_package_root(pkg_name, entry)
        if pkg_root is None:
            logger.debug(
                "Package root not locatable for skill",
                skill_name=name,
                package=pkg_name,
            )
            continue
        skill_path = _resolve_skill_md(pkg_root / path_str)
        if skill_path is None:
            logger.debug(
                "Package skill path not found",
                skill_name=name,
                path=str(pkg_root / path_str),
            )
            continue
        origin = f"package:{pkg_name}" if pkg_name else "package"
        skill = load_skill_from_path(skill_path, origin=origin, default_name=name)
        if skill is None:
            continue
        # Override frontmatter-derived description with manifest's if present.
        if entry.get("description") and not skill.description:
            skill.description = str(entry["description"])
        # Package skills default disabled (Qa) unless:
        #   - the creature config named this skill in ``skills:``, OR
        #   - the creature config used the ``"*"`` wildcard, OR
        #   - the origin rule puts ``package`` into default_enabled_origins.
        if (
            enable_all_packages
            or "package" in default_enabled_origins
            or skill.name in declared_names
        ):
            skill.enabled = True
        else:
            skill.enabled = False
        skills.append(skill)
    return skills


def _resolve_package_root(pkg_name: str, entry: dict | None) -> Path | None:
    """Find the package root dir for ``pkg_name``."""
    if pkg_name:
        root = get_package_path(pkg_name)
        if root is not None:
            return root
    # Fall back: manifest entries often carry "_root" when tests stub them.
    if entry and entry.get("_root"):
        return Path(str(entry["_root"]))
    return None


def _resolve_skill_md(candidate: Path) -> Path | None:
    """Resolve the skill path to a readable ``SKILL.md`` or ``<name>.md``."""
    if candidate.is_file():
        return candidate
    if candidate.is_dir():
        skill_md = candidate / "SKILL.md"
        if skill_md.is_file():
            return skill_md
    # Flat form: ``<path>/<name>.md`` might be what the manifest pointed at.
    sibling = candidate.with_suffix(".md")
    if sibling.is_file():
        return sibling
    return None


# Re-injection helper — list_package_skills returns entries without the
# owning-package name, so we also need an alternative enumerator that
# preserves it. This is mainly used by tests to stub out the packages
# layer.
def list_package_skills_with_owner() -> list[tuple[str, dict]]:
    """Return ``[(package_name, entry), ...]`` for every packaged skill."""
    out: list[tuple[str, dict]] = []
    for pkg in list_packages():
        pkg_name = pkg.get("name", "")
        for entry in pkg.get("skills", []) or []:
            if not isinstance(entry, dict):
                continue
            merged = dict(entry)
            merged.setdefault("package", pkg_name)
            out.append((pkg_name, merged))
    return out
