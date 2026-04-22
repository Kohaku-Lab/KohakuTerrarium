"""Agent-start handling of provider-native tools.

Semantic:

* Provider-native tools are **opt-out**. Each provider declares the
  builtin names it can serve via ``provider_native_tools``; those
  entries auto-register into every creature that runs on the
  provider, without the creature listing them.
* Creatures drop a tool with the ``disable_provider_tools`` list.
* If a creature does wire a provider-native tool explicitly and the
  active provider can't handle it, the entry is silently unregistered
  (INFO log) so it doesn't sit in the registry unreachable.
"""

from kohakuterrarium.bootstrap.agent_init import AgentInitMixin
from kohakuterrarium.builtins.tools import ImageGenTool
from kohakuterrarium.builtins.tools.bash import BashTool
from kohakuterrarium.core.config_types import AgentConfig
from kohakuterrarium.core.registry import Registry


class _StubProvider:
    """Minimal provider surface — the mixin only reads two attrs."""

    def __init__(self, name: str, offered: frozenset[str] | None = None):
        self.provider_name = name
        self.provider_native_tools = offered or frozenset()


class _Harness(AgentInitMixin):
    """Minimal harness — no config loading, no LLM init."""

    def __init__(
        self,
        llm: _StubProvider,
        pre_registered: list | None = None,
        disable: list[str] | None = None,
    ):
        self.llm = llm
        self.registry = Registry()
        for tool in pre_registered or []:
            self.registry.register_tool(tool)
        self.config = AgentConfig(name="t", disable_provider_tools=disable or [])


# ─── Auto-injection (the opt-out behaviour) ──────────────────────────


def test_auto_injects_provider_natives_into_empty_registry():
    h = _Harness(_StubProvider("codex", frozenset({"image_gen"})))
    h._drop_unsupported_provider_native_tools()
    h._auto_inject_provider_native_tools()
    assert "image_gen" in h.registry.list_tools()


def test_auto_injection_skips_when_provider_offers_nothing():
    h = _Harness(_StubProvider("openai"))
    h._drop_unsupported_provider_native_tools()
    h._auto_inject_provider_native_tools()
    assert h.registry.list_tools() == []


def test_auto_injection_respects_disable_provider_tools():
    h = _Harness(
        _StubProvider("codex", frozenset({"image_gen"})),
        disable=["image_gen"],
    )
    h._drop_unsupported_provider_native_tools()
    h._auto_inject_provider_native_tools()
    assert "image_gen" not in h.registry.list_tools()


def test_auto_injection_preserves_user_wired_instance():
    """If the user already wired image_gen in the creature config, keep
    their instance (which may carry custom knobs) rather than
    replacing it with a fresh default."""
    user_tool = ImageGenTool()
    h = _Harness(
        _StubProvider("codex", frozenset({"image_gen"})),
        pre_registered=[user_tool],
    )
    h._drop_unsupported_provider_native_tools()
    h._auto_inject_provider_native_tools()
    assert h.registry.get_tool("image_gen") is user_tool


# ─── Drop path (user wired but provider can't serve) ────────────────


def test_explicit_wiring_on_unsupported_provider_is_dropped():
    h = _Harness(
        _StubProvider("openai"),  # provider_native_tools = ()
        pre_registered=[ImageGenTool()],
    )
    h._drop_unsupported_provider_native_tools()
    h._auto_inject_provider_native_tools()
    assert "image_gen" not in h.registry.list_tools()


def test_explicit_wiring_on_supported_provider_is_kept():
    h = _Harness(
        _StubProvider("codex", frozenset({"image_gen"})),
        pre_registered=[ImageGenTool()],
    )
    h._drop_unsupported_provider_native_tools()
    h._auto_inject_provider_native_tools()
    assert "image_gen" in h.registry.list_tools()


# ─── Non-provider-native tools are left alone ────────────────────────


def test_regular_tools_untouched_by_either_pass():
    h = _Harness(
        _StubProvider("codex", frozenset({"image_gen"})),
        pre_registered=[BashTool()],
    )
    h._drop_unsupported_provider_native_tools()
    h._auto_inject_provider_native_tools()
    tools = set(h.registry.list_tools())
    assert "bash" in tools
    assert "image_gen" in tools  # auto-injected


def test_no_llm_means_no_injection():
    """A bare harness without an LLM stub doesn't crash."""

    class _Harness2(AgentInitMixin):
        def __init__(self):
            self.registry = Registry()
            self.config = AgentConfig(name="t")
            self.llm = None  # no provider yet

    h = _Harness2()
    h._drop_unsupported_provider_native_tools()
    h._auto_inject_provider_native_tools()
    assert h.registry.list_tools() == []
