"""Tests for kt-biome Telegram input / output modules.

The SDK (``python-telegram-bot``) is mocked end-to-end — no network,
no real bot. These tests exercise:

1. Graceful failure when the SDK is missing.
2. Env-var expansion for the ``token`` option.
3. ``allow_chat_ids`` filtering.
4. Long-message splitting / code-fence preservation.

Tests follow kt-biome convention: no real sleeps, mock every SDK
surface via ``unittest.mock.MagicMock``.
"""

from __future__ import annotations

import asyncio
import sys
import types
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from kt_biome.io.telegram_input import (
    TelegramInput,
    expand_env_var,
    is_sdk_available,
)
from kt_biome.io.telegram_output import (
    TelegramOutput,
    escape_markdown_v2,
    split_for_telegram,
)

# ---------------------------------------------------------------------------
# SDK install / uninstall helpers (test-only shims)
# ---------------------------------------------------------------------------


def _install_fake_sdk() -> dict[str, Any]:
    """Register a minimal fake ``telegram`` / ``telegram.ext`` into
    ``sys.modules``. Returns a dict of the key mock handles."""
    telegram_mod = types.ModuleType("telegram")
    telegram_ext_mod = types.ModuleType("telegram.ext")

    # telegram.Bot
    bot_instance = MagicMock(name="Bot")
    bot_instance.send_message = AsyncMock(name="send_message")
    bot_cls = MagicMock(name="BotCls", return_value=bot_instance)
    telegram_mod.Bot = bot_cls

    # telegram.ext.Application — builder pattern
    application = MagicMock(name="Application")
    application.initialize = AsyncMock()
    application.start = AsyncMock()
    application.stop = AsyncMock()
    application.shutdown = AsyncMock()
    application.running = False
    application.updater = MagicMock(name="Updater")
    application.updater.start_polling = AsyncMock()
    application.updater.stop = AsyncMock()
    application.updater.running = False
    application.add_handler = MagicMock()

    builder = MagicMock(name="ApplicationBuilder")
    builder.token = MagicMock(return_value=builder)
    builder.build = MagicMock(return_value=application)
    application_cls = MagicMock(name="ApplicationCls")
    application_cls.builder = MagicMock(return_value=builder)
    telegram_ext_mod.Application = application_cls

    # telegram.ext.MessageHandler — just needs to exist
    telegram_ext_mod.MessageHandler = MagicMock(name="MessageHandler")

    # telegram.ext.filters — expose .ALL as a truthy sentinel
    filters_ns = types.SimpleNamespace(ALL=MagicMock(name="filters.ALL"))
    telegram_ext_mod.filters = filters_ns

    sys.modules["telegram"] = telegram_mod
    sys.modules["telegram.ext"] = telegram_ext_mod

    return {
        "telegram": telegram_mod,
        "telegram.ext": telegram_ext_mod,
        "application": application,
        "bot_instance": bot_instance,
        "bot_cls": bot_cls,
    }


def _uninstall_fake_sdk() -> None:
    for name in ("telegram", "telegram.ext"):
        sys.modules.pop(name, None)


@pytest.fixture
def fake_sdk():
    """Install fake telegram SDK, yield the handles, restore on teardown."""
    # Remove anything that may have been cached.
    _uninstall_fake_sdk()
    handles = _install_fake_sdk()
    yield handles
    _uninstall_fake_sdk()


# ---------------------------------------------------------------------------
# 1. SDK-missing behaviour
# ---------------------------------------------------------------------------


async def test_start_fails_cleanly_when_sdk_missing(monkeypatch):
    """``start()`` raises ImportError w/ pip hint when SDK is absent.

    Construction must still work (optional dep); only start() checks.
    """
    _uninstall_fake_sdk()

    # Block ``telegram`` import so importlib raises ImportError for us.
    real_import = (
        __builtins__["__import__"]
        if isinstance(__builtins__, dict)
        else __builtins__.__import__
    )

    def guarded_import(name, *args, **kwargs):
        if name == "telegram" or name.startswith("telegram."):
            raise ImportError(f"mocked: {name} not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", guarded_import)

    assert is_sdk_available() is False

    monkeypatch.setenv("TELEGRAM_TOKEN_TEST_MISSING", "dummy-token")
    inp = TelegramInput(options={"token": "${TELEGRAM_TOKEN_TEST_MISSING}"})

    with pytest.raises(ImportError) as excinfo:
        await inp.start()

    assert "python-telegram-bot" in str(excinfo.value)
    assert "pip install" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 2. Token env-var resolution
# ---------------------------------------------------------------------------


def test_expand_env_var_resolves(monkeypatch):
    monkeypatch.setenv("KT_TG_TOKEN_A", "secret-abc")
    assert expand_env_var("${KT_TG_TOKEN_A}") == "secret-abc"
    # Plain value passes through untouched.
    assert expand_env_var("plain-token") == "plain-token"


def test_expand_env_var_missing_raises(monkeypatch):
    monkeypatch.delenv("KT_TG_TOKEN_MISSING", raising=False)
    with pytest.raises(ValueError) as excinfo:
        expand_env_var("${KT_TG_TOKEN_MISSING}")
    assert "KT_TG_TOKEN_MISSING" in str(excinfo.value)


async def test_input_start_resolves_env_token(fake_sdk, monkeypatch):
    """TelegramInput.start() expands ${VAR} and passes result to the
    Application builder."""
    monkeypatch.setenv("KT_TG_TOKEN_B", "super-secret-xyz")
    inp = TelegramInput(options={"token": "${KT_TG_TOKEN_B}"})

    # Replace the background poll coroutine with a no-op so start()
    # doesn't sit on ``asyncio.sleep(3600)``.
    async def _noop_poll() -> None:
        await asyncio.sleep(0)

    inp._run_polling = _noop_poll  # type: ignore[assignment]

    await inp.start()

    # Application builder must have been called with the resolved token.
    builder_chain = fake_sdk["telegram.ext"].Application.builder.return_value
    builder_chain.token.assert_called_once_with("super-secret-xyz")
    builder_chain.build.assert_called_once()

    await inp.stop()


async def test_output_start_resolves_env_token(fake_sdk, monkeypatch):
    """TelegramOutput.start() expands ${VAR} and hands it to Bot()."""
    monkeypatch.setenv("KT_TG_TOKEN_C", "out-secret-123")
    out = TelegramOutput(options={"token": "${KT_TG_TOKEN_C}"})

    await out.start()

    fake_sdk["bot_cls"].assert_called_once_with(token="out-secret-123")

    await out.stop()


# ---------------------------------------------------------------------------
# 3. allow_chat_ids filter
# ---------------------------------------------------------------------------


def _fake_update(
    *,
    chat_id: int,
    user_id: int,
    text: str,
    chat_type: str = "private",
    message_id: int = 42,
    username: str = "alice",
) -> Any:
    """Build a MagicMock update that mimics python-telegram-bot's shape."""
    chat = MagicMock()
    chat.id = chat_id
    chat.type = chat_type
    user = MagicMock()
    user.id = user_id
    user.username = username
    message = MagicMock()
    message.chat = chat
    message.from_user = user
    message.text = text
    message.caption = None
    message.photo = []
    message.message_id = message_id
    update = MagicMock()
    update.message = message
    update.effective_message = message
    return update


async def test_allow_chat_ids_blocks_disallowed_chat():
    """Messages from chats not in the allow-list must be dropped and
    leave the input queue empty."""
    inp = TelegramInput(
        options={
            "token": "literal-token",
            "allow_chat_ids": [111, 222],
            "dm_only": False,  # irrelevant to this test
        }
    )

    allowed = _fake_update(chat_id=111, user_id=7, text="hello")
    blocked = _fake_update(chat_id=999, user_id=7, text="spy")

    await inp._handle_message(allowed, context=None)
    await inp._handle_message(blocked, context=None)

    # Exactly one event should be queued — the allowed one.
    assert inp._queue.qsize() == 1
    evt = await asyncio.wait_for(inp._queue.get(), timeout=0.1)
    assert evt.content == "hello"
    assert evt.context["metadata"]["chat_id"] == 111
    assert evt.context["metadata"]["platform"] == "telegram"


async def test_dm_only_blocks_group_chat():
    """With dm_only=True, group-chat messages are dropped."""
    inp = TelegramInput(options={"token": "t", "dm_only": True})

    group = _fake_update(chat_id=10, user_id=7, text="hi all", chat_type="group")
    await inp._handle_message(group, context=None)
    assert inp._queue.qsize() == 0


async def test_command_prefix_filter():
    """Messages not matching command_prefix are dropped; matching
    messages have the prefix stripped from ``content``."""
    inp = TelegramInput(
        options={"token": "t", "command_prefix": "/ask", "dm_only": False}
    )

    matching = _fake_update(chat_id=1, user_id=1, text="/ask who are you?")
    non_matching = _fake_update(chat_id=1, user_id=1, text="hello")

    await inp._handle_message(matching, context=None)
    await inp._handle_message(non_matching, context=None)

    assert inp._queue.qsize() == 1
    evt = await inp._queue.get()
    assert evt.content == "who are you?"


# ---------------------------------------------------------------------------
# 4. Long-message splitting
# ---------------------------------------------------------------------------


def test_split_for_telegram_short_message_passes_through():
    assert split_for_telegram("hello", limit=100) == ["hello"]


def test_split_for_telegram_respects_limit():
    limit = 200
    text = "word " * 200  # ~1000 chars
    chunks = split_for_telegram(text, limit=limit)
    assert len(chunks) > 1
    for c in chunks:
        assert len(c) <= limit, f"chunk too long: {len(c)}"


def test_split_for_telegram_preserves_code_fence():
    """A split that would otherwise land inside a code fence must close
    the fence for the emitted chunk and reopen it in the next."""
    # Build: preamble + big python code block + postamble.
    code_body = "\n".join(f"x_{i} = {i}" for i in range(100))
    text = (
        "Here is some code:\n"
        f"```python\n{code_body}\n```\n"
        "And this is the tail paragraph."
    )
    limit = 300
    chunks = split_for_telegram(text, limit=limit)

    assert len(chunks) > 1
    for c in chunks:
        assert len(c) <= limit
    # Each chunk must have an even number of ``` markers — i.e. fences
    # are balanced on a per-chunk basis.
    for c in chunks:
        count = c.count("```")
        assert count % 2 == 0, f"Unbalanced fence in chunk:\n{c!r}"


def test_split_for_telegram_never_breaks_mid_word_in_fence():
    """Corollary of the fence-balance rule: an identifier like
    ``some_long_name`` at the boundary must not be split across two
    chunks inside a fence."""
    body = "variable_" + "a" * 250  # single long token
    text = f"```\n{body}\n```"
    limit = 100
    chunks = split_for_telegram(text, limit=limit)

    for c in chunks:
        # Every chunk is a complete (possibly re-opened) fenced block.
        assert c.count("```") % 2 == 0
        assert len(c) <= limit


def test_escape_markdown_v2_escapes_specials():
    """MarkdownV2 escape helper escapes every special *outside* fences."""
    src = "Hello! This is a (test) with [link] and #hash."
    out = escape_markdown_v2(src)
    # Every special char is preceded by a backslash.
    for ch in "!()[]#.":
        assert f"\\{ch}" in out, f"{ch!r} was not escaped"


def test_escape_markdown_v2_leaves_code_fence_untouched():
    src = "before\n```\nprint('x.y!')\n```\nafter!"
    out = escape_markdown_v2(src)
    assert "print('x.y!')" in out  # fence content untouched
    assert "after\\!" in out  # non-fence content escaped


# ---------------------------------------------------------------------------
# Integration: output.flush() sends one message per chunk
# ---------------------------------------------------------------------------


async def test_output_flush_sends_chunks(fake_sdk):
    out = TelegramOutput(
        options={
            "token": "literal-token",
            "max_message_chars": 50,
            "parse_mode": None,
        }
    )
    await out.start()
    out.set_target_chat_id(424242)

    # Write a message long enough to require splitting.
    await out.write("alpha " * 30)
    await out.flush()

    send = fake_sdk["bot_instance"].send_message
    assert send.await_count >= 2
    for call in send.await_args_list:
        kwargs = call.kwargs
        assert kwargs["chat_id"] == 424242
        assert len(kwargs["text"]) <= 50

    await out.stop()


async def test_output_drop_when_no_chat_id(fake_sdk, caplog):
    """With no target chat id known, ``flush()`` warns and drops the
    buffered text rather than crashing."""
    out = TelegramOutput(options={"token": "literal-token"})
    await out.start()
    await out.write("something")
    await out.flush()  # must not raise

    fake_sdk["bot_instance"].send_message.assert_not_called()
    await out.stop()
