"""Coverage for ``studio.sessions.handles`` — Session / SessionListing."""

from kohakuterrarium.studio.sessions.handles import Session, SessionListing


def test_session_to_dict():
    sess = Session(
        session_id="s1",
        name="alice",
        creatures=[{"name": "alice"}],
        channels=[{"name": "c"}],
        created_at="2024",
        config_path="x.yaml",
        pwd=".",
        has_root=False,
    )
    d = sess.to_dict()
    assert d["session_id"] == "s1"
    assert d["creatures"] == [{"name": "alice"}]
    assert d["has_root"] is False
    assert "kind" not in d


def test_session_listing_to_dict():
    listing = SessionListing(session_id="s1", name="team", running=True, creatures=3)
    d = listing.to_dict()
    assert d == {
        "session_id": "s1",
        "name": "team",
        "running": True,
        "creatures": 3,
    }
