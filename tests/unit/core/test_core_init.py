"""Coverage for the ``__getattr__`` lazy-export in :mod:`kohakuterrarium.core`."""

import pytest


@pytest.fixture(autouse=True)
def _reset_lazy_attrs():
    """Drop cached Agent/run_agent on each test so the lazy branch fires."""
    import kohakuterrarium.core as core

    snap = (
        core.__dict__.pop("Agent", None),
        core.__dict__.pop("run_agent", None),
    )
    yield
    # Restore so other tests find them.
    if snap[0] is not None:
        core.__dict__["Agent"] = snap[0]
    if snap[1] is not None:
        core.__dict__["run_agent"] = snap[1]


def test_lazy_agent_resolves():
    import kohakuterrarium.core as core
    from kohakuterrarium.core.agent import Agent as _RealAgent

    # The lazy ``__getattr__`` must resolve to the real Agent class,
    # not some other object.
    assert core.Agent is _RealAgent


def test_lazy_run_agent_resolves():
    import kohakuterrarium.core as core
    from kohakuterrarium.core.agent import run_agent as _real_run_agent

    assert core.run_agent is _real_run_agent


def test_unknown_attr_raises():
    import kohakuterrarium.core as core

    with pytest.raises(AttributeError):
        core.nonexistent_attr
