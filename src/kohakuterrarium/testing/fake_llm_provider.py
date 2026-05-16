"""Fake LLM provider routed through the real profile resolution path.

Unlike :class:`kohakuterrarium.testing.llm.ScriptedLLM`, which has to be
installed via monkeypatching the module-level factory (and therefore
*bypasses* the entire profile + api-key resolution chain), this fake is
built via the *real* :func:`bootstrap.llm._create_from_profile`.  That
means a test using it actually exercises:

- ``resolve_controller_llm`` against the host's profile store;
- ``get_api_key(profile.provider)`` against the host's identity store
  (and, in worker mode, the ``IdentityCache`` + ``studio.identity`` RPC);
- ``_apply_backend_native_identity``.

If anything in that chain is broken the test fails — that is the
property the unit/integration ``ScriptedLLM`` seam loses by design.

To activate the fake, declare a profile with::

    backend_type: fake_test
    provider: openai          # any registered provider — its api key is fetched
    model: fake-echo          # any string; for logs only
    extra_body:
      script_path: /abs/path/to/script.json

The JSON file at ``script_path`` has shape ``{"script": ["reply", ...]}``
and is re-read on every chat turn so tests can rotate the reply between
turns without rebuilding the provider.  When ``script_path`` is absent
or unreadable the fake replies with ``"OK"``.
"""

import json
from pathlib import Path
from typing import Any, AsyncIterator

from kohakuterrarium.llm.base import ChatResponse, LLMProvider
from kohakuterrarium.llm.message import Message


class FakeLLMProvider(LLMProvider):
    """Deterministic LLM provider built via the real profile path.

    Carries the resolved api key on the instance solely so tests can
    assert the credential resolution actually happened (``self.api_key``
    is non-empty after construction).  Nothing else uses it — no HTTP
    is ever performed.
    """

    provider_name = "fake_test"
    provider_native_tools: frozenset[str] = frozenset()

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "fake-echo",
        script_path: str | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.script_path = Path(script_path) if script_path else None
        self.call_count = 0

    def _load_script(self) -> list[str]:
        if self.script_path is None:
            return ["OK"]
        try:
            data = json.loads(self.script_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ["OK"]
        script = data.get("script") if isinstance(data, dict) else None
        if not isinstance(script, list):
            return ["OK"]
        return [str(s) for s in script]

    def _pick(self) -> str:
        script = self._load_script()
        if not script:
            return "OK"
        idx = min(self.call_count, len(script) - 1)
        self.call_count += 1
        return script[idx]

    async def chat(
        self,
        messages: list[Message] | list[dict[str, Any]],
        *,
        stream: bool = True,
        **kwargs: Any,
    ) -> AsyncIterator[str]:
        text = self._pick()
        # Stream in two chunks so the streaming code path is exercised.
        mid = max(1, len(text) // 2)
        yield text[:mid]
        if text[mid:]:
            yield text[mid:]

    async def chat_complete(
        self,
        messages: list[Message] | list[dict[str, Any]],
        **kwargs: Any,
    ) -> ChatResponse:
        text = self._pick()
        return ChatResponse(
            content=text,
            finish_reason="stop",
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            model=self.model,
        )

    async def close(self) -> None:
        return None


__all__ = ["FakeLLMProvider"]
