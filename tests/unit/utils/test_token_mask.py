"""Unit tests for the token-masking logging filter.

Verifies that the three regex patterns (URL query, JSON body, KV) all
mask their tokens before reaching the handler, and that the filter
never crashes on weird record shapes.
"""

import logging

from kohakuterrarium.utils.logging import _mask_tokens, _TokenMaskingFilter


class TestMaskTokens:
    def test_url_query_token_masked(self):
        out = _mask_tokens("connecting to wss://x/lab?token=secretvalue123")
        assert "secretvalue123" not in out
        assert "?token=****" in out

    def test_url_amp_query_token_masked(self):
        out = _mask_tokens("ws://x?node=a&token=abcd1234efgh")
        assert "abcd1234efgh" not in out
        assert "&token=****" in out

    def test_json_token_masked(self):
        out = _mask_tokens('payload: {"token": "deadbeef123456", "other": 1}')
        assert "deadbeef123456" not in out
        assert '"token": "****"' in out

    def test_kv_token_masked(self):
        out = _mask_tokens("auth: token=verylongsecretvalue here")
        # _TOKEN_KV_RE keys on a word-boundary `token` followed by =/:.
        assert "verylongsecretvalue" not in out
        assert "token=****" in out

    def test_no_token_passthrough(self):
        original = "boring message with no secrets"
        assert _mask_tokens(original) == original

    def test_short_kv_token_not_masked(self):
        # Below the 8-char threshold of the KV pattern — we err on the
        # side of NOT masking unknown short strings so we don't drop
        # legit content like ``token=ok``.
        assert _mask_tokens("token=ok") == "token=ok"


class TestTokenMaskingFilter:
    def _make_record(self, msg, *args):
        return logging.LogRecord(
            name="t",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg=msg,
            args=args,
            exc_info=None,
        )

    def test_filter_masks_string_msg(self):
        rec = self._make_record("connecting to ws://x/lab?token=sekretvalue")
        _TokenMaskingFilter().filter(rec)
        assert "sekretvalue" not in rec.msg
        assert "****" in rec.msg

    def test_filter_masks_args_tuple(self):
        rec = self._make_record("hit %s", "wss://x/lab?token=sekret123")
        _TokenMaskingFilter().filter(rec)
        # args were re-tupled with masked values
        assert "sekret123" not in rec.args[0]
        assert "****" in rec.args[0]

    def test_filter_passes_record_through(self):
        rec = self._make_record("nothing to mask here")
        assert _TokenMaskingFilter().filter(rec) is True

    def test_filter_never_raises(self):
        # Non-string record.msg (KTLogger uses dicts for structured logs).
        rec = self._make_record({"event": "ok", "n": 1})
        # Must return True even though the msg is not a string.
        assert _TokenMaskingFilter().filter(rec) is True
