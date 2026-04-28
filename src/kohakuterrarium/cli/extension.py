"""CLI extension commands -- list and inspect package extension modules.

Thin formatter over :mod:`kohakuterrarium.studio.catalog.builtins` —
the read-side helpers there are the single source of truth shared
with the studio catalog routes.
"""

from kohakuterrarium.studio.catalog.builtins import (
    extension_module_types,
    get_extension_modules,
    list_extension_packages,
)
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)


def extension_list_cli() -> int:
    """Show all installed extension modules (tools, plugins, presets)."""
    packages = list_extension_packages()
    if not packages:
        print("No packages installed.")
        return 0

    module_types = extension_module_types()
    found_any = False
    for pkg in packages:
        counts = {mt: len(pkg.get(mt, [])) for mt in module_types}
        if not any(counts.values()):
            continue

        found_any = True
        editable_tag = " (editable)" if pkg["editable"] else ""
        print(f"{pkg['name']} v{pkg['version']}{editable_tag}")
        if pkg.get("description"):
            print(f"  {pkg['description']}")
        for mt in module_types:
            items = pkg.get(mt, [])
            if items:
                label = mt.replace("_", " ")
                names = [(i["name"] if isinstance(i, dict) else str(i)) for i in items]
                print(f"  {label} ({len(items)}): {', '.join(names)}")
        print()

    if not found_any:
        print("No extension modules found in installed packages.")

    return 0


def extension_info_cli(name: str) -> int:
    """Show details of a specific package's extension modules."""
    packages = list_extension_packages()
    pkg_match = [p for p in packages if p["name"] == name]
    if not pkg_match:
        print(f"Package not found: {name}")
        return 1

    pkg = pkg_match[0]
    editable_tag = " (editable)" if pkg["editable"] else ""
    print(f"Package: {pkg['name']} v{pkg['version']}{editable_tag}")
    if pkg.get("description"):
        print(f"Description: {pkg['description']}")
    print(f"Path: {pkg['path']}")
    print()

    all_types = ("creatures", "terrariums", *extension_module_types())
    for module_type in all_types:
        modules = get_extension_modules(name, module_type)
        if not modules:
            continue
        label = module_type.replace("_", " ").title()
        print(f"{label} ({len(modules)}):")
        for mod in modules:
            if isinstance(mod, dict):
                mod_name = mod.get("name", "?")
                desc = mod.get("description", "")
                line = f"  - {mod_name}"
                if desc:
                    line += f": {desc}"
                if mod.get("module"):
                    line += f"  [{mod['module']}]"
                print(line)
            else:
                print(f"  - {mod}")
        print()

    return 0
