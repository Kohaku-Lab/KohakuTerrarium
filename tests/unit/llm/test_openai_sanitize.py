"""Unit tests for ``llm/openai_sanitize.py`` — content-part sanitisation.

Behavior-first: assert that KT-internal keys are stripped from content
parts, that the no-op path returns the *identical* list object (the
docstring's identity-preservation promise), surrogate stripping, and
the request-shape log-level routing.
"""

import logging

from kohakuterrarium.llm.openai_sanitize import (
    log_request_shape,
    strip_kt_extras,
    strip_surrogates,
)


class TestStripKtExtras:
    def test_clean_messages_return_identical_object(self):
        messages = [
            {"role": "user", "content": "plain string"},
            {
                "role": "user",
                "content": [{"type": "text", "text": "clean"}],
            },
        ]
        # nothing to clean → same list object back (no-op fast path)
        assert strip_kt_extras(messages) is messages

    def test_image_part_meta_key_stripped(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:...", "detail": "high"},
                        "meta": {"source_type": "upload", "source_name": "a.png"},
                    }
                ],
            }
        ]
        out = strip_kt_extras(messages)
        part = out[0]["content"][0]
        assert "meta" not in part
        assert part == {
            "type": "image_url",
            "image_url": {"url": "data:...", "detail": "high"},
        }

    def test_unknown_image_url_subkeys_stripped(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": "u", "badge": "x", "detail": "low"},
                    }
                ],
            }
        ]
        out = strip_kt_extras(messages)
        assert out[0]["content"][0]["image_url"] == {"url": "u", "detail": "low"}

    def test_text_part_extra_key_stripped(self):
        messages = [
            {
                "role": "user",
                "content": [{"type": "text", "text": "hi", "ui_hint": "bold"}],
            }
        ]
        out = strip_kt_extras(messages)
        assert out[0]["content"][0] == {"type": "text", "text": "hi"}

    def test_unknown_part_type_passed_through(self):
        messages = [
            {
                "role": "user",
                "content": [{"type": "video", "data": "blob", "extra": "kept"}],
            }
        ]
        out = strip_kt_extras(messages)
        # unknown schema → not policed, passed verbatim
        assert out[0]["content"][0] == {
            "type": "video",
            "data": "blob",
            "extra": "kept",
        }

    def test_string_content_messages_passed_through(self):
        messages = [{"role": "user", "content": "just text"}]
        out = strip_kt_extras(messages)
        assert out[0]["content"] == "just text"

    def test_non_dict_parts_preserved(self):
        messages = [{"role": "user", "content": ["raw", {"type": "text", "text": "t"}]}]
        out = strip_kt_extras(messages)
        assert out[0]["content"][0] == "raw"

    def test_only_dirty_message_reallocated_clean_one_shared(self):
        clean_msg = {"role": "system", "content": [{"type": "text", "text": "ok"}]}
        dirty_msg = {
            "role": "user",
            "content": [{"type": "text", "text": "t", "meta": 1}],
        }
        out = strip_kt_extras([clean_msg, dirty_msg])
        # clean message identity preserved, dirty one rebuilt
        assert out[0] is clean_msg
        assert out[1] is not dirty_msg
        assert out[1]["content"][0] == {"type": "text", "text": "t"}


class TestStripSurrogates:
    def test_lone_surrogate_dropped(self):
        text = "valid\ud800text"
        assert strip_surrogates(text) == "validtext"

    def test_clean_text_unchanged(self):
        assert strip_surrogates("hello world") == "hello world"

    def test_unicode_scalar_values_preserved(self):
        assert strip_surrogates("emoji 🎉 cjk 漢字") == "emoji 🎉 cjk 漢字"


class _RecordingHandler(logging.Handler):
    """Capture records straight off the (non-propagating) kt logger tree."""

    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


def _capture_kt_logs():
    """Attach a recording handler to the kohakuterrarium logger.

    ``utils.logging`` sets ``propagate=False`` on the ``kohakuterrarium``
    logger, so pytest's ``caplog`` (which hooks the root) never sees its
    records. Capture them at the source instead.
    """
    handler = _RecordingHandler()
    kt_logger = logging.getLogger("kohakuterrarium")
    kt_logger.addHandler(handler)
    return kt_logger, handler


class TestLogRequestShape:
    def test_logs_at_info_when_images_present(self):
        kt_logger, handler = _capture_kt_logs()
        try:
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "t"},
                        {"type": "image_url", "image_url": {"url": "u"}},
                    ],
                }
            ]
            log_request_shape("outgoing", "gpt-x", messages)
        finally:
            kt_logger.removeHandler(handler)
        # an image-bearing request is logged at INFO with the image count
        info = [r for r in handler.records if r.levelno == logging.INFO]
        assert len(info) == 1
        assert getattr(info[0], "image_parts", None) == 1

    def test_file_parts_counted_in_info_log(self):
        kt_logger, handler = _capture_kt_logs()
        try:
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "t"},
                        {"type": "file", "file": {"name": "a.pdf"}},
                    ],
                }
            ]
            log_request_shape("outgoing", "gpt-x", messages)
        finally:
            kt_logger.removeHandler(handler)
        info = [r for r in handler.records if r.levelno == logging.INFO]
        assert len(info) == 1
        assert getattr(info[0], "file_parts", None) == 1

    def test_logs_at_debug_when_text_only(self):
        kt_logger, handler = _capture_kt_logs()
        # the module logger is INFO-level by default; drop it to DEBUG so
        # the DEBUG record actually reaches our handler.
        module_logger = logging.getLogger("kohakuterrarium.llm.openai_sanitize")
        original = module_logger.level
        module_logger.setLevel(logging.DEBUG)
        try:
            log_request_shape("outgoing", "gpt-x", [{"role": "user", "content": "t"}])
        finally:
            module_logger.setLevel(original)
            kt_logger.removeHandler(handler)
        # a text-only request is logged at DEBUG, never INFO
        assert all(r.levelno != logging.INFO for r in handler.records)
        assert any(r.levelno == logging.DEBUG for r in handler.records)
