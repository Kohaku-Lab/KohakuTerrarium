from io import StringIO

from kohakuterrarium.builtins.terminal_attention import safe_title, set_attention


class TTYBuffer(StringIO):
    def isatty(self) -> bool:
        return True


class PlainBuffer(StringIO):
    def isatty(self) -> bool:
        return False


def test_set_attention_writes_safe_osc_title(monkeypatch):
    monkeypatch.delenv("TERM", raising=False)
    stream = TTYBuffer()

    assert set_attention("input required", base_title="KT\x1b]2;bad\a", stream=stream)
    assert stream.getvalue() == "\x1b]2;KT]2;bad - input required\x1b\\"


def test_set_attention_is_noop_without_terminal(monkeypatch):
    monkeypatch.delenv("TERM", raising=False)
    stream = PlainBuffer()

    assert not set_attention("ready", stream=stream)
    assert stream.getvalue() == ""


def test_safe_title_removes_terminal_controls():
    assert safe_title("line\nnext\x1b\a") == "linenext"
