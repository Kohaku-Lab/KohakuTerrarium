from kohakuterrarium.builtins.tui.session import TUISession


class App:
    is_running = True

    def __init__(self):
        self.snapshots = []

    def call_later(self, callback, *args):
        callback(*args)

    def update_attention(self, attention):
        self.snapshots.append(attention)


def test_session_tracks_target_attention_and_preserves_pending_when_read():
    session = TUISession()
    session._app = App()

    session.attention_processing_start("reviewer")
    session.attention_processing_end("reviewer")
    session.attention_processing_end("reviewer")
    session.attention_pending("ask", "reviewer")
    session.attention_pending("ask", "reviewer")
    session.clear_attention_completed("reviewer")

    state = session._attention_state("reviewer")
    assert state["completed"] is False
    assert state["count"] == 1
    assert state["pending"] == {"ask"}
    assert state["processing"] is False

    session.attention_clear("ask", "reviewer")
    assert state["pending"] == set()
