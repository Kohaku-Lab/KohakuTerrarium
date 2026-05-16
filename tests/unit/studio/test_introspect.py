"""Unit tests for :mod:`kohakuterrarium.studio.catalog.introspect`."""

from kohakuterrarium.studio.catalog import introspect

# ── builtin_schema ───────────────────────────────────────────


class TestBuiltinSchema:
    def test_tools(self):
        out = introspect.builtin_schema("tools")
        assert "params" in out
        assert any(p["name"] == "timeout" for p in out["params"])

    def test_subagents(self):
        out = introspect.builtin_schema("subagents")
        assert any(p["name"] == "max_turns" for p in out["params"])

    def test_plugins(self):
        out = introspect.builtin_schema("plugins")
        assert any(p["name"] == "priority" for p in out["params"])

    def test_triggers_empty(self):
        out = introspect.builtin_schema("triggers")
        assert out == {"params": [], "warnings": []}

    def test_unknown_kind_returns_empty(self):
        out = introspect.builtin_schema("not-a-kind")
        assert out == {"params": [], "warnings": []}


# ── custom_schema ────────────────────────────────────────────


class TestCustomSchema:
    def test_syntax_error(self):
        out = introspect.custom_schema("def broken(:\n", class_name=None)
        assert out["params"] == []
        assert out["warnings"][0]["code"] == "syntax_error"

    def test_class_not_found(self):
        out = introspect.custom_schema("class A:\n    pass\n", class_name="B")
        assert out["warnings"][0]["code"] == "class_not_found"

    def test_no_class_in_source(self):
        out = introspect.custom_schema("x = 1\n", class_name=None)
        assert out["warnings"][0]["code"] == "class_not_found"

    def test_no_init_returns_empty(self):
        out = introspect.custom_schema("class A:\n    pass\n", class_name=None)
        assert out == {"params": [], "warnings": []}

    def test_init_with_typed_params(self):
        out = introspect.custom_schema(
            (
                "class A:\n"
                "    def __init__(self, name: str, age: int = 30):\n"
                "        pass\n"
            ),
            class_name="A",
        )
        names = [p["name"] for p in out["params"]]
        assert "name" in names
        assert "age" in names
        age = next(p for p in out["params"] if p["name"] == "age")
        assert age["default"] == 30
        assert age["required"] is False

    def test_init_with_kwonly_params(self):
        out = introspect.custom_schema(
            (
                "class A:\n"
                "    def __init__(self, *, debug: bool = False, host: str = 'x'):\n"
                "        pass\n"
            ),
            class_name="A",
        )
        names = [p["name"] for p in out["params"]]
        assert "debug" in names and "host" in names

    def test_variadic_warning(self):
        out = introspect.custom_schema(
            (
                "class A:\n"
                "    def __init__(self, *args, **kwargs):\n"
                "        pass\n"
            ),
            class_name="A",
        )
        codes = [w["code"] for w in out["warnings"]]
        assert "variadic_ignored" in codes

    def test_first_class_fallback(self):
        out = introspect.custom_schema(
            (
                "class First:\n"
                "    def __init__(self, x: int = 1):\n"
                "        pass\n"
                "class Second:\n"
                "    pass\n"
            ),
            class_name=None,
        )
        names = [p["name"] for p in out["params"]]
        assert "x" in names

    def test_self_and_hidden_params_excluded(self):
        out = introspect.custom_schema(
            (
                "class A:\n"
                "    def __init__(self, config: dict, x: int):\n"
                "        pass\n"
            ),
            class_name="A",
        )
        # config is hidden, only x surfaces.
        names = [p["name"] for p in out["params"]]
        assert names == ["x"]

    def test_sidecar_schema_used_for_options_init(self):
        sidecar = [{"name": "cost_per_token", "type_hint": "float"}]
        out = introspect.custom_schema(
            ("class A:\n" "    def __init__(self, options: dict):\n" "        pass\n"),
            class_name="A",
            sidecar_schema=sidecar,
        )
        assert out["params"][0]["name"] == "cost_per_token"

    def test_sidecar_ignored_for_rich_init(self):
        # Init has more than the single ``options: dict`` param, so
        # the sidecar is ignored.
        sidecar = [{"name": "k", "type_hint": "str"}]
        out = introspect.custom_schema(
            (
                "class A:\n"
                "    def __init__(self, host: str, options: dict):\n"
                "        pass\n"
            ),
            class_name="A",
            sidecar_schema=sidecar,
        )
        # Real init params used, not sidecar.
        names = [p["name"] for p in out["params"]]
        assert names == ["host", "options"]

    def test_default_with_unevaluable_value(self):
        # Default referencing a name that can't be literal_eval'd → None.
        out = introspect.custom_schema(
            (
                "class A:\n"
                "    def __init__(self, x = some_constant):\n"
                "        pass\n"
            ),
            class_name="A",
        )
        x = next(p for p in out["params"] if p["name"] == "x")
        assert x["default"] is None


# ── _normalize_sidecar_param ────────────────────────────────


class TestNormalizeSidecarParam:
    def test_non_dict_returns_blank(self):
        out = introspect._normalize_sidecar_param("not-a-dict")
        assert out["name"] == ""

    def test_dict_fills_defaults(self):
        out = introspect._normalize_sidecar_param({"name": "x", "type_hint": "int"})
        assert out["name"] == "x"
        assert out["type_hint"] == "int"
        assert out["required"] is False


# ── _is_options_dict_init ────────────────────────────────────


class TestIsOptionsDictInit:
    def test_single_options_dict(self):
        assert introspect._is_options_dict_init(
            [{"name": "options", "type_hint": "dict"}]
        )

    def test_multi_params(self):
        assert not introspect._is_options_dict_init(
            [{"name": "options"}, {"name": "x"}]
        )

    def test_wrong_name(self):
        assert not introspect._is_options_dict_init(
            [{"name": "other", "type_hint": "dict"}]
        )

    def test_options_with_any_hint(self):
        assert introspect._is_options_dict_init(
            [{"name": "options", "type_hint": "Any"}]
        )

    def test_options_no_hint(self):
        assert not introspect._is_options_dict_init(
            [{"name": "options", "type_hint": ""}]
        )


# ── resolve_module_source ────────────────────────────────────


class TestResolveModuleSource:
    def test_empty_returns_none(self, tmp_path):
        assert introspect.resolve_module_source(tmp_path, "") is None

    def test_absolute_path(self, tmp_path):
        p = tmp_path / "tool.py"
        p.write_text("# tool")
        out = introspect.resolve_module_source(tmp_path, str(p))
        assert out == "# tool"

    def test_relative_path(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "t.py").write_text("# t")
        out = introspect.resolve_module_source(tmp_path, "sub/t.py")
        assert out == "# t"

    def test_relative_not_py(self, tmp_path):
        # Non-.py file → falls through.
        (tmp_path / "x.txt").write_text("text")
        out = introspect.resolve_module_source(tmp_path, "x.txt")
        assert out is None

    def test_package_ref_success(self, tmp_path, monkeypatch):
        f = tmp_path / "pkg.py"
        f.write_text("# pkg")
        monkeypatch.setattr(introspect, "resolve_package_path", lambda r: f)
        out = introspect.resolve_module_source(tmp_path, "@pkg/tool.py")
        assert out == "# pkg"

    def test_package_ref_failure(self, tmp_path, monkeypatch):
        def boom(r):
            raise FileNotFoundError("no pkg")

        monkeypatch.setattr(introspect, "resolve_package_path", boom)
        out = introspect.resolve_module_source(tmp_path, "@pkg/tool.py")
        assert out is None

    def test_package_ref_not_a_file(self, tmp_path, monkeypatch):
        # Resolve returns a directory.
        monkeypatch.setattr(introspect, "resolve_package_path", lambda r: tmp_path)
        out = introspect.resolve_module_source(tmp_path, "@pkg/dir")
        assert out is None

    def test_dotted_module_in_workspace(self, tmp_path):
        sub = tmp_path / "modules" / "tools"
        sub.mkdir(parents=True)
        (sub / "my_tool.py").write_text("# my_tool")
        out = introspect.resolve_module_source(tmp_path, "modules.tools.my_tool")
        assert out == "# my_tool"

    def test_dotted_module_unresolvable(self, tmp_path, monkeypatch):
        monkeypatch.setattr(introspect, "list_packages", lambda: [])
        # No matching path, no installed package.
        out = introspect.resolve_module_source(tmp_path, "ghost.module.name")
        assert out is None

    def test_relative_py_read_failure_returns_none(self, tmp_path, monkeypatch):
        # The file exists and is .py, but read_text blows up (e.g. a
        # permission error) — the resolver swallows it and returns None
        # rather than propagating an OSError to the editor form.
        p = tmp_path / "tool.py"
        p.write_text("# tool")
        orig = introspect.Path.read_text

        def _boom(self, *a, **k):
            if self == p:
                raise PermissionError("denied")
            return orig(self, *a, **k)

        monkeypatch.setattr(introspect.Path, "read_text", _boom)
        assert introspect.resolve_module_source(tmp_path, "tool.py") is None

    def test_dotted_workspace_candidate_read_failure_returns_none(
        self, tmp_path, monkeypatch
    ):
        # The dotted path resolves to an on-disk workspace file, but the
        # read fails — swallowed, None returned.
        sub = tmp_path / "modules" / "tools"
        sub.mkdir(parents=True)
        candidate = sub / "my_tool.py"
        candidate.write_text("# my_tool")
        orig = introspect.Path.read_text

        def _boom(self, *a, **k):
            if self == candidate:
                raise OSError("io error")
            return orig(self, *a, **k)

        monkeypatch.setattr(introspect.Path, "read_text", _boom)
        out = introspect.resolve_module_source(tmp_path, "modules.tools.my_tool")
        assert out is None

    def test_dotted_module_resolved_via_installed_package(self, tmp_path, monkeypatch):
        # No workspace file, but importlib.find_spec locates the dotted
        # module in an installed package — its source is returned.
        pkg_file = tmp_path / "installed_mod.py"
        pkg_file.write_text("# installed source")

        class _Spec:
            origin = str(pkg_file)

        monkeypatch.setattr(introspect, "list_packages", lambda: [])
        monkeypatch.setattr(
            introspect.importlib.util, "find_spec", lambda name: _Spec()
        )
        out = introspect.resolve_module_source(tmp_path, "some.installed.module")
        assert out == "# installed source"

    def test_dotted_module_spec_origin_read_failure_returns_none(
        self, tmp_path, monkeypatch
    ):
        # find_spec resolves, but reading the spec.origin file fails.
        pkg_file = tmp_path / "installed_mod.py"
        pkg_file.write_text("# installed source")

        class _Spec:
            origin = str(pkg_file)

        monkeypatch.setattr(introspect, "list_packages", lambda: [])
        monkeypatch.setattr(
            introspect.importlib.util, "find_spec", lambda name: _Spec()
        )
        orig = introspect.Path.read_text

        def _boom(self, *a, **k):
            if str(self) == str(pkg_file):
                raise OSError("denied")
            return orig(self, *a, **k)

        monkeypatch.setattr(introspect.Path, "read_text", _boom)
        out = introspect.resolve_module_source(tmp_path, "some.installed.module")
        assert out is None


# ── _extract_init_params exception arms ──────────────────────


class TestExtractInitParamsCorners:
    def test_kwonly_hidden_param_excluded(self):
        # 'config' is a framework-injected kwarg; even as keyword-only it
        # must not surface in the editable form.
        out = introspect.custom_schema(
            (
                "class A:\n"
                "    def __init__(self, *, config: dict, host: str = 'x'):\n"
                "        pass\n"
            ),
            class_name="A",
        )
        names = [p["name"] for p in out["params"]]
        assert "config" not in names
        assert "host" in names

    def test_kwonly_default_unevaluable_becomes_none(self):
        # A keyword-only default referencing a name (not a literal) →
        # default is reported as None, required stays False.
        out = introspect.custom_schema(
            (
                "class A:\n"
                "    def __init__(self, *, retries=SOME_CONSTANT):\n"
                "        pass\n"
            ),
            class_name="A",
        )
        retries = next(p for p in out["params"] if p["name"] == "retries")
        assert retries["default"] is None
        assert retries["required"] is False

    def test_positional_annotation_unparse_failure_yields_none_hint(self, monkeypatch):
        # If ast.unparse fails on an annotation node, the param still
        # surfaces but with type_hint=None instead of crashing.
        real_unparse = introspect.ast.unparse

        def _boom(node):
            raise ValueError("cannot unparse")

        monkeypatch.setattr(introspect.ast, "unparse", _boom)
        try:
            out = introspect.custom_schema(
                "class A:\n    def __init__(self, name: str):\n        pass\n",
                class_name="A",
            )
        finally:
            monkeypatch.setattr(introspect.ast, "unparse", real_unparse)
        name = next(p for p in out["params"] if p["name"] == "name")
        assert name["type_hint"] is None

    def test_kwonly_annotation_unparse_failure_yields_none_hint(self, monkeypatch):
        real_unparse = introspect.ast.unparse

        def _boom(node):
            raise ValueError("cannot unparse")

        monkeypatch.setattr(introspect.ast, "unparse", _boom)
        try:
            out = introspect.custom_schema(
                (
                    "class A:\n"
                    "    def __init__(self, *, host: str = 'x'):\n"
                    "        pass\n"
                ),
                class_name="A",
            )
        finally:
            monkeypatch.setattr(introspect.ast, "unparse", real_unparse)
        host = next(p for p in out["params"] if p["name"] == "host")
        assert host["type_hint"] is None
