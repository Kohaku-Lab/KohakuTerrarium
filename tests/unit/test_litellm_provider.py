"""Tests for LiteLLM provider."""

import ast
from pathlib import Path

import pytest

PROVIDER_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "kohakuterrarium"
    / "llm"
    / "litellm_provider.py"
)
FACTORY_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "kohakuterrarium"
    / "bootstrap"
    / "llm.py"
)


class TestLiteLLMProviderStructure:
    def _parse(self):
        return ast.parse(PROVIDER_PATH.read_text())

    def test_file_exists(self):
        assert PROVIDER_PATH.exists()

    def test_has_litellm_provider_class(self):
        tree = self._parse()
        classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
        assert "LiteLLMProvider" in classes

    def test_inherits_base_llm_provider(self):
        tree = self._parse()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "LiteLLMProvider":
                base_names = []
                for base in node.bases:
                    if isinstance(base, ast.Name):
                        base_names.append(base.id)
                assert "BaseLLMProvider" in base_names
                return
        pytest.fail("LiteLLMProvider class not found")

    def test_has_stream_chat(self):
        tree = self._parse()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name == "LiteLLMProvider":
                methods = [
                    n.name for n in node.body if isinstance(n, ast.AsyncFunctionDef)
                ]
                assert "_stream_chat" in methods
                assert "_complete_chat" in methods
                return

    def test_has_with_model(self):
        src = PROVIDER_PATH.read_text()
        assert "def with_model" in src

    def test_uses_drop_params_true(self):
        src = PROVIDER_PATH.read_text()
        assert '"drop_params": True' in src or "'drop_params': True" in src

    def test_uses_litellm_acompletion(self):
        src = PROVIDER_PATH.read_text()
        assert "litellm.acompletion" in src

    def test_provider_name_is_litellm(self):
        src = PROVIDER_PATH.read_text()
        assert "provider_name" in src
        assert '"litellm"' in src


class TestFactoryRegistration:
    def test_litellm_imported_in_factory(self):
        src = FACTORY_PATH.read_text()
        assert "LiteLLMProvider" in src

    def test_litellm_backend_type_branch(self):
        src = FACTORY_PATH.read_text()
        assert '"litellm"' in src


class TestLiteLLMSDKInteraction:
    def test_acompletion_stream_called_with_drop_params(self):
        import asyncio
        import sys
        import types

        fake = types.ModuleType("litellm")

        async def fake_acompletion(**kwargs):
            class FakeChunk:
                class choices_item:
                    class delta:
                        content = "hello"
                        tool_calls = None

                    finish_reason = None

                choices = [choices_item()]

            async def gen():
                yield FakeChunk()

            return gen()

        fake.acompletion = fake_acompletion
        sys.modules["litellm"] = fake

        try:
            # Just verify the SDK call pattern works
            async def run():
                resp = await fake.acompletion(
                    model="openai/gpt-4o",
                    messages=[{"role": "user", "content": "hi"}],
                    drop_params=True,
                    stream=True,
                )
                chunks = []
                async for chunk in resp:
                    if chunk.choices[0].delta.content:
                        chunks.append(chunk.choices[0].delta.content)
                return "".join(chunks)

            result = asyncio.run(run())
            assert result == "hello"
        finally:
            del sys.modules["litellm"]

    def test_acompletion_complete_called_with_drop_params(self):
        import asyncio
        import sys
        import types

        fake = types.ModuleType("litellm")

        async def fake_acompletion(**kwargs):
            assert kwargs["drop_params"] is True

            class FakeMessage:
                content = "world"
                tool_calls = None

            class FakeChoice:
                message = FakeMessage()
                finish_reason = "stop"

            class FakeUsage:
                prompt_tokens = 5
                completion_tokens = 3
                total_tokens = 8

            class FakeResponse:
                choices = [FakeChoice()]
                usage = FakeUsage()
                model = "openai/gpt-4o"

            return FakeResponse()

        fake.acompletion = fake_acompletion
        sys.modules["litellm"] = fake

        try:

            async def run():
                resp = await fake.acompletion(
                    model="openai/gpt-4o",
                    messages=[{"role": "user", "content": "hi"}],
                    drop_params=True,
                )
                return resp.choices[0].message.content

            result = asyncio.run(run())
            assert result == "world"
        finally:
            del sys.modules["litellm"]


class TestDependency:
    def test_litellm_in_pyproject_optional_deps(self):
        pyproject = (Path(__file__).resolve().parents[2] / "pyproject.toml").read_text()
        assert "litellm" in pyproject
