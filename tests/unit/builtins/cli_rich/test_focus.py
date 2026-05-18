"""Tests for :mod:`builtins.cli_rich.focus`."""

from kohakuterrarium.builtins.cli_rich.focus import (
    AtNameTarget,
    FocusController,
    parse_at_name,
)


class TestFocusControllerInit:
    def test_empty_init(self):
        c = FocusController()
        assert c.count == 0
        assert c.focus_id == ""
        assert c.index() == -1

    def test_focus_falls_back_to_first_when_missing(self):
        c = FocusController(creature_ids=["a", "b"], focus_id="c")
        # ``c`` not in list — __post_init__ resets focus to first.
        assert c.focus_id == "a"

    def test_focus_respected_when_present(self):
        c = FocusController(creature_ids=["a", "b", "c"], focus_id="b")
        assert c.focus_id == "b"
        assert c.index() == 1


class TestFocusCycle:
    def test_next_wraps(self):
        c = FocusController(creature_ids=["a", "b", "c"], focus_id="c")
        assert c.next() == "a"

    def test_prev_wraps(self):
        c = FocusController(creature_ids=["a", "b", "c"], focus_id="a")
        assert c.prev() == "c"

    def test_next_returns_new_focus(self):
        c = FocusController(creature_ids=["a", "b"])
        assert c.next() == "b"
        assert c.next() == "a"

    def test_cycle_on_empty_is_safe(self):
        c = FocusController()
        assert c.next() == ""
        assert c.prev() == ""

    def test_cycle_on_single_creature_returns_same(self):
        c = FocusController(creature_ids=["a"])
        assert c.next() == "a"
        assert c.prev() == "a"


class TestSet:
    def test_set_known_id_succeeds(self):
        c = FocusController(creature_ids=["a", "b"])
        assert c.set("b") is True
        assert c.focus_id == "b"

    def test_set_unknown_id_fails(self):
        c = FocusController(creature_ids=["a", "b"], focus_id="a")
        assert c.set("nope") is False
        assert c.focus_id == "a"


class TestAddRemove:
    def test_add_new_id_appends(self):
        c = FocusController(creature_ids=["a"])
        c.add("b")
        assert c.creature_ids == ["a", "b"]

    def test_add_duplicate_is_noop(self):
        c = FocusController(creature_ids=["a"])
        c.add("a")
        assert c.creature_ids == ["a"]

    def test_add_seeds_focus_when_empty(self):
        c = FocusController()
        c.add("only")
        assert c.focus_id == "only"

    def test_remove_unknown_id_is_noop(self):
        c = FocusController(creature_ids=["a"], focus_id="a")
        assert c.remove("nope") == "a"

    def test_remove_non_focused_keeps_focus(self):
        c = FocusController(creature_ids=["a", "b", "c"], focus_id="b")
        assert c.remove("c") == "b"
        assert c.creature_ids == ["a", "b"]

    def test_remove_focused_picks_next_sibling(self):
        c = FocusController(creature_ids=["a", "b", "c"], focus_id="b")
        assert c.remove("b") == "c"

    def test_remove_last_focused_picks_previous(self):
        c = FocusController(creature_ids=["a", "b", "c"], focus_id="c")
        # After removing c, the new focus should be b (last remaining
        # at idx min(2, 1) = 1).
        assert c.remove("c") == "b"

    def test_remove_only_creature_clears_focus(self):
        c = FocusController(creature_ids=["a"], focus_id="a")
        assert c.remove("a") == ""
        assert c.focus_id == ""


class TestReplace:
    def test_replace_preserves_focus_when_present(self):
        c = FocusController(creature_ids=["a", "b"], focus_id="b")
        c.replace(["b", "c", "d"])
        assert c.focus_id == "b"

    def test_replace_resets_focus_when_missing(self):
        c = FocusController(creature_ids=["a"], focus_id="a")
        c.replace(["x", "y"])
        assert c.focus_id == "x"

    def test_replace_empty_clears_focus(self):
        c = FocusController(creature_ids=["a"], focus_id="a")
        c.replace([])
        assert c.focus_id == ""


class TestAtNameParser:
    def test_plain_text_no_match(self):
        assert parse_at_name("hello") is None
        assert parse_at_name("") is None

    def test_at_name_with_body_parses(self):
        t = parse_at_name("@physics what's collision returning?")
        assert t is not None
        assert t.name == "physics"
        assert t.payload == "what's collision returning?"
        assert t.is_broadcast is False

    def test_at_all_sets_broadcast(self):
        t = parse_at_name("@all stop")
        assert t is not None
        assert t.name == "all"
        assert t.is_broadcast is True

    def test_at_name_with_dots_dashes_underscores(self):
        t = parse_at_name("@agent-1.scout hello")
        assert t is not None
        assert t.name == "agent-1.scout"

    def test_at_name_with_no_body_falls_through(self):
        # "@name" alone with no message is not a redirect — let it
        # fall through to plain text so the user can type "@me" as
        # part of a sentence-in-progress (no risk of accidental send).
        assert parse_at_name("@physics") is None
        assert parse_at_name("@physics  ") is None

    def test_leading_whitespace_tolerated(self):
        t = parse_at_name("   @bob hi there")
        assert t is not None
        assert t.name == "bob"
        assert t.payload == "hi there"

    def test_at_name_multiline_body(self):
        t = parse_at_name("@bob line1\nline2\nline3")
        assert t is not None
        assert t.payload == "line1\nline2\nline3"

    def test_at_name_target_dataclass_shape(self):
        t = AtNameTarget(name="x", payload="y")
        assert t.is_broadcast is False
