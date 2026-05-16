"""Unit tests for :mod:`kohakuterrarium.studio.editors.codegen_common`."""

import libcst as cst

from kohakuterrarium.studio.editors import codegen_common as cg

# ── parse / find_class / first_class ─────────────────────────


class TestParseAndFind:
    def test_parse_basic(self):
        src = "class A:\n    pass\n"
        tree = cg.parse(src)
        # Parsed tree must serialize back to the original source.
        assert isinstance(tree, cst.Module)
        assert tree.code == src

    def test_find_class_existing(self):
        tree = cg.parse("class A:\n    pass\nclass B:\n    pass\n")
        cls = cg.find_class(tree, "B")
        assert cls.name.value == "B"

    def test_find_class_missing(self):
        tree = cg.parse("class A: pass\n")
        assert cg.find_class(tree, "Ghost") is None

    def test_first_class(self):
        tree = cg.parse("x = 1\nclass A: pass\nclass B: pass\n")
        cls = cg.first_class(tree)
        assert cls.name.value == "A"

    def test_first_class_none(self):
        tree = cg.parse("x = 1\n")
        assert cg.first_class(tree) is None


# ── replace_string_property + read_property_string ──────────


class TestReplaceStringProperty:
    def test_replaces_property_return(self):
        src = (
            "class A:\n"
            "    @property\n"
            "    def name(self) -> str:\n"
            '        return "old"\n'
        )
        tree = cg.parse(src)
        cls = cg.find_class(tree, "A")
        new_cls = cg.replace_string_property(cls, "name", "new")
        new_tree = cg.replace_class_in_module(tree, "A", new_cls)
        # The property now returns "new"; old value is gone.
        rt_cls = cg.find_class(cg.parse(new_tree.code), "A")
        assert cg.read_property_string(rt_cls, "name") == "new"

    def test_replaces_attr_assignment(self):
        src = 'class A:\n    name = "old"\n'
        tree = cg.parse(src)
        cls = cg.find_class(tree, "A")
        new_cls = cg.replace_string_property(cls, "name", "new")
        new_tree = cg.replace_class_in_module(tree, "A", new_cls)
        rt_cls = cg.find_class(cg.parse(new_tree.code), "A")
        assert cg.read_property_string(rt_cls, "name") == "new"

    def test_no_match_no_change(self):
        src = 'class A:\n    name = "keep"\n'
        tree = cg.parse(src)
        cls = cg.find_class(tree, "A")
        new_cls = cg.replace_string_property(cls, "ghost", "x")
        # Replacing a non-existent property leaves existing attrs intact.
        assert cg.read_property_string(new_cls, "name") == "keep"
        assert cg.read_property_string(new_cls, "ghost") is None


# ── read_property_string ────────────────────────────────────


class TestReadPropertyString:
    def test_property_return(self):
        src = (
            "class A:\n"
            "    @property\n"
            "    def name(self) -> str:\n"
            '        return "hello"\n'
        )
        cls = cg.find_class(cg.parse(src), "A")
        assert cg.read_property_string(cls, "name") == "hello"

    def test_attr_assignment(self):
        src = 'class A:\n    name = "hello"\n'
        cls = cg.find_class(cg.parse(src), "A")
        assert cg.read_property_string(cls, "name") == "hello"

    def test_missing_returns_none(self):
        src = "class A:\n    pass\n"
        cls = cg.find_class(cg.parse(src), "A")
        assert cg.read_property_string(cls, "ghost") is None

    def test_concatenated_string_returns_none(self):
        src = (
            "class A:\n"
            "    @property\n"
            "    def name(self) -> str:\n"
            '        return "a" "b"\n'
        )
        cls = cg.find_class(cg.parse(src), "A")
        assert cg.read_property_string(cls, "name") is None


# ── read_class_attr_bool ────────────────────────────────────


class TestReadClassAttrBool:
    def test_true(self):
        src = "class A:\n    flag = True\n"
        cls = cg.find_class(cg.parse(src), "A")
        assert cg.read_class_attr_bool(cls, "flag") is True

    def test_false(self):
        src = "class A:\n    flag = False\n"
        cls = cg.find_class(cg.parse(src), "A")
        assert cg.read_class_attr_bool(cls, "flag") is False

    def test_missing_defaults_false(self):
        src = "class A:\n    pass\n"
        cls = cg.find_class(cg.parse(src), "A")
        assert cg.read_class_attr_bool(cls, "ghost") is False

    def test_non_bool_value_defaults_false(self):
        src = "class A:\n    flag = 0\n"
        cls = cg.find_class(cg.parse(src), "A")
        assert cg.read_class_attr_bool(cls, "flag") is False


# ── replace_class_attr_bool ─────────────────────────────────


class TestReplaceClassAttrBool:
    def test_replaces_existing_true_to_false(self):
        """An existing ``attr = True`` assignment is rewritten in place."""
        src = "class A:\n    flag = True\n\n    x = 1\n"
        cls = cg.find_class(cg.parse(src), "A")
        out = cg.replace_class_attr_bool(cls, "flag", False)
        assert cg.read_class_attr_bool(out, "flag") is False
        # The unrelated assignment is untouched.
        assert cg.read_property_string(out, "x") is None  # not a string prop

    def test_replaces_existing_false_to_true(self):
        src = "class A:\n    flag = False\n"
        cls = cg.find_class(cg.parse(src), "A")
        out = cg.replace_class_attr_bool(cls, "flag", True)
        assert cg.read_class_attr_bool(out, "flag") is True

    def test_inserts_when_missing(self):
        """When the class has no such attribute, it is inserted as the
        first statement of the class body."""
        src = "class A:\n    async def m(self):\n        return None\n"
        cls = cg.find_class(cg.parse(src), "A")
        out = cg.replace_class_attr_bool(cls, "flag", True)
        assert cg.read_class_attr_bool(out, "flag") is True
        # Inserted at the head of the class body, before the method.
        first = out.body.body[0]
        assert isinstance(first, cst.SimpleStatementLine)
        assert isinstance(first.body[0], cst.Assign)
        assert first.body[0].targets[0].target.value == "flag"

    def test_inserts_false_when_missing(self):
        src = "class A:\n    pass\n"
        cls = cg.find_class(cg.parse(src), "A")
        out = cg.replace_class_attr_bool(cls, "flag", False)
        assert cg.read_class_attr_bool(out, "flag") is False


# ── replace_method_body + read_method_body ──────────────────


class TestMethodBody:
    def test_replace_with_simple_body(self):
        src = "class A:\n" "    def go(self):\n" "        return 1\n"
        cls = cg.find_class(cg.parse(src), "A")
        new_cls = cg.replace_method_body(cls, "go", "return 42")
        new_tree = cg.replace_class_in_module(cg.parse(src), "A", new_cls)
        assert "return 42" in new_tree.code
        assert "return 1" not in new_tree.code

    def test_replace_with_empty_body_falls_back(self):
        src = "class A:\n" "    def go(self):\n" "        return 1\n"
        cls = cg.find_class(cg.parse(src), "A")
        new_cls = cg.replace_method_body(cls, "go", "")
        new_tree = cg.replace_class_in_module(cg.parse(src), "A", new_cls)
        assert "return None" in new_tree.code

    def test_read_method_body(self):
        src = "class A:\n" "    def go(self):\n" "        return 42\n"
        cls = cg.find_class(cg.parse(src), "A")
        body = cg.read_method_body(cls, "go")
        assert "return 42" in body

    def test_read_missing_returns_none(self):
        src = "class A:\n    pass\n"
        cls = cg.find_class(cg.parse(src), "A")
        assert cg.read_method_body(cls, "ghost") is None


# ── replace_class_in_module ─────────────────────────────────


class TestReplaceClassInModule:
    def test_swaps_named_class(self):
        src = 'class A:\n    x = "old"\nclass B:\n    pass\n'
        tree = cg.parse(src)
        cls = cg.find_class(tree, "A")
        # Build a genuinely different A (x = "swapped").
        new_cls = cg.replace_string_property(cls, "x", "swapped")
        out = cg.replace_class_in_module(tree, "A", new_cls)
        # A was swapped; B untouched.
        rt_a = cg.find_class(out, "A")
        assert cg.read_property_string(rt_a, "x") == "swapped"
        assert cg.find_class(out, "B") is not None

    def test_unknown_class_name_is_noop(self):
        src = 'class A:\n    x = "v"\n'
        tree = cg.parse(src)
        replacement = cg.find_class(tree, "A")
        out = cg.replace_class_in_module(tree, "Ghost", replacement)
        # No class named Ghost → tree body unchanged.
        assert out.code == src


# ── internals: _assign_target_name + _py_string_literal + _dedent_body


class TestInternals:
    def test_py_string_literal_single_line(self):
        out = cg._py_string_literal("hello")
        # The literal must round-trip through cst back to the value.
        assert cst.parse_expression(out).evaluated_value == "hello"

    def test_py_string_literal_multi_line(self):
        out = cg._py_string_literal("a\nb")
        assert out.startswith('"""')
        assert cst.parse_expression(out).evaluated_value == "a\nb"

    def test_py_string_literal_with_triple_quotes(self):
        # Embedded triple quotes get escaped so the literal round-trips.
        out = cg._py_string_literal('x"""y\nz')
        assert cst.parse_expression(out).evaluated_value == 'x"""y\nz'

    def test_dedent_body_strips_leading_blanks(self):
        out = cg._dedent_body("\n\n    return 1")
        assert out == "return 1"

    def test_dedent_empty(self):
        assert cg._dedent_body("\n\n") == ""

    def test_assign_target_name_simple(self):
        stmt = cst.parse_module("x = 1").body[0].body[0]
        assert cg._assign_target_name(stmt) == "x"

    def test_assign_target_name_tuple_returns_none(self):
        stmt = cst.parse_module("x = y = 1").body[0].body[0]
        assert cg._assign_target_name(stmt) is None

    def test_assign_target_name_annotated(self):
        stmt = cst.parse_module("x: int = 1").body[0].body[0]
        assert cg._assign_target_name(stmt) == "x"


# ── RoundTripError ──────────────────────────────────────────


class TestRoundTripError:
    def test_is_valueerror(self):
        assert issubclass(cg.RoundTripError, ValueError)
