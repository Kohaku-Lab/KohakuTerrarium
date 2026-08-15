"""Unit tests for ``llm/responses_reasoning.py``."""

from kohakuterrarium.llm.responses_reasoning import ResponsesReasoningCollector


class Ev:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class TestResponsesReasoningCollector:
    def test_delta_events_are_accumulated(self):
        collector = ResponsesReasoningCollector()
        collector.consume(Ev(type="response.reasoning_text.delta", delta="think "))
        collector.consume(Ev(type="response.reasoning_text.delta", delta="hard"))
        collector.consume(
            Ev(type="response.reasoning_summary_text.delta", delta="summary")
        )
        assert collector.fields() == {
            "reasoning_content": "think hard",
            "reasoning_summary": "summary",
        }

    def test_done_event_replaces_accumulator(self):
        collector = ResponsesReasoningCollector()
        collector.consume(Ev(type="response.reasoning_text.delta", delta="partial"))
        collector.consume(Ev(type="response.reasoning_text.done", text="complete"))
        assert collector.fields()["reasoning_content"] == "complete"

    def test_reasoning_item_summary_and_content_are_captured(self):
        collector = ResponsesReasoningCollector()
        item = Ev(
            type="reasoning",
            summary=[{"type": "summary_text", "text": "brief"}],
            content=[{"type": "text", "text": "private"}],
        )
        collector.consume(Ev(type="response.output_item.done", item=item))
        assert collector.fields() == {
            "reasoning_content": "private",
            "reasoning_summary": "brief",
        }

    def test_empty_collector_returns_empty_fields(self):
        assert ResponsesReasoningCollector().fields() == {}
