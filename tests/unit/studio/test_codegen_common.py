"""Unit tests for the shared libcst helpers."""

from kohakuterrarium.studio.editors.codegen_common import (
    find_class,
    first_class,
    parse,
    read_class_attr_bool,
    read_method_body,
    read_property_string,
    replace_class_in_module,
    replace_method_body,
    replace_string_property,
)

SIMPLE_SRC = """\
class Foo:
    needs_context = True

    @property
    def name(self) -> str:
        return "original"

    def run(self):
        return 42
"""


def test_find_class():
    tree = parse(SIMPLE_SRC)
    assert find_class(tree, "Foo") is not None
    assert find_class(tree, "Bar") is None


def test_first_class():
    tree = parse(SIMPLE_SRC)
    k = first_class(tree)
    assert k is not None
    assert k.name.value == "Foo"


def test_read_property_string():
    tree = parse(SIMPLE_SRC)
    klass = find_class(tree, "Foo")
    assert read_property_string(klass, "name") == "original"
    assert read_property_string(klass, "does_not_exist") is None


def test_read_class_attr_bool():
    tree = parse(SIMPLE_SRC)
    klass = find_class(tree, "Foo")
    assert read_class_attr_bool(klass, "needs_context") is True
    assert read_class_attr_bool(klass, "nope") is False


def test_read_method_body():
    tree = parse(SIMPLE_SRC)
    klass = find_class(tree, "Foo")
    body = read_method_body(klass, "run")
    assert body is not None
    assert "return 42" in body
    assert read_method_body(klass, "absent") is None


def test_replace_string_property_is_targeted():
    tree = parse(SIMPLE_SRC)
    klass = find_class(tree, "Foo")
    new_klass = replace_string_property(klass, "name", "patched")
    out = replace_class_in_module(tree, "Foo", new_klass).code
    assert '"patched"' in out or "'patched'" in out
    # The other string body ("run" method) is untouched
    assert "return 42" in out


def test_replace_method_body_preserves_signature():
    tree = parse(SIMPLE_SRC)
    klass = find_class(tree, "Foo")
    new_klass = replace_method_body(klass, "run", "return 99")
    out = replace_class_in_module(tree, "Foo", new_klass).code
    assert "def run(self)" in out
    assert "return 99" in out
    # Property name() is untouched
    assert 'return "original"' in out


def test_replace_method_body_handles_multiline():
    tree = parse(SIMPLE_SRC)
    klass = find_class(tree, "Foo")
    body = "x = 1\nif x:\n    return x\nreturn 0"
    new_klass = replace_method_body(klass, "run", body)
    out = replace_class_in_module(tree, "Foo", new_klass).code
    # Compiles cleanly
    compile(out, "<test>", "exec")
    assert "x = 1" in out
    assert "return x" in out


def test_replace_method_body_with_indented_input():
    """Callers may send already-indented bodies; we strip it."""
    tree = parse(SIMPLE_SRC)
    klass = find_class(tree, "Foo")
    body = "        return 123"  # 8-space indent
    new_klass = replace_method_body(klass, "run", body)
    out = replace_class_in_module(tree, "Foo", new_klass).code
    compile(out, "<test>", "exec")
    assert "return 123" in out
