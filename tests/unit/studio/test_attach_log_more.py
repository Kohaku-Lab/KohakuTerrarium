"""Push studio.attach.log._tail_file backfill + poll loop coverage."""

import asyncio


from kohakuterrarium.studio.attach import log as log_mod


class _FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def send_json(self, data):
        self.sent.append(data)


class TestRunLogAttachErrorPath:
    async def test_error_during_tail_is_swallowed_even_if_send_and_close_fail(
        self, tmp_path, monkeypatch
    ):
        """Contract: ``run_log_attach`` must never let a tail-loop error
        escape — it logs, *tries* to send an error frame, *tries* to
        close, and swallows failures from both. With ``_tail_file``
        raising AND the error-frame send AND the close both raising,
        the handler still returns cleanly — exercising the full
        error-within-error arm (`except Exception: pass` around the
        error-frame send, `except Exception` around the close)."""
        log_file = tmp_path / "proc.log"
        log_file.write_text("[12:00:00] [m] [INFO] line\n", encoding="utf-8")
        monkeypatch.setattr(log_mod, "_find_current_process_log", lambda: log_file)

        async def _boom_tail(path, ws):
            raise RuntimeError("tail loop crashed")

        monkeypatch.setattr(log_mod, "_tail_file", _boom_tail)

        class _HostileWS:
            def __init__(self):
                self.accepted = False
                self.closed = False
                self.frames: list[dict] = []

            async def accept(self):
                self.accepted = True

            async def send_json(self, data):
                self.frames.append(data)
                # The meta frame goes through; the error frame fails.
                if data.get("type") == "error":
                    raise RuntimeError("ws send broken")

            async def close(self):
                self.closed = True
                raise RuntimeError("ws close broken")

        ws = _HostileWS()
        # Must return cleanly — no exception escapes.
        await log_mod.run_log_attach(ws)
        assert ws.accepted is True
        # meta frame sent, then the handler attempted the error frame
        # (which raised) and still attempted to close.
        assert ws.frames[0]["type"] == "meta"
        assert ws.frames[-1]["type"] == "error"
        assert ws.closed is True


class TestTailFileBackfill:
    async def test_seed_with_small_file(self, tmp_path):
        log_file = tmp_path / "tiny.log"
        # File smaller than the 32K chunk threshold.
        log_file.write_text(
            "[12:00:00] [mod] [INFO] line1\n" "[12:00:01] [mod] [INFO] line2\n"
        )
        ws = _FakeWebSocket()

        # Patch asyncio.sleep so the follow loop runs only one iteration
        # and then we cancel.
        original_sleep = asyncio.sleep

        async def _instant_sleep(_t):
            raise asyncio.CancelledError()

        log_mod.asyncio.sleep = _instant_sleep
        try:
            try:
                await log_mod._tail_file(log_file, ws)
            except asyncio.CancelledError:
                pass
        finally:
            log_mod.asyncio.sleep = original_sleep

        assert any(s["type"] == "line" for s in ws.sent)

    async def test_seed_with_large_file_skips_partial_first(self, tmp_path):
        log_file = tmp_path / "big.log"
        # Write more than 32 KiB so the partial-line skip branch fires.
        big_lines = (
            "\n".join(f"[12:00:{i:02d}] [mod] [INFO] line {i}" for i in range(2000))
            + "\n"
        )
        log_file.write_text(big_lines)
        ws = _FakeWebSocket()

        original_sleep = asyncio.sleep

        async def _stop(_t):
            raise asyncio.CancelledError()

        log_mod.asyncio.sleep = _stop
        try:
            try:
                await log_mod._tail_file(log_file, ws)
            except asyncio.CancelledError:
                pass
        finally:
            log_mod.asyncio.sleep = original_sleep

        # Seeded with 200-line tail.
        line_frames = [s for s in ws.sent if s["type"] == "line"]
        assert 1 <= len(line_frames) <= 200

    async def test_new_lines_appended_during_poll(self, tmp_path):
        log_file = tmp_path / "live.log"
        log_file.write_text("[12:00:00] [m] [INFO] start\n")

        ws = _FakeWebSocket()
        tick = {"n": 0}

        async def _append_then_stop(_t):
            tick["n"] += 1
            if tick["n"] == 1:
                # Append a new line so the next readline() picks it up.
                with open(log_file, "a", encoding="utf-8") as f:
                    f.write("[12:00:01] [m] [INFO] follow\n")
            elif tick["n"] >= 3:
                raise asyncio.CancelledError()

        original_sleep = asyncio.sleep
        log_mod.asyncio.sleep = _append_then_stop
        try:
            try:
                await log_mod._tail_file(log_file, ws)
            except asyncio.CancelledError:
                pass
        finally:
            log_mod.asyncio.sleep = original_sleep

        # The follow-line was streamed.
        assert any("follow" in (s.get("text") or "") for s in ws.sent)

    async def test_polls_skips_blank_lines(self, tmp_path):
        log_file = tmp_path / "blank.log"
        log_file.write_text("\n[12:00:00] [m] [INFO] one\n\n")

        ws = _FakeWebSocket()
        original_sleep = asyncio.sleep

        async def _stop(_t):
            raise asyncio.CancelledError()

        log_mod.asyncio.sleep = _stop
        try:
            try:
                await log_mod._tail_file(log_file, ws)
            except asyncio.CancelledError:
                pass
        finally:
            log_mod.asyncio.sleep = original_sleep

        # Blank lines filtered out — only the real entry surfaces, and
        # no frame carries empty text.
        line_frames = [s for s in ws.sent if s["type"] == "line"]
        assert any("one" in (s.get("text") or "") for s in line_frames)
        assert all((s.get("text") or "").strip() for s in line_frames)
