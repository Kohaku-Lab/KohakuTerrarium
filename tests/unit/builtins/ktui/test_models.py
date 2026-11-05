"""The ``/model`` picker data: presets to picker items, picker answers to selectors."""

from kohakuterrarium.builtins.ktui.models import model_items, selector_of

PRESETS = [
    {
        "name": "gpt-6-astra",
        "provider": "codex",
        "model": "gpt-6-astra",
        "available": True,
        "variation_groups": {
            "speed": {"normal": {}, "fast": {}},
            "reasoning": {"low": {}, "medium": {}, "high": {}},
        },
        "selected_variations": {"reasoning": "low"},
    },
    {
        "name": "claude-opus",
        "provider": "anthropic",
        "model": "claude-opus-4-8",
        "available": False,
        "variation_groups": {},
        "selected_variations": {},
    },
    {
        "name": "gpt-5.4",
        "provider": "codex",
        "model": "gpt-5.4",
        "available": True,
        "variation_groups": {},
        "selected_variations": {},
    },
]


class TestModelItems:
    def test_groups_by_provider_and_marks_the_current_model_with_its_variations(self):
        items = model_items(PRESETS, "codex/gpt-6-astra@reasoning=high,speed=fast")

        assert [i["value"] for i in items] == [
            "codex/gpt-6-astra",
            "codex/gpt-5.4",
            "anthropic/claude-opus",
        ]
        astra = items[0]
        assert astra["current"] is True and astra["group"] == "codex"
        assert astra["chosen"] == {"reasoning": "high", "speed": "fast"}
        # reasoning has a preset default (low); speed has none, so it offers "default"
        assert astra["choices"] == [
            ["reasoning", ["low", "medium", "high"]],
            ["speed", ["default", "normal", "fast"]],
        ]
        assert astra["detail"] is None
        assert items[2]["detail"] == "claude-opus-4-8"
        assert items[2]["available"] is False
        assert not any(i["current"] for i in items[1:])

    def test_an_unparsable_current_id_marks_nothing(self):
        items = model_items(PRESETS, "@broken")
        assert not any(i["current"] for i in items)
        assert items[0]["chosen"] == {"reasoning": "low", "speed": "default"}


class TestSelectorOf:
    def test_choices_become_a_sorted_variation_selector(self):
        answer = {
            "value": "codex/gpt-6-astra",
            "choices": {"speed": "fast", "reasoning": "high"},
        }
        assert selector_of(answer) == "codex/gpt-6-astra@reasoning=high,speed=fast"

    def test_default_leaves_the_group_to_the_provider(self):
        answer = {
            "value": "codex/gpt-6-astra",
            "choices": {"speed": "default", "reasoning": "high"},
        }
        assert selector_of(answer) == "codex/gpt-6-astra@reasoning=high"
        only_default = {"value": "codex/gpt-6-astra", "choices": {"speed": "default"}}
        assert selector_of(only_default) == "codex/gpt-6-astra"

    def test_plain_answers_pass_through(self):
        assert selector_of("codex/gpt-5.4") == "codex/gpt-5.4"
        assert selector_of({"value": "codex/gpt-5.4", "choices": {}}) == "codex/gpt-5.4"
        assert selector_of(None) == ""
