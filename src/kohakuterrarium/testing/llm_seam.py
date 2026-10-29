"""In-process LLM seam for tests: every provider factory returns a test double.

``LLM_FACTORY_TARGETS`` lists each ``(module, attribute)`` through which the
framework builds a provider; :func:`install_llm_seam` replaces all of them,
and :mod:`kohakuterrarium.testing.subprocess_seam` patches the same list.
:func:`forbid_real_providers` makes ``BaseLLMProvider`` construction raise so
a factory missing from the list fails loudly instead of building a real
provider.
"""

import importlib
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

from kohakuterrarium.llm.base import BaseLLMProvider
from kohakuterrarium.testing.llm import ScriptedLLM

LLM_FACTORY_TARGETS: tuple[tuple[str, str], ...] = (
    ("kohakuterrarium.bootstrap.llm", "create_llm_provider"),
    ("kohakuterrarium.bootstrap.llm", "create_llm_from_profile_name"),
    ("kohakuterrarium.bootstrap.llm", "_create_from_profile"),
    ("kohakuterrarium.bootstrap.agent_init", "create_llm_provider"),
    ("kohakuterrarium.core.agent_model", "create_llm_from_profile_name"),
    ("kohakuterrarium.core.agent_compact", "create_llm_from_profile_name"),
    (
        "kohakuterrarium.studio.sessions.summary.tracking",
        "create_llm_from_profile_name",
    ),
    ("kohakuterrarium.modules.subagent.model_resolve", "_create_from_profile"),
    ("kohakuterrarium.validate", "_create_from_profile"),
)


class RealProviderBlocked(RuntimeError):
    """A real LLM provider was constructed while the test seam forbids it."""


class LLMSeam:
    """Factory replacement installed by :func:`install_llm_seam`."""

    def __init__(self, factory: Callable[[], Any]) -> None:
        self.factory = factory
        self.built: list[Any] = []

    def build(self, *args: Any, **kwargs: Any) -> Any:
        """Return a new double from ``factory`` for any factory call shape."""
        llm = self.factory()
        self.built.append(llm)
        return llm


def _default_factory() -> ScriptedLLM:
    return ScriptedLLM(["OK"])


_ORIGINALS: dict[tuple[str, str], Any] = {}
_REAL_INIT = BaseLLMProvider.__init__


def _targets() -> list[tuple[Any, str, tuple[str, str]]]:
    found = []
    for key in LLM_FACTORY_TARGETS:
        module = importlib.import_module(key[0])
        _ORIGINALS.setdefault(key, getattr(module, key[1]))
        found.append((module, key[1], key))
    return found


def patch_factories(setter: Callable[[Any, str, Any], None], build: Any) -> None:
    """Point every entry of ``LLM_FACTORY_TARGETS`` at ``build`` through ``setter``."""
    for module, attr, _ in _targets():
        setter(module, attr, build)


@contextmanager
def real_llm_providers() -> Iterator[None]:
    """Run a block with the real provider factories and provider construction.

    For the part of a seamed test that builds a provider against a fake
    transport; everything outside the block stays seamed.
    """
    saved = [(module, attr, getattr(module, attr)) for module, attr, _ in _targets()]
    saved_init = BaseLLMProvider.__init__
    for module, attr, key in _targets():
        setattr(module, attr, _ORIGINALS[key])
    BaseLLMProvider.__init__ = _REAL_INIT
    try:
        yield
    finally:
        for module, attr, value in saved:
            setattr(module, attr, value)
        BaseLLMProvider.__init__ = saved_init


def install_llm_seam(
    monkeypatch: Any, factory: Callable[[], Any] | None = None
) -> LLMSeam:
    """Replace every provider factory for the current test; return the seam."""
    seam = LLMSeam(factory or _default_factory)
    patch_factories(monkeypatch.setattr, seam.build)
    return seam


def forbid_real_providers(setter: Callable[[Any, str, Any], None]) -> None:
    """Make constructing any ``BaseLLMProvider`` raise :class:`RealProviderBlocked`."""

    def _blocked(self: Any, *args: Any, **kwargs: Any) -> None:
        raise RealProviderBlocked(
            f"{type(self).__name__} was constructed under the test LLM seam; "
            "build providers through the seam, or mark the test "
            "@pytest.mark.real_llm_provider when it targets a fake transport"
        )

    setter(BaseLLMProvider, "__init__", _blocked)


__all__ = [
    "LLMSeam",
    "LLM_FACTORY_TARGETS",
    "RealProviderBlocked",
    "forbid_real_providers",
    "install_llm_seam",
    "patch_factories",
    "real_llm_providers",
]
