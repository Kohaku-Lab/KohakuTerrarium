"""Unit contract for standalone Codex-subscription web search."""

import json
from types import SimpleNamespace

import pytest

from kohakuterrarium.llm import codex_web_search as search_mod
from kohakuterrarium.llm.codex_auth import CodexTokens
from kohakuterrarium.llm.codex_web_search import (
    CodexSearchOperationalError,
    CodexSearchUnavailable,
    CodexSubscriptionSearchBackend,
    _SearchCollector,
    _map_request_error,
)


def _event(event_type: str, **values):
    return SimpleNamespace(type=event_type, **values)


class TestSearchCollector:
    def test_collects_text_actions_sources_and_usage(self):
        collector = _SearchCollector(query="KT latest", model="gpt-5.6-luna")
        collector.observe(_event("response.output_text.delta", delta="Current answer"))
        collector.observe(
            _event(
                "response.output_item.done",
                item=SimpleNamespace(
                    type="web_search_call",
                    action=SimpleNamespace(
                        type="search",
                        query="KT latest",
                        sources=[
                            SimpleNamespace(
                                type="api",
                                title="Primary source",
                                url="https://example.com/source",
                            )
                        ],
                    ),
                ),
            )
        )
        collector.observe(
            _event(
                "response.completed",
                response=SimpleNamespace(
                    status="completed",
                    usage=SimpleNamespace(
                        input_tokens=11,
                        output_tokens=7,
                        total_tokens=18,
                        input_tokens_details=SimpleNamespace(cached_tokens=3),
                    ),
                ),
            )
        )

        result = collector.result(5)

        assert "Current answer" in result.output
        assert result.sources == [
            {"title": "Primary source", "url": "https://example.com/source"}
        ]
        assert result.metadata["actions"] == [{"type": "search", "query": "KT latest"}]
        assert result.metadata["citation_status"] == "grounded"
        assert result.metadata["action_source_count"] == 1
        assert result.metadata["verified_source_count"] == 0
        assert result.metadata["consulted_sources"] == result.sources
        assert result.metadata["usage"]["cached_tokens"] == 3
        json.dumps(result.metadata)

    def test_message_annotations_are_normalized_and_deduplicated(self):
        annotation = {
            "type": "url_citation",
            "url": "https://example.org/doc",
            "title": "Doc",
        }
        collector = _SearchCollector(query="q", model="m")
        collector.observe(
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "message",
                    "content": [{"annotations": [annotation, annotation]}],
                },
            }
        )

        result = collector.result(10)

        assert result.sources == [{"title": "Doc", "url": "https://example.org/doc"}]
        assert result.metadata["annotation_count"] == 2
        assert result.metadata["citation_status"] == "verified"
        assert result.metadata["verified_sources"] == result.sources

    def test_inline_citations_take_priority_over_consulted_source_limit(self):
        collector = _SearchCollector(query="q", model="m")
        collector.observe(
            _event(
                "response.output_item.done",
                item=SimpleNamespace(
                    type="web_search_call",
                    action=SimpleNamespace(
                        type="search",
                        sources=[
                            SimpleNamespace(
                                type="url", url="https://example.com/consulted"
                            )
                        ],
                    ),
                ),
            )
        )
        collector.observe(
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "message",
                    "content": [
                        {
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "url": "https://example.com/cited",
                                    "title": "Cited",
                                }
                            ]
                        }
                    ],
                },
            }
        )

        result = collector.result(1)

        assert result.sources == [
            {"title": "Cited", "url": "https://example.com/cited"}
        ]
        assert result.metadata["source_count"] == 1
        assert result.metadata["citation_status"] == "verified"

    def test_same_url_is_one_source_but_keeps_both_grounding_roles(self):
        collector = _SearchCollector(query="q", model="m")
        collector.observe(
            _event(
                "response.output_item.done",
                item=SimpleNamespace(
                    type="web_search_call",
                    action=SimpleNamespace(
                        type="search",
                        sources=[
                            SimpleNamespace(
                                type="url", url="https://example.com/source"
                            )
                        ],
                    ),
                ),
            )
        )
        collector.observe(
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "message",
                    "content": [
                        {
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "url": "https://example.com/source",
                                    "title": "Specific article",
                                }
                            ]
                        }
                    ],
                },
            }
        )

        result = collector.result(5)

        assert result.sources == [
            {"title": "Specific article", "url": "https://example.com/source"}
        ]
        assert result.metadata["source_count"] == 1
        assert result.metadata["verified_source_count"] == 1
        assert result.metadata["action_source_count"] == 1

    def test_non_url_annotation_is_not_promoted_to_verified_source(self):
        collector = _SearchCollector(query="q", model="m")
        collector.observe(
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "message",
                    "content": [
                        {
                            "annotations": [
                                {
                                    "type": "file_reference",
                                    "url": "https://example.com/not-a-citation",
                                    "title": "Not a citation",
                                }
                            ]
                        }
                    ],
                },
            }
        )

        result = collector.result(5)

        assert result.sources == []
        assert result.metadata["annotation_count"] == 1
        assert result.metadata["citation_status"] == "none"

    def test_weather_action_source_is_grounded_without_inline_citation(self):
        collector = _SearchCollector(query="Shanghai weather", model="m")
        collector.observe(
            _event(
                "response.output_text.delta",
                delta="Seven-day forecast from the live weather feed.",
            )
        )
        collector.observe(
            _event(
                "response.output_item.done",
                item=SimpleNamespace(
                    type="web_search_call",
                    action=SimpleNamespace(
                        type="search",
                        query="weather: China, Shanghai, Shanghai",
                        sources=[
                            SimpleNamespace(
                                type="url", url="https://weather.example/forecast"
                            )
                        ],
                    ),
                ),
            )
        )

        result = collector.result(5)

        assert result.metadata["citation_status"] == "grounded"
        assert result.metadata["annotation_count"] == 0
        assert result.metadata["action_source_count"] == 1
        assert result.metadata["verified_source_count"] == 0
        assert result.metadata["consulted_sources"] == [
            {
                "title": "weather.example",
                "url": "https://weather.example/forecast",
            }
        ]
        assert "structured URLs reported as consulted" in result.output

    def test_completed_response_is_a_fallback_source_for_included_actions(self):
        collector = _SearchCollector(query="q", model="m")
        collector.observe(
            _event(
                "response.completed",
                response=SimpleNamespace(
                    status="completed",
                    usage=None,
                    output=[
                        SimpleNamespace(
                            type="web_search_call",
                            action=SimpleNamespace(
                                type="search",
                                sources=[
                                    SimpleNamespace(
                                        type="url", url="https://example.com/source"
                                    )
                                ],
                            ),
                        )
                    ],
                ),
            )
        )

        result = collector.result(5)

        assert result.metadata["citation_status"] == "grounded"
        assert result.metadata["source_count"] == 1
        assert result.metadata["consulted_sources"] == [
            {"title": "example.com", "url": "https://example.com/source"}
        ]

    def test_failed_response_without_partial_output_is_error(self):
        collector = _SearchCollector(query="q", model="m")

        with pytest.raises(CodexSearchOperationalError, match="backend unavailable"):
            collector.observe(
                _event(
                    "response.failed",
                    response=SimpleNamespace(
                        status="failed",
                        error=SimpleNamespace(message="backend unavailable"),
                    ),
                )
            )

    def test_failed_response_with_partial_output_is_still_error(self):
        collector = _SearchCollector(query="q", model="m")
        collector.observe(_event("response.output_text.delta", delta="partial"))

        with pytest.raises(CodexSearchOperationalError, match="backend unavailable"):
            collector.observe(
                _event(
                    "response.failed",
                    response=SimpleNamespace(
                        status="failed",
                        error=SimpleNamespace(message="backend unavailable"),
                    ),
                )
            )

    def test_incomplete_response_with_partial_output_is_explicit(self):
        collector = _SearchCollector(query="q", model="m")
        collector.observe(_event("response.output_text.delta", delta="partial"))
        collector.observe(
            _event(
                "response.incomplete",
                response=SimpleNamespace(
                    status="incomplete",
                    error=None,
                    output=[],
                ),
            )
        )

        result = collector.result(5)

        assert result.metadata["response_status"] == "incomplete"
        assert "Incomplete Codex search response" in result.output
        assert "partial" in result.output


class TestCodexSubscriptionSearchBackend:
    @pytest.mark.asyncio
    async def test_sends_forced_live_search_with_cached_subscription(self, monkeypatch):
        captured = {}

        async def valid_tokens():
            return CodexTokens(access_token="token", refresh_token="refresh")

        async def events():
            yield _event("response.output_text.delta", delta="Answer")
            yield _event(
                "response.output_item.done",
                item=SimpleNamespace(
                    type="web_search_call",
                    action=SimpleNamespace(
                        type="search",
                        query="query",
                        sources=[
                            SimpleNamespace(
                                title="Source", url="https://example.com", type="api"
                            )
                        ],
                    ),
                ),
            )
            yield _event(
                "response.completed",
                response=SimpleNamespace(status="completed", usage=None),
            )

        class _Responses:
            async def create(self, **kwargs):
                captured.update(kwargs)
                return events()

        class _Client:
            responses = _Responses()

            async def close(self):
                captured["closed"] = True

        monkeypatch.setattr(search_mod, "_load_valid_tokens", valid_tokens)
        monkeypatch.setattr(search_mod, "AsyncOpenAI", lambda **kwargs: _Client())

        result = await CodexSubscriptionSearchBackend().search("query", 3, "CN")

        assert captured["tools"] == [
            {"type": "web_search", "external_web_access": True}
        ]
        assert captured["tool_choice"] == {"type": "web_search"}
        assert captured["include"] == ["web_search_call.action.sources"]
        assert captured["model"] == "gpt-5.6-luna"
        assert captured["closed"] is True
        assert result.metadata["backend"] == "codex"
        assert result.sources[0]["url"] == "https://example.com"

    @pytest.mark.asyncio
    async def test_missing_cached_login_does_not_open_interactive_oauth(
        self, monkeypatch
    ):
        monkeypatch.setattr(search_mod, "HAS_OPENAI", True)
        monkeypatch.setattr(search_mod.CodexTokens, "load", lambda: None)

        with pytest.raises(CodexSearchUnavailable, match="kt login codex"):
            await search_mod._load_valid_tokens()


class TestRequestErrorMapping:
    def test_auth_error_is_unavailable(self):
        error = SimpleNamespace(status_code=401, response=None)
        assert isinstance(_map_request_error(error), CodexSearchUnavailable)

    def test_rate_limit_is_operational(self):
        error = SimpleNamespace(status_code=429, response=None)
        assert isinstance(_map_request_error(error), CodexSearchOperationalError)
