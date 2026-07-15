"""Discover procedural skills with deterministic scope precedence.

Registration runs from package to creature to user to project so last-wins
storage favors narrower scopes. Folder-form skills shadow flat files per root.
"""

from pathlib import Path

from kohakuterrarium.packages.locations import get_package_path
from kohakuterrarium.packages.walk import list_packages
from kohakuterrarium.skill_docs import parse_frontmatter, read_skill_text
from kohakuterrarium.skills.registry import Skill
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


# Root order favors native KT paths before interoperability paths.
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
# Procedural skills remain separate from prompts/tools reference documentation.
CREATURE_SKILL_SUBDIR: str = "prompts/skills"


def load_skill_from_path(
    skill_md: Path,
    *,
    origin: str,
    default_name: str | None = None,
) -> Skill | None:
    """Load one skill file without allowing isolated failures to abort startup.

    ``default_name`` supplies the folder or file-derived name when omitted.
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
    except Exception as exc:  # noqa: BLE001 - isolate malformed skills
        logger.warning(
            "Failed to load skill; skipping",
            path=str(skill_md),
            origin=origin,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return None


def _as_string_list(value: object) -> list[str]:
    """Normalize scalar or sequence frontmatter into a safe string list."""
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


def _scan_root(root: Path, *, origin: str) -> list[Skill]:
    """Load folder and flat-form skills, with folder form taking precedence."""
    try:
        if not root.exists() or not root.is_dir():
            return []
    except OSError as exc:  # Filesystem metadata may be unreadable.
        logger.warning("Failed to stat skill root", path=str(root), error=str(exc))
        return []

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
    """Return skills in low-to-high priority registration order.

    Package skills default disabled unless declared, enabled by wildcard, or
    included in ``default_enabled_origins``.
    """
    cwd = cwd or Path.cwd()
    home = home or Path.home()
    declared = set(declared_package_skills or [])
    # ``*`` cannot collide with valid skill names.
    enable_all_packages = "*" in declared
    declared.discard("*")

    collected: list[Skill] = []

    def _safe_extend(
        origin: str, source: str, scan: "callable[[], list[Skill]]"
    ) -> None:
        """Contain source-wide failures after per-skill failures are isolated."""
        try:
            new_skills = scan()
        except Exception as exc:  # noqa: BLE001 - startup isolation boundary
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

    _safe_extend(
        "package",
        "package-manifest",
        lambda: _load_package_skills(
            declared_names=declared,
            enable_all_packages=enable_all_packages,
            default_enabled_origins=default_enabled_origins,
        ),
    )

    if agent_path is not None:
        creature_root = Path(agent_path) / CREATURE_SKILL_SUBDIR
        _safe_extend(
            "creature",
            str(creature_root),
            lambda: _scan_root(creature_root, origin="creature"),
        )

    for rel in USER_SKILL_ROOTS:
        user_root = home / rel
        _safe_extend(
            "user",
            str(user_root),
            lambda root=user_root: _scan_root(root, origin="user"),
        )

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
    """Load every packaged skill while preserving last-wins collisions."""
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
        if entry.get("description") and not skill.description:
            skill.description = str(entry["description"])
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
    """Resolve a package root from installation metadata or test-provided data."""
    if pkg_name:
        root = get_package_path(pkg_name)
        if root is not None:
            return root
    if entry and entry.get("_root"):
        return Path(str(entry["_root"]))
    return None


def _resolve_skill_md(candidate: Path) -> Path | None:
    """Resolve a manifest path to folder-form or flat-form skill Markdown."""
    if candidate.is_file():
        return candidate
    if candidate.is_dir():
        skill_md = candidate / "SKILL.md"
        if skill_md.is_file():
            return skill_md
    sibling = candidate.with_suffix(".md")
    if sibling.is_file():
        return sibling
    return None


def list_package_skills_with_owner() -> list[tuple[str, dict]]:
    """Return packaged skill entries paired with their owning package names."""
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
