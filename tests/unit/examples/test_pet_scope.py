"""Pet storage scope and package import boundary."""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from kohakuterrarium.modules.ui_owner import OwnerForbiddenError

from examples.packages.virtual_pet.kt_virtual_pet import scope


def test_owner_identity_selects_the_existing_library_root(tmp_path, monkeypatch):
    monkeypatch.setenv("KT_CONFIG_DIR", str(tmp_path))
    assert scope.user_root(None) == (None, tmp_path / "virtual-pet")
    for multi_user in (False, True):
        context = SimpleNamespace(user_id=7, multi_user=multi_user)
        assert scope.user_root(context) == (7, tmp_path / "users/7/virtual-pet")
        assert (tmp_path / "users/7").is_dir()
    for user_id in (None, True, False, 0, -1, 7.0, "7", "../7"):
        with pytest.raises(OwnerForbiddenError):
            scope.user_root(SimpleNamespace(user_id=user_id, multi_user=True))
    assert sorted(path.name for path in (tmp_path / "users").iterdir()) == ["7"]


def test_package_imports_do_not_reach_into_auth_internals():
    for path in Path(scope.__file__).parent.glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            match node:
                case ast.ImportFrom(module=module) if module:
                    imports = [module]
                case ast.Import(names=names):
                    imports = [name.name for name in names]
                case _:
                    continue
            assert not any(
                name == "kohakuterrarium.api.auth"
                or name.startswith("kohakuterrarium.api.auth.")
                for name in imports
            ), f"{path.name}:{node.lineno} imports auth internals"
