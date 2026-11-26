"""The declared SDK requirements match the SDK generation the code is written for."""

import inspect
import tomllib
from importlib import metadata
from pathlib import Path

from anthropic.resources.messages import AsyncMessages
from openai.resources.responses import AsyncResponses
from packaging.requirements import Requirement
from packaging.version import Version

from kohakuterrarium.llm.responses.ws import MIN_OPENAI_FOR_WS

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def declared(name: str) -> Requirement:
    dependencies = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"][
        "dependencies"
    ]
    return next(
        req for req in map(Requirement, dependencies) if req.name.lower() == name
    )


class TestAnthropicPin:
    def test_only_the_1x_line_is_allowed(self):
        spec = declared("anthropic").specifier
        assert Version("1.0.0") in spec
        assert Version("0.125.0") not in spec
        assert Version("2.0.0") not in spec

    def test_installed_sdk_satisfies_the_pin(self):
        version = Version(metadata.version("anthropic"))
        assert version in declared("anthropic").specifier

    def test_sdk_no_longer_takes_sampling_keyword_arguments(self):
        parameters = inspect.signature(AsyncMessages.create).parameters
        assert "extra_body" in parameters
        assert not {"temperature", "top_p", "top_k"} & set(parameters)


class TestOpenAIPin:
    def test_floor_is_the_websocket_connect_requirement(self):
        spec = declared("openai").specifier
        assert Version(MIN_OPENAI_FOR_WS) in spec
        assert Version("2.31.0") not in spec
        assert str(spec) == f">={MIN_OPENAI_FOR_WS}"

    def test_installed_sdk_satisfies_the_pin(self):
        version = Version(metadata.version("openai"))
        assert version in declared("openai").specifier

    def test_sdk_connect_takes_the_keywords_the_providers_pass(self):
        parameters = inspect.signature(AsyncResponses.connect).parameters
        assert {"max_retries", "extra_headers", "websocket_connection_options"} <= set(
            parameters
        )
