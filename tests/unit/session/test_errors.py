"""Unit tests for :mod:`kohakuterrarium.session.errors`."""

from kohakuterrarium.session.errors import (
    AlreadyAttachedError,
    ForkNotStableError,
    NotAttachedError,
)


def test_fork_not_stable_is_exception():
    assert issubclass(ForkNotStableError, Exception)


def test_already_attached_is_exception():
    assert issubclass(AlreadyAttachedError, Exception)


def test_not_attached_is_exception():
    assert issubclass(NotAttachedError, Exception)


def test_distinct_error_types():
    # The three exceptions are distinct classes.
    assert ForkNotStableError is not AlreadyAttachedError
    assert ForkNotStableError is not NotAttachedError
    assert AlreadyAttachedError is not NotAttachedError


def test_attach_module_reexports():
    """:mod:`session.attach` is a thin re-export shim — confirm the
    aliases resolve to the real ``attachment_service`` symbols."""
    from kohakuterrarium.session import attach as attach_mod
    from kohakuterrarium.session import attachment_service

    assert attach_mod.attach_agent_to_session is (
        attachment_service.attach_agent_to_session
    )
    assert attach_mod.detach_agent_from_session is (
        attachment_service.detach_agent_from_session
    )
    assert attach_mod.get_attach_state is attachment_service.get_attach_state
    # ``__all__`` advertises the three symbols.
    assert set(attach_mod.__all__) == {
        "attach_agent_to_session",
        "detach_agent_from_session",
        "get_attach_state",
    }


def test_agent_attach_helpers_delegate():
    """``session.agent_attach`` provides bound-style wrappers that
    delegate to ``attachment_service``."""
    from kohakuterrarium.session import agent_attach as aa
    from kohakuterrarium.session import attachment_service

    # Module re-exports the low-level imports.
    assert aa.attach_agent_to_session is (attachment_service.attach_agent_to_session)
    assert aa.detach_agent_from_session is (
        attachment_service.detach_agent_from_session
    )

    # Wrappers forward arguments.
    captured = []

    def fake_attach(self, session, role):
        captured.append(("attach", self, session, role))

    def fake_detach(self):
        captured.append(("detach", self))

    import kohakuterrarium.session.agent_attach as aa_mod

    aa_mod.attach_agent_to_session = fake_attach
    aa_mod.detach_agent_from_session = fake_detach
    try:
        aa.attach_to_session("self", "sess", "rev")
        aa.detach_from_session("self")
        assert captured == [
            ("attach", "self", "sess", "rev"),
            ("detach", "self"),
        ]
    finally:
        # Reset to the real symbols so other tests still see them.
        aa_mod.attach_agent_to_session = attachment_service.attach_agent_to_session
        aa_mod.detach_agent_from_session = attachment_service.detach_agent_from_session
