"""The declared SDK requirements match the SDK generation the code is written for."""

import inspect
import tomllib
from importlib import metadata
from pathlib import Path

from anthropic.resources.messages import AsyncMessages
from packaging.requirements import Requirement
from packaging.version import Version

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
