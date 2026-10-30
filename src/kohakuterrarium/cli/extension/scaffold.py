"""``kt extension new <name> [--ui]``: scaffold a package directory.

Without ``--ui`` it writes a minimal ``kohaku.yaml`` + README. With
``--ui`` it writes a package-wide settings section and a plugin-owned
chat side panel, both talking to their owners through ``useOwner()``,
plus the SDK test double and an optional Vite preset.
"""

import re
import shutil
from importlib import resources
from pathlib import Path

import kohakuterrarium

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

# template file -> path inside the new package ("{module}" is substituted)
UI_FILES: dict[str, str] = {
    "kohaku.yaml.tmpl": "kohaku.yaml",
    "README.md.tmpl": "README.md",
    "ui_owner.py.tmpl": "{module}/ui_owner.py",
    "plugin.py.tmpl": "{module}/plugin.py",
    "api.py.tmpl": "{module}/api.py",
    "index.js.tmpl": "ui/index.js",
    "style.css.tmpl": "ui/style.css",
    "en.json.tmpl": "ui/i18n/en.json",
    "index.test.js.tmpl": "ui/test/index.test.js",
    "vite.config.js.tmpl": "vite.config.js",
    "vitest.config.js.tmpl": "vitest.config.js",
    "package.json.tmpl": "package.json",
}

_PLAIN_MANIFEST = """name: {name}
version: 0.1.0
description: "{name}: a KohakuTerrarium package."
"""


def module_name(name: str) -> str:
    return name.replace("-", "_")


def sdk_testing_dir() -> Path:
    """Where the built dashboard keeps the SDK test double."""
    return Path(kohakuterrarium.__file__).parent / "web_dist" / "ext-shared" / "testing"


def _render(text: str, name: str) -> str:
    return text.replace("{{name}}", name).replace("{{module}}", module_name(name))


def scaffold_package(name: str, target_dir: Path, *, ui: bool) -> list[Path]:
    """Create ``target_dir/<name>``; return every written file.

    Raises:
        ValueError: ``name`` is not a valid package name.
        FileExistsError: the destination exists and is not empty.
    """
    if not _NAME_RE.match(name):
        raise ValueError(f"invalid package name {name!r} (use a-z, 0-9, - and _)")
    root = target_dir / name
    if root.exists() and any(root.iterdir()):
        raise FileExistsError(f"{root} already exists and is not empty")
    written: list[Path] = []

    def write(rel: str, text: str) -> None:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written.append(path)

    if not ui:
        write("kohaku.yaml", _PLAIN_MANIFEST.format(name=name))
        write("README.md", f"# {name}\n")
        return written

    templates = resources.files("kohakuterrarium.cli") / "scaffold" / "ui_extension"
    module = module_name(name)
    for template, rel in UI_FILES.items():
        text = (templates / template).read_text(encoding="utf-8")
        write(rel.replace("{module}", module), _render(text, name))
    write(f"{module}/__init__.py", "")
    testing = sdk_testing_dir()
    if testing.is_dir():
        for source in sorted(testing.iterdir()):
            dest = root / "ui" / "test" / "kt-sdk" / source.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
            written.append(dest)
    return written
