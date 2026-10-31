"""The ``ui:`` manifest slot: dashboard contributions declared by a package.

Parses and validates each installed package's ``ui:`` block without
importing any package code, and resolves served asset paths inside the
package's UI root. A malformed block marks only that package invalid
(``UISlot.error``); other packages are unaffected.

Every contribution names an ``owner`` (``package:<pkg>``, ``plugin:<name>``,
``tool:<name>``, ``trigger:<name>``); its mount kind fixes which owner kind
is allowed (``MOUNT_OWNER_KINDS``), and a runtime owner must be a module the
package's own manifest declares.
"""

import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from kohakuterrarium.packages.locations import _packages_dir
from kohakuterrarium.packages.walk import (
    _manifest_mtime,
    list_packages,
    packages_fingerprint,
)

MOUNT_OWNER_KINDS: dict[str, tuple[str, ...]] = {
    "settings": ("package",),
    "tab": ("package",),
    "rail": ("package",),
    "panel": ("plugin",),
    "tool-renderer": ("tool",),
    "trigger-renderer": ("trigger",),
    "widget": ("package", "plugin", "tool", "trigger"),
    "message-renderer": ("package", "plugin", "tool", "trigger"),
    "overlay": ("package",),
    "command": ("package",),
    "status-item": ("package",),
    "action": ("package",),
    "message-decorator": ("package",),
    "file-view": ("package",),
    "graph-badge": ("package",),
    "graph-panel": ("package",),
    "tray-item": ("package",),
    "notification-channel": ("package",),
}
GRAPH_NODE_KINDS: tuple[str, ...] = ("creature", "channel", "group")
FILE_PATTERN_MAX = 16
_FILE_PATTERN_RE = re.compile(
    r"^(\*\.[A-Za-z0-9_+-]+(\.[A-Za-z0-9_+-]+)*|[A-Za-z0-9_][A-Za-z0-9_.+-]{0,63})$"
)
DECORATOR_POSITIONS: tuple[str, ...] = ("header", "footer")
DECORATOR_MIN_HEIGHT = 16
DECORATOR_MAX_HEIGHT = 400
RENDERER_KINDS: tuple[str, ...] = ("widget", "message-renderer")
ACTION_TARGETS: tuple[str, ...] = (
    "message",
    "composer",
    "tab",
    "rail-item",
    "file",
    "graph-node",
    "graph-edge",
)
ACTION_ROLES: tuple[str, ...] = ("assistant", "user", "tool", "system")
STATUS_SIDES: tuple[str, ...] = ("left", "right")
STATUS_MIN_WIDTH = 24
STATUS_MAX_WIDTH = 320
OVERLAY_ANCHORS: tuple[str, ...] = (
    "top-left",
    "top-right",
    "bottom-left",
    "bottom-right",
    "center",
)
OVERLAY_SURFACES: tuple[str, ...] = ("app", "window")
OVERLAY_FLAGS: tuple[str, ...] = (
    "draggable",
    "resizable",
    "clickThrough",
    "defaultVisible",
    "alwaysOnTop",
)
OVERLAY_MAX_SIZE = 4096
_SHORTCUT_RE = re.compile(
    r"^((Ctrl|Alt|Shift|Meta)\+){1,3}([A-Za-z0-9]|F([1-9]|1[0-2]))$"
)
UI_KINDS: tuple[str, ...] = tuple(MOUNT_OWNER_KINDS)
OWNER_KINDS: tuple[str, ...] = ("package", "plugin", "tool", "trigger")
OWNER_MANIFEST_SLOTS: dict[str, str] = {
    "plugin": "plugins",
    "tool": "tools",
    "trigger": "triggers",
}
ISOLATION_MODES: tuple[str, ...] = ("inline", "frame")
DENSITIES: tuple[str, ...] = ("compact", "regular")
DEFAULT_SDK_RANGE = ">=1,<2"
DEFAULT_UI_ROOT = "ui"
SCAN_WINDOW_SECONDS = 2.0

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_REF_RE = re.compile(r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$")
_OWNER_NAME_RE = re.compile(r"^[A-Za-z0-9_][\w.\-]{0,127}$")
_RENDERER_RE = re.compile(
    r"^[a-z0-9][a-z0-9_-]{0,31}(\.[a-z0-9][a-z0-9_-]{0,31}){1,3}$"
)
RESERVED_RENDERER_PREFIXES: tuple[str, ...] = ("kt.", "mcp.")
_COMPARATOR_RE = re.compile(r"^(>=|<=|>|<|=|\^|~)?\d+(\.\d+){0,2}$")

_lock = threading.Lock()
_cache: dict[str, object] = {
    "fingerprint": None,
    "slots": [],
    "checked_at": None,
    "dir_mtime": None,
}


@dataclass(frozen=True)
class UIContribution:
    """One mount-point entry and the module that owns it."""

    kind: str
    id: str
    label: str
    owner: str
    icon: str = ""
    description: str = ""
    order: int = 100
    min_density: str = "compact"
    isolation: str = "inline"
    renderer: str = ""
    overlay: dict = field(default_factory=dict)
    shortcut: str = ""
    keywords: str = ""
    status: dict = field(default_factory=dict)
    action: dict = field(default_factory=dict)
    decorator: dict = field(default_factory=dict)
    dock: bool = False
    file_view: dict = field(default_factory=dict)
    graph: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "id": self.id,
            "label": self.label,
            "owner": self.owner,
            "icon": self.icon,
            "description": self.description,
            "order": self.order,
            "minDensity": self.min_density,
            "isolation": self.isolation,
            "renderer": self.renderer,
            "overlay": dict(self.overlay),
            "shortcut": self.shortcut,
            "keywords": self.keywords,
            "status": dict(self.status),
            "action": {k: list(v) for k, v in self.action.items()},
            "decorator": {
                k: list(v) if isinstance(v, list) else v
                for k, v in self.decorator.items()
            },
            "dock": self.dock,
            "fileView": {
                k: list(v) if isinstance(v, list) else v
                for k, v in self.file_view.items()
            },
            "graph": {k: list(v) for k, v in self.graph.items()},
        }


@dataclass(frozen=True)
class UISlot:
    """A package's validated ``ui:`` block; ``error`` is set when invalid.

    ``fingerprint`` changes only when this package's own manifest or path
    changes; ``declared`` holds the ``(kind, name)`` runtime modules its
    manifest declares.
    """

    package: str
    version: str
    package_root: Path
    ui_root: str = DEFAULT_UI_ROOT
    sdk: str = DEFAULT_SDK_RANGE
    entry: str = ""
    frame: str = ""
    styles: tuple[str, ...] = ()
    api: str = ""
    handler: str = ""
    i18n: dict[str, str] = field(default_factory=dict)
    contributions: tuple[UIContribution, ...] = ()
    error: str = ""
    fingerprint: tuple = ()
    declared: frozenset = frozenset()

    @property
    def ui_dir(self) -> Path:
        return (self.package_root / self.ui_root).resolve()

    def to_dict(self) -> dict:
        return {
            "package": self.package,
            "version": self.version,
            "sdk": self.sdk,
            "entry": self.entry,
            "frame": self.frame,
            "styles": list(self.styles),
            "has_api": bool(self.api),
            "has_handler": bool(self.handler),
            "i18n": dict(self.i18n),
            "contributes": [c.to_dict() for c in self.contributions],
            "error": self.error,
        }


class _SlotError(ValueError):
    """A ``ui:`` block violates the manifest schema."""


def is_valid_sdk_range(text: str) -> bool:
    """Check the comma-separated comparator grammar used by ``ui.sdk``."""
    parts = [p.strip() for p in str(text).split(",")]
    return bool(parts) and all(_COMPARATOR_RE.match(p) for p in parts)


def _relative_inside(value: object, root: str, what: str) -> str:
    """Return ``value`` as a POSIX path relative to ``root``; raise when outside."""
    if not isinstance(value, str) or not value.strip():
        raise _SlotError(f"{what} must be a non-empty path string")
    path = PurePosixPath(value.strip().replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        raise _SlotError(f"{what} {value!r} must be a relative path without '..'")
    root_parts = PurePosixPath(root).parts
    if path.parts[: len(root_parts)] != root_parts or len(path.parts) <= len(
        root_parts
    ):
        raise _SlotError(f"{what} {value!r} must live under the UI root {root!r}/")
    return PurePosixPath(*path.parts[len(root_parts) :]).as_posix()


def _declared_modules(record: dict) -> frozenset:
    """``(kind, name)`` for every plugin / tool / trigger the manifest declares."""
    out = set()
    for kind, slot in OWNER_MANIFEST_SLOTS.items():
        for entry in record.get(slot) or []:
            name = entry.get("name") if isinstance(entry, dict) else entry
            if isinstance(name, str) and name:
                out.add((kind, name))
    return frozenset(out)


def _parse_owner(
    raw: object, kind: str, package: str, where: str, declared: frozenset
) -> str:
    """Normalize a contribution's owner to ``<kind>:<name>`` and check it fits."""
    allowed = MOUNT_OWNER_KINDS[kind]
    if raw is None or raw == "package":
        if "package" not in allowed:
            raise _SlotError(
                f"{where}.owner is required for a {kind} ({allowed[0]}:<name>)"
            )
        return f"package:{package}"
    owner_kind, sep, name = str(raw).partition(":")
    if not sep or owner_kind not in OWNER_KINDS or not _OWNER_NAME_RE.match(name):
        raise _SlotError(f"{where}.owner {raw!r} must be '<kind>:<name>'")
    if owner_kind not in allowed:
        raise _SlotError(
            f"{where}: a {kind} must be owned by {'/'.join(allowed)}, not {owner_kind}"
        )
    if owner_kind == "package" and name != package:
        raise _SlotError(f"{where}.owner may not claim another package ({name!r})")
    if owner_kind != "package" and (owner_kind, name) not in declared:
        slot = OWNER_MANIFEST_SLOTS[owner_kind]
        raise _SlotError(
            f"{where}: the package does not declare {owner_kind} {name!r} "
            f"in its {slot}: slot"
        )
    return f"{owner_kind}:{name}"


def _parse_contribution(
    raw: object, index: int, package: str, declared: frozenset
) -> UIContribution:
    where = f"contributes[{index}]"
    if not isinstance(raw, dict):
        raise _SlotError(f"{where} must be a mapping")
    kind = raw.get("kind")
    if kind not in UI_KINDS:
        raise _SlotError(f"{where}.kind must be one of {list(UI_KINDS)}")
    cid = raw.get("id")
    if not isinstance(cid, str) or not _ID_RE.match(cid):
        raise _SlotError(f"{where}.id must match {_ID_RE.pattern} (got {cid!r})")
    isolation = raw.get("isolation", "inline")
    if isolation not in ISOLATION_MODES:
        raise _SlotError(f"{where}.isolation must be one of {list(ISOLATION_MODES)}")
    density = raw.get("minDensity", raw.get("min_density", "compact"))
    if density not in DENSITIES:
        raise _SlotError(f"{where}.minDensity must be one of {list(DENSITIES)}")
    order = raw.get("order", 100)
    if not isinstance(order, int) or isinstance(order, bool):
        raise _SlotError(f"{where}.order must be an integer")
    renderer = _parse_renderer(raw.get("renderer"), kind, where)
    return UIContribution(
        kind=kind,
        id=cid,
        label=str(raw.get("label") or cid),
        owner=_parse_owner(raw.get("owner"), kind, package, where, declared),
        icon=str(raw.get("icon") or ""),
        description=str(raw.get("description") or ""),
        order=order,
        min_density=density,
        isolation=isolation,
        renderer=renderer,
        overlay=_parse_overlay(raw, kind, where),
        shortcut=_parse_shortcut(raw, kind, where),
        keywords=_parse_keywords(raw, kind, where),
        status=_parse_status(raw, kind, where),
        action=_parse_action(raw, kind, where),
        decorator=_parse_decorator(raw, kind, where),
        dock=_parse_dock(raw, kind, where),
        file_view=_parse_file_view(raw, kind, where),
        graph=_parse_graph(raw, kind, where),
    )


def _parse_file_view(raw: dict, kind: str, where: str) -> dict:
    """File name patterns of a ``file-view``; empty for other kinds."""
    own = ("patterns", "defaultView")
    if kind != "file-view":
        stray = [k for k in own if k in raw]
        if stray:
            raise _SlotError(f"{where}.{stray[0]} is only valid on a file-view")
        return {}
    patterns = raw.get("patterns")
    if (
        not isinstance(patterns, list)
        or not 0 < len(patterns) <= FILE_PATTERN_MAX
        or len(set(patterns)) != len(patterns)
        or not all(isinstance(p, str) and _FILE_PATTERN_RE.match(p) for p in patterns)
    ):
        raise _SlotError(
            f"{where}.patterns must be 1..{FILE_PATTERN_MAX} distinct names such as "
            f"'*.csv' or 'Makefile' (got {patterns!r})"
        )
    default = raw.get("defaultView", False)
    if not isinstance(default, bool):
        raise _SlotError(f"{where}.defaultView must be true or false")
    return {"patterns": list(patterns), "defaultView": default}


def _parse_graph(raw: dict, kind: str, where: str) -> dict:
    """Node kinds a ``graph-badge`` or ``graph-panel`` applies to; empty otherwise."""
    if kind not in ("graph-badge", "graph-panel"):
        if "nodes" in raw:
            raise _SlotError(
                f"{where}.nodes is only valid on a graph-badge or graph-panel"
            )
        return {}
    if "nodes" not in raw:
        return {"nodes": list(GRAPH_NODE_KINDS)}
    return {"nodes": _choice_list(raw, "nodes", GRAPH_NODE_KINDS, where)}


def _parse_dock(raw: dict, kind: str, where: str) -> bool:
    """Whether a ``panel`` also opens from the session dock; false for other kinds."""
    value = raw.get("dock")
    if kind != "panel":
        if value is not None:
            raise _SlotError(f"{where}.dock is only valid on a panel")
        return False
    if value is not None and not isinstance(value, bool):
        raise _SlotError(f"{where}.dock must be true or false")
    return bool(value)


def _parse_decorator(raw: dict, kind: str, where: str) -> dict:
    """Placement of a ``message-decorator``; empty for other kinds."""
    if kind != "message-decorator":
        stray = [k for k in ("position", "maxHeight") if k in raw]
        if stray:
            raise _SlotError(f"{where}.{stray[0]} is only valid on a message-decorator")
        return {}
    position = raw.get("position", "footer")
    if position not in DECORATOR_POSITIONS:
        raise _SlotError(f"{where}.position must be one of {list(DECORATOR_POSITIONS)}")
    height = raw.get("maxHeight", 120)
    if (
        not isinstance(height, int)
        or isinstance(height, bool)
        or not DECORATOR_MIN_HEIGHT <= height <= DECORATOR_MAX_HEIGHT
    ):
        raise _SlotError(
            f"{where}.maxHeight must be an integer in "
            f"{DECORATOR_MIN_HEIGHT}..{DECORATOR_MAX_HEIGHT}"
        )
    roles = (
        _choice_list(raw, "roles", ACTION_ROLES, where)
        if "roles" in raw
        else ["assistant"]
    )
    return {"position": position, "maxHeight": height, "roles": roles}


def _choice_list(raw: dict, key: str, allowed: tuple[str, ...], where: str) -> list:
    value = raw.get(key)
    if not isinstance(value, list) or not value:
        raise _SlotError(f"{where}.{key} must be a non-empty list of {list(allowed)}")
    bad = [v for v in value if v not in allowed]
    if bad or len(set(value)) != len(value):
        raise _SlotError(
            f"{where}.{key} must hold distinct values from {list(allowed)} "
            f"(got {value!r})"
        )
    return list(value)


def _parse_action(raw: dict, kind: str, where: str) -> dict:
    """Targets (and message roles) of an ``action``; empty for other kinds."""
    if kind != "action":
        own = ("targets",) if kind == "message-decorator" else ("targets", "roles")
        stray = [k for k in own if k in raw]
        if stray:
            raise _SlotError(f"{where}.{stray[0]} is only valid on an action")
        return {}
    targets = _choice_list(raw, "targets", ACTION_TARGETS, where)
    if "roles" in raw and "message" not in targets:
        raise _SlotError(f"{where}.roles needs the message target")
    out = {"targets": targets}
    if "message" in targets:
        out["roles"] = (
            _choice_list(raw, "roles", ACTION_ROLES, where)
            if "roles" in raw
            else ["assistant"]
        )
    return out


def _parse_status(raw: dict, kind: str, where: str) -> dict:
    """Placement options of a ``status-item``; empty for other kinds."""
    keys = ("side", "maxWidth")
    if kind != "status-item":
        stray = [k for k in keys if k in raw]
        if stray:
            raise _SlotError(f"{where}.{stray[0]} is only valid on a status-item")
        return {}
    side = raw.get("side", "right")
    if side not in STATUS_SIDES:
        raise _SlotError(f"{where}.side must be one of {list(STATUS_SIDES)}")
    width = raw.get("maxWidth", 160)
    if (
        not isinstance(width, int)
        or isinstance(width, bool)
        or not STATUS_MIN_WIDTH <= width <= STATUS_MAX_WIDTH
    ):
        raise _SlotError(
            f"{where}.maxWidth must be an integer in "
            f"{STATUS_MIN_WIDTH}..{STATUS_MAX_WIDTH}"
        )
    return {"side": side, "maxWidth": width}


def _parse_shortcut(raw: dict, kind: str, where: str) -> str:
    """The in-app key combination of a command; empty for other kinds."""
    value = raw.get("shortcut")
    if kind != "command":
        if value is not None:
            raise _SlotError(f"{where}.shortcut is only valid on a command")
        return ""
    if value is None:
        return ""
    modifiers = str(value).split("+")[:-1]
    if (
        not isinstance(value, str)
        or not _SHORTCUT_RE.match(value)
        or not set(modifiers) & {"Ctrl", "Alt", "Meta"}
        or len(set(modifiers)) != len(modifiers)
    ):
        raise _SlotError(
            f"{where}.shortcut must look like 'Ctrl+Shift+P' (a Ctrl, Alt or Meta "
            f"modifier plus one key), got {value!r}"
        )
    return value


def _parse_keywords(raw: dict, kind: str, where: str) -> str:
    """Extra search words of a command; empty for other kinds."""
    value = raw.get("keywords")
    if kind != "command":
        if value is not None:
            raise _SlotError(f"{where}.keywords is only valid on a command")
        return ""
    if value is not None and not isinstance(value, str):
        raise _SlotError(f"{where}.keywords must be a string")
    return value or ""


def _parse_overlay(raw: dict, kind: str, where: str) -> dict:
    """Placement options of an ``overlay`` contribution; empty for other kinds."""
    keys = ("anchor", "surface", "width", "height", *OVERLAY_FLAGS)
    if kind != "overlay":
        stray = [k for k in keys if k in raw]
        if stray:
            raise _SlotError(f"{where}.{stray[0]} is only valid on an overlay")
        return {}
    anchor = raw.get("anchor", "bottom-right")
    if anchor not in OVERLAY_ANCHORS:
        raise _SlotError(f"{where}.anchor must be one of {list(OVERLAY_ANCHORS)}")
    surface = raw.get("surface", "app")
    if surface not in OVERLAY_SURFACES:
        raise _SlotError(f"{where}.surface must be one of {list(OVERLAY_SURFACES)}")
    out: dict = {"anchor": anchor, "surface": surface}
    for name in ("width", "height"):
        value = raw.get(name, 320 if name == "width" else 240)
        if (
            not isinstance(value, int)
            or isinstance(value, bool)
            or not 1 <= value <= OVERLAY_MAX_SIZE
        ):
            raise _SlotError(
                f"{where}.{name} must be an integer in 1..{OVERLAY_MAX_SIZE}"
            )
        out[name] = value
    defaults = {"draggable": True, "resizable": False, "clickThrough": False}
    for flag in OVERLAY_FLAGS:
        value = raw.get(flag, defaults.get(flag, True))
        if not isinstance(value, bool):
            raise _SlotError(f"{where}.{flag} must be true or false")
        out[flag] = value
    return out


def _parse_renderer(raw: object, kind: str, where: str) -> str:
    """The renderer id a widget or message-renderer provides; empty otherwise."""
    if kind not in RENDERER_KINDS:
        if raw is not None:
            raise _SlotError(
                f"{where}.renderer is only valid on a widget or message-renderer"
            )
        return ""
    if not isinstance(raw, str) or not _RENDERER_RE.match(raw):
        raise _SlotError(
            f"{where}.renderer must be a dotted id like 'pkg.board' (got {raw!r})"
        )
    if raw.startswith(RESERVED_RENDERER_PREFIXES):
        raise _SlotError(f"{where}.renderer {raw!r} uses a reserved prefix")
    return raw


def _parse_ref(raw: dict, key: str) -> str:
    value = raw.get(key) or ""
    if value and (not isinstance(value, str) or not _REF_RE.match(value)):
        raise _SlotError(f"{key} {value!r} must be 'dotted.module:attribute'")
    return value


def _parse_root(raw: dict, package_root: Path) -> str:
    """The UI root, which must resolve inside the (resolved) package root."""
    root = raw.get("root", DEFAULT_UI_ROOT)
    if not isinstance(root, str) or not root.strip():
        raise _SlotError("root must be a non-empty path string")
    root_path = PurePosixPath(root.strip().replace("\\", "/"))
    if root_path.is_absolute() or ".." in root_path.parts:
        raise _SlotError(f"root {root!r} must be a relative path without '..'")
    root = root_path.as_posix()
    ui_dir = package_root / root
    if not ui_dir.is_dir():
        raise _SlotError(f"UI root {root!r}/ does not exist in the package")
    if not ui_dir.resolve().is_relative_to(package_root.resolve()):
        raise _SlotError(f"UI root {root!r}/ resolves outside the package")
    return root


def _parse_block(
    raw: dict, package_root: Path, package: str, declared: frozenset
) -> dict:
    """Validate a ``ui:`` mapping and return ``UISlot`` keyword fields."""
    root = _parse_root(raw, package_root)
    sdk = str(raw.get("sdk", DEFAULT_SDK_RANGE))
    if not is_valid_sdk_range(sdk):
        raise _SlotError(f"sdk {sdk!r} is not a valid version range")

    contributions = tuple(
        _parse_contribution(item, i, package, declared)
        for i, item in enumerate(raw.get("contributes") or [])
    )
    seen: set[str] = set()
    for c in contributions:
        if c.id in seen:
            raise _SlotError(f"contribution id {c.id!r} is declared twice")
        seen.add(c.id)

    entry = ""
    if raw.get("entry") is not None:
        entry = _relative_inside(raw.get("entry"), root, "entry")
    if not entry and any(c.isolation == "inline" for c in contributions):
        raise _SlotError("entry is required when any contribution is inline")

    frame = ""
    if raw.get("frame") is not None:
        frame = _relative_inside(raw.get("frame"), root, "frame")
    if not frame and any(c.isolation == "frame" for c in contributions):
        raise _SlotError(
            "frame is required when any contribution uses isolation: frame"
        )

    styles_raw = raw.get("styles") or []
    if not isinstance(styles_raw, list):
        raise _SlotError("styles must be a list of paths")
    styles = tuple(_relative_inside(s, root, "styles entry") for s in styles_raw)

    i18n_raw = raw.get("i18n") or {}
    if not isinstance(i18n_raw, dict):
        raise _SlotError("i18n must map locale -> path")
    i18n = {
        str(locale): _relative_inside(path, root, f"i18n[{locale}]")
        for locale, path in i18n_raw.items()
    }
    return {
        "ui_root": root,
        "sdk": sdk,
        "entry": entry,
        "frame": frame,
        "styles": styles,
        "api": _parse_ref(raw, "api"),
        "handler": _parse_ref(raw, "handler"),
        "i18n": i18n,
        "contributions": contributions,
    }


def parse_ui_slot(record: dict) -> UISlot | None:
    """Build a ``UISlot`` from a ``list_packages`` record; ``None`` without ``ui:``."""
    raw = record.get("ui")
    if raw is None or raw == {}:
        return None
    name = str(record.get("name", "?"))
    version = str(record.get("version", "?"))
    package_root = Path(record.get("path", ".")).resolve()
    declared = _declared_modules(record)
    base = {
        "fingerprint": (str(package_root), version, _manifest_mtime(package_root)),
        "declared": declared,
    }
    if not isinstance(raw, dict):
        return UISlot(
            name, version, package_root, error="ui: must be a mapping", **base
        )
    try:
        fields = _parse_block(raw, package_root, name, declared)
    except _SlotError as e:
        return UISlot(name, version, package_root, error=str(e), **base)
    return UISlot(name, version, package_root, **fields, **base)


def _dir_mtime() -> int:
    try:
        return _packages_dir().stat().st_mtime_ns
    except OSError:
        return 0


def invalidate_ui_cache() -> None:
    """Force the next :func:`list_package_ui` to re-check installed packages."""
    with _lock:
        _cache["checked_at"] = None


def list_package_ui() -> list[UISlot]:
    """Every installed package's ``ui:`` slot.

    Within ``SCAN_WINDOW_SECONDS`` of the last check the cached slots are
    reused unless the packages directory itself changed (an install or
    uninstall); after the window the package fingerprint is re-checked.
    """
    now = time.monotonic()
    dir_mtime = _dir_mtime()
    with _lock:
        checked = _cache["checked_at"]
        if (
            checked is not None
            and now - checked < SCAN_WINDOW_SECONDS
            and _cache["dir_mtime"] == dir_mtime
        ):
            return list(_cache["slots"])
    fingerprint = packages_fingerprint()
    with _lock:
        if _cache["fingerprint"] != fingerprint:
            slots = [parse_ui_slot(record) for record in list_packages()]
            _cache["slots"] = [s for s in slots if s is not None]
            _cache["fingerprint"] = fingerprint
        _cache["checked_at"] = now
        _cache["dir_mtime"] = dir_mtime
        return list(_cache["slots"])


def get_package_ui(package: str) -> UISlot | None:
    """The ``ui:`` slot of one installed package, or ``None``."""
    for slot in list_package_ui():
        if slot.package == package:
            return slot
    return None


def packages_declaring(kind: str, name: str) -> list[UISlot]:
    """UI slots of the packages whose manifest declares module ``kind:name``."""
    return [s for s in list_package_ui() if (kind, name) in s.declared]


def resolve_ui_asset(slot: UISlot, rel_path: str) -> Path | None:
    """Map a served path to a file inside the slot's UI root, or ``None``."""
    if slot.error or not rel_path:
        return None
    parts = PurePosixPath(rel_path.replace("\\", "/")).parts
    if not parts or ".." in parts or parts[0] == "/":
        return None
    ui_dir = slot.ui_dir
    package_root = slot.package_root.resolve()
    candidate = (ui_dir / PurePosixPath(*parts)).resolve()
    if not candidate.is_relative_to(ui_dir) or not candidate.is_relative_to(
        package_root
    ):
        return None
    return candidate if candidate.is_file() else None
