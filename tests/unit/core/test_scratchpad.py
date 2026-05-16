"""Unit tests for :mod:`kohakuterrarium.core.scratchpad`."""

from kohakuterrarium.core.scratchpad import Scratchpad, is_reserved_scratchpad_key


class TestIsReservedKey:
    def test_double_underscore_wrapped_is_reserved(self):
        assert is_reserved_scratchpad_key("__internal__") is True

    def test_normal_key_not_reserved(self):
        assert is_reserved_scratchpad_key("answer") is False

    def test_one_sided_underscore_not_reserved(self):
        assert is_reserved_scratchpad_key("__private") is False
        assert is_reserved_scratchpad_key("public__") is False


class TestSetGetDelete:
    def test_set_then_get(self):
        sp = Scratchpad()
        sp.set("answer", "42")
        assert sp.get("answer") == "42"

    def test_get_missing_returns_none(self):
        sp = Scratchpad()
        assert sp.get("nope") is None

    def test_set_overwrites(self):
        sp = Scratchpad()
        sp.set("k", "v1")
        sp.set("k", "v2")
        assert sp.get("k") == "v2"

    def test_delete_existing_returns_true(self):
        sp = Scratchpad()
        sp.set("k", "v")
        assert sp.delete("k") is True
        assert sp.get("k") is None

    def test_delete_missing_returns_false(self):
        sp = Scratchpad()
        assert sp.delete("nope") is False


class TestListKeysAndDict:
    def test_list_keys_hides_reserved_by_default(self):
        sp = Scratchpad()
        sp.set("public", "x")
        sp.set("__private__", "y")
        assert sp.list_keys() == ["public"]

    def test_list_keys_include_reserved(self):
        sp = Scratchpad()
        sp.set("a", "x")
        sp.set("__b__", "y")
        keys = sp.list_keys(include_reserved=True)
        assert set(keys) == {"a", "__b__"}

    def test_to_dict_excludes_reserved(self):
        sp = Scratchpad()
        sp.set("a", "1")
        sp.set("__internal__", "secret")
        assert sp.to_dict() == {"a": "1"}

    def test_to_dict_returns_copy(self):
        sp = Scratchpad()
        sp.set("k", "v")
        d = sp.to_dict()
        d["k"] = "tampered"
        # Mutating the returned dict must NOT change the scratchpad.
        assert sp.get("k") == "v"


class TestClear:
    def test_clear_empties(self):
        sp = Scratchpad()
        sp.set("a", "1")
        sp.set("b", "2")
        sp.clear()
        assert sp.list_keys() == []
        assert sp.get("a") is None


class TestPromptSection:
    def test_empty_pad_returns_empty_string(self):
        assert Scratchpad().to_prompt_section() == ""

    def test_single_line_value_inline(self):
        sp = Scratchpad()
        sp.set("answer", "42")
        out = sp.to_prompt_section()
        assert "## Working Memory" in out
        assert "- **answer**: 42" in out

    def test_multi_line_value_as_section(self):
        sp = Scratchpad()
        sp.set("notes", "line one\nline two")
        out = sp.to_prompt_section()
        assert "### notes" in out
        assert "line one\nline two" in out

    def test_reserved_keys_excluded_from_section(self):
        sp = Scratchpad()
        sp.set("visible", "x")
        sp.set("__hidden__", "secret")
        out = sp.to_prompt_section()
        assert "visible" in out
        assert "__hidden__" not in out
        assert "secret" not in out


class TestDunderProtocol:
    def test_len_counts_all_entries_incl_reserved(self):
        sp = Scratchpad()
        sp.set("a", "1")
        sp.set("__b__", "2")
        assert len(sp) == 2

    def test_contains_checks_raw_key(self):
        sp = Scratchpad()
        sp.set("a", "1")
        assert "a" in sp
        assert "missing" not in sp

    def test_repr_lists_visible_keys(self):
        sp = Scratchpad()
        sp.set("public", "x")
        sp.set("__hidden__", "y")
        r = repr(sp)
        assert "public" in r
        assert "__hidden__" not in r


class TestWriteObserver:
    def test_observer_fires_on_set(self):
        events: list[tuple[str, str, int]] = []
        sp = Scratchpad()
        sp.set_write_observer(lambda k, a, n: events.append((k, a, n)))
        sp.set("k", "hello")
        assert events == [("k", "set", len("hello".encode("utf-8")))]

    def test_observer_fires_on_delete(self):
        events: list[tuple[str, str, int]] = []
        sp = Scratchpad()
        sp.set("k", "x")
        sp.set_write_observer(lambda k, a, n: events.append((k, a, n)))
        sp.delete("k")
        assert events == [("k", "delete", 0)]

    def test_observer_skipped_on_failed_delete(self):
        events: list[tuple[str, str, int]] = []
        sp = Scratchpad()
        sp.set_write_observer(lambda k, a, n: events.append((k, a, n)))
        sp.delete("missing")
        assert events == []

    def test_unset_observer_does_not_crash(self):
        sp = Scratchpad()
        sp.set_write_observer(None)
        sp.set("k", "v")  # must not raise
        sp.delete("k")

    def test_observer_exception_swallowed(self):
        sp = Scratchpad()

        def _boom(*_a):
            raise RuntimeError("observer fail")

        sp.set_write_observer(_boom)
        # Must not raise.
        sp.set("k", "v")
        sp.delete("k")
        # The data still made it in/out.
        assert "k" not in sp

    def test_set_byte_size_uses_utf8_encoding(self):
        events: list[tuple[str, str, int]] = []
        sp = Scratchpad()
        sp.set_write_observer(lambda k, a, n: events.append((k, a, n)))
        sp.set("k", "日本語")
        # UTF-8 encoding of 日本語 is 9 bytes (3 chars × 3 bytes each).
        assert events[0][2] == 9
