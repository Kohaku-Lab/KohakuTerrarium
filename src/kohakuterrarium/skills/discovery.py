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
from kohakuterrarium.prompt.skill_loader import parse_frontmatter
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

    ``default_name`` is used when the frontmatter lacks a ``name`` —
    typically the parent directory name for the folder form, or the
    file stem for the flat form.
    """
    if not skill_md.exists():
        return None

    try:
        text = skill_md.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("Failed to read skill file", path=str(skill_md), error=str(exc))
        return None

    frontmatter, body = parse_frontmatter(text)

    name = str(frontmatter.get("name") or default_name or skill_md.stem).strip()
    description = str(frontmatter.get("description") or "").strip()
    disable_model = bool(frontmatter.get("disable-model-invocation", False))

    paths = _as_string_list(frontmatter.get("paths"))
    allowed_tools = _as_string_list(frontmatter.get("allowed-tools"))

    base_dir = skill_md.parent if skill_md.name == "SKILL.md" else skill_md.parent

    return Skill(
        name=name,
        description=description,
        body=body,
        frontmatter=dict(frontmatter),
        base_dir=base_dir,
        origin=origin,
        disable_model_invocation=disable_model,
        paths=paths,
        allowed_tools=allowed_tools,
    )


def _as_string_list(value: object) -> list[str]:
    """Normalise YAML-ish ``foo`` / ``foo, bar`` / ``[foo, bar]`` to list."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        if "," in value:
            return [part.strip() for part in value.split(",") if part.strip()]
        return [value.strip()] if value.strip() else []
    return [str(value)]


# ---------------------------------------------------------------------------
# Roots → Skill[]
# ---------------------------------------------------------------------------


def _scan_root(root: Path, *, origin: str) -> list[Skill]:
    """Load every ``<name>/SKILL.md`` or ``<name>.md`` under ``root``.

    Folder form shadows flat form (spec 4.1) when both exist for the
    same name.
    """
    if not root.exists() or not root.is_dir():
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
        if entry.is_dir():
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
        if entry.is_file() and entry.suffix == ".md" and entry.name != "SKILL.md":
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
            the creature config explicitly enables them").
        default_enabled_origins: Origins that default to enabled=True
            when no scratchpad override exists. Creature / user /
            project are enabled by default; ``package`` is not.
    """
    cwd = cwd or Path.cwd()
    home = home or Path.home()
    declared = set(declared_package_skills or [])

    collected: list[Skill] = []

    # 4 — packages (lowest).
    collected.extend(
        _load_package_skills(
            declared_names=declared,
            default_enabled_origins=default_enabled_origins,
        )
    )

    # 3 — creature.
    if agent_path is not None:
        creature_root = Path(agent_path) / CREATURE_SKILL_SUBDIR
        for skill in _scan_root(creature_root, origin="creature"):
            skill.enabled = "creature" in default_enabled_origins
            collected.append(skill)

    # 2 — user.
    for rel in USER_SKILL_ROOTS:
        for skill in _scan_root(home / rel, origin="user"):
            skill.enabled = "user" in default_enabled_origins
            collected.append(skill)

    # 1 — project (highest).
    for rel in PROJECT_SKILL_ROOTS:
        for skill in _scan_root(cwd / rel, origin="project"):
            skill.enabled = "project" in default_enabled_origins
            collected.append(skill)

    return collected


def _load_package_skills(
    *,
    declared_names: set[str],
    default_enabled_origins: tuple[str, ...],
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
        # Package skills default disabled (Qa) unless the creature config
        # opted-in.  But if the origin rule puts "package" into
        # default_enabled_origins, we honour that override.
        if "package" in default_enabled_origins:
            skill.enabled = True
        else:
            skill.enabled = skill.name in declared_names
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
