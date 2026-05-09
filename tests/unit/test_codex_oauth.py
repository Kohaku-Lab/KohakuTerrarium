"""
Tests for Codex OAuth authentication and provider.

These tests cover offline functionality only (no browser auth, no network).
"""

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from kohakuterrarium.llm.codex_auth import (
    AUTH_URL,
    CLIENT_ID,
    CODEX_CLI_TOKEN_PATH,
    DEFAULT_TOKEN_PATH,
    CodexTokens,
    _build_auth_url,
    _generate_pkce,
)
from kohakuterrarium.llm.codex_provider import CODEX_BASE_URL, CodexOAuthProvider

# =========================================================================
# CodexTokens dataclass
# =========================================================================


class TestCodexTokens:
    """Tests for the CodexTokens dataclass."""

    def test_is_expired_true(self):
        tokens = CodexTokens(
            access_token="tok",
            refresh_token="ref",
            expires_at=time.time() - 100,
        )
        assert tokens.is_expired()

    def test_is_expired_within_buffer(self):
        # Expires in 30s, but 60s buffer makes it "expired"
        tokens = CodexTokens(
            access_token="tok",
            refresh_token="ref",
            expires_at=time.time() + 30,
        )
        assert tokens.is_expired()

    def test_is_expired_false(self):
        tokens = CodexTokens(
            access_token="tok",
            refresh_token="ref",
            expires_at=time.time() + 3600,
        )
        assert not tokens.is_expired()

    def test_is_expired_default(self):
        # Default expires_at=0 should be expired
        tokens = CodexTokens(access_token="tok", refresh_token="ref")
        assert tokens.is_expired()

    def test_save_and_load(self, tmp_path: Path):
        token_path = tmp_path / "tokens.json"
        original = CodexTokens(
            access_token="my-access-token",
            refresh_token="my-refresh-token",
            expires_at=1234567890.0,
        )
        original.save(token_path)

        # Verify file was written
        assert token_path.exists()
        data = json.loads(token_path.read_text())
        assert data["access_token"] == "my-access-token"
        assert data["refresh_token"] == "my-refresh-token"
        assert data["expires_at"] == 1234567890.0

        # Load it back
        loaded = CodexTokens.load(token_path)
        assert loaded is not None
        assert loaded.access_token == "my-access-token"
        assert loaded.refresh_token == "my-refresh-token"
        assert loaded.expires_at == 1234567890.0

    def test_save_creates_parent_dirs(self, tmp_path: Path):
        token_path = tmp_path / "deep" / "nested" / "tokens.json"
        tokens = CodexTokens(access_token="tok", refresh_token="ref")
        tokens.save(token_path)
        assert token_path.exists()

    def test_load_nonexistent_returns_none(self, tmp_path: Path):
        result = CodexTokens.load(tmp_path / "nonexistent.json")
        assert result is None

    def test_load_empty_access_token_returns_none(self, tmp_path: Path):
        token_path = tmp_path / "tokens.json"
        token_path.write_text(
            json.dumps(
                {
                    "access_token": "",
                    "refresh_token": "ref",
                }
            )
        )
        result = CodexTokens.load(token_path)
        assert result is None

    def test_load_malformed_json_returns_none(self, tmp_path: Path):
        token_path = tmp_path / "tokens.json"
        token_path.write_text("not json at all")
        result = CodexTokens.load(token_path)
        assert result is None


# =========================================================================
# PKCE generation
# =========================================================================


class TestPKCE:
    """Tests for PKCE code generation."""

    def test_generate_pkce_returns_two_strings(self):
        verifier, challenge = _generate_pkce()
        assert isinstance(verifier, str)
        assert isinstance(challenge, str)
        assert len(verifier) > 32
        assert len(challenge) > 16

    def test_generate_pkce_unique(self):
        v1, c1 = _generate_pkce()
        v2, c2 = _generate_pkce()
        assert v1 != v2
        assert c1 != c2

    def test_build_auth_url(self):
        url = _build_auth_url("test-challenge", "test-state")
        assert url.startswith(AUTH_URL)
        assert "client_id=" + CLIENT_ID in url
        assert "redirect_uri=" in url  # URL-encoded
        assert "code_challenge=test-challenge" in url
        assert "state=test-state" in url
        assert "code_challenge_method=S256" in url
        assert "response_type=code" in url
        # Scope should be URL-encoded (no raw spaces)
        assert "scope=openid+email+profile" in url or "scope=openid%20email" in url


# =========================================================================
# CodexOAuthProvider
# =========================================================================


class TestCodexOAuthProvider:
    """Tests for the CodexOAuthProvider class."""

    def test_init_defaults(self):
        provider = CodexOAuthProvider()
        assert provider.model == "gpt-5.4"
        assert provider._tokens is None
        assert provider._client is None

    def test_init_custom_model(self):
        provider = CodexOAuthProvider(model="o3")
        assert provider.model == "o3"

    def test_last_tool_calls_default_empty(self):
        provider = CodexOAuthProvider()
        assert provider.last_tool_calls == []

    @pytest.mark.asyncio
    async def test_ensure_authenticated_uses_cached(self, tmp_path: Path):
        """If valid tokens exist on disk, no browser login needed."""
        token_path = tmp_path / "tokens.json"
        CodexTokens(
            access_token="cached-token",
            refresh_token="cached-refresh",
            expires_at=time.time() + 3600,
        ).save(token_path)

        provider = CodexOAuthProvider()
        with patch("kohakuterrarium.llm.codex_auth.DEFAULT_TOKEN_PATH", token_path):
            await provider.ensure_authenticated()

        assert provider._tokens is not None
        assert provider._tokens.access_token == "cached-token"

    @pytest.mark.asyncio
    async def test_close_without_client(self):
        """Closing without ever making a request should not error."""
        provider = CodexOAuthProvider()
        await provider.close()  # Should not raise


# =========================================================================
# _to_responses_input conversion
# =========================================================================


class TestToResponsesInput:
    """Tests for Chat Completions -> Responses API message conversion."""

    def test_user_string_content(self):
        messages = [{"role": "user", "content": "Hello"}]
        result = CodexOAuthProvider._to_responses_input(messages)
        assert result == [
            {"role": "user", "content": [{"type": "input_text", "text": "Hello"}]}
        ]

    def test_user_multimodal_content(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is this?"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "https://example.com/img.png"},
                    },
                ],
            }
        ]
        result = CodexOAuthProvider._to_responses_input(messages)
        assert len(result) == 1
        assert result[0]["role"] == "user"
        parts = result[0]["content"]
        assert parts[0] == {"type": "input_text", "text": "What is this?"}
        assert parts[1] == {
            "type": "input_image",
            "image_url": "https://example.com/img.png",
        }

    def test_assistant_text_only(self):
        messages = [{"role": "assistant", "content": "I'll help you"}]
        result = CodexOAuthProvider._to_responses_input(messages)
        assert result == [
            {
                "role": "assistant",
                "content": [{"type": "output_text", "text": "I'll help you"}],
            }
        ]

    def test_assistant_with_tool_calls(self):
        messages = [
            {
                "role": "assistant",
                "content": "Let me check.",
                "tool_calls": [
                    {
                        "id": "call_abc",
                        "function": {
                            "name": "bash",
                            "arguments": '{"command": "ls"}',
                        },
                    }
                ],
            }
        ]
        result = CodexOAuthProvider._to_responses_input(messages)
        assert len(result) == 2
        assert result[0] == {
            "role": "assistant",
            "content": [{"type": "output_text", "text": "Let me check."}],
        }
        assert result[1] == {
            "type": "function_call",
            "call_id": "call_abc",
            "name": "bash",
            "arguments": '{"command": "ls"}',
        }

    def test_assistant_tool_calls_no_content(self):
        """Assistant with tool calls but no text content."""
        messages = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {"name": "read", "arguments": '{"path": "/a"}'},
                    },
                    {
                        "id": "call_2",
                        "function": {"name": "read", "arguments": '{"path": "/b"}'},
                    },
                ],
            }
        ]
        result = CodexOAuthProvider._to_responses_input(messages)
        # No text item, just two function_call items
        assert len(result) == 2
        assert result[0]["type"] == "function_call"
        assert result[0]["call_id"] == "call_1"
        assert result[1]["type"] == "function_call"
        assert result[1]["call_id"] == "call_2"

    def test_tool_result(self):
        messages = [
            {
                "role": "tool",
                "tool_call_id": "call_abc",
                "content": "file1.txt\nfile2.txt",
            }
        ]
        result = CodexOAuthProvider._to_responses_input(messages)
        assert result == [
            {
                "type": "function_call_output",
                "call_id": "call_abc",
                "output": "file1.txt\nfile2.txt",
            }
        ]

    def test_tool_result_multimodal_image(self):
        """Tool results with images use the array form so the image
        rides directly inside ``function_call_output.output`` — that's
        the canonical Responses API shape for multimodal tool returns.
        """
        messages = [
            {
                "role": "tool",
                "tool_call_id": "call_img",
                "content": [
                    {"type": "text", "text": "rendered chart"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,AAAA"},
                    },
                ],
            }
        ]
        result = CodexOAuthProvider._to_responses_input(messages)
        assert result == [
            {
                "type": "function_call_output",
                "call_id": "call_img",
                "output": [
                    {"type": "input_text", "text": "rendered chart"},
                    {
                        "type": "input_image",
                        "image_url": "data:image/png;base64,AAAA",
                    },
                ],
            }
        ]

    def test_tool_result_image_only_uses_array_form(self):
        """Tool results with only an image: array-form output with a
        single ``input_image`` part, no synthetic followup, no
        placeholder text.
        """
        messages = [
            {
                "role": "tool",
                "tool_call_id": "call_img",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,AAAA"},
                    },
                ],
            }
        ]
        result = CodexOAuthProvider._to_responses_input(messages)
        assert result == [
            {
                "type": "function_call_output",
                "call_id": "call_img",
                "output": [
                    {
                        "type": "input_image",
                        "image_url": "data:image/png;base64,AAAA",
                    },
                ],
            }
        ]

    def test_tool_result_text_only_list_uses_string(self):
        """Text-only list content keeps the historical string output —
        no upgrade to the array form.
        """
        messages = [
            {
                "role": "tool",
                "tool_call_id": "call_txt",
                "content": [{"type": "text", "text": "ok"}],
            }
        ]
        result = CodexOAuthProvider._to_responses_input(messages)
        assert result == [
            {
                "type": "function_call_output",
                "call_id": "call_txt",
                "output": "ok",
            }
        ]

    def test_artifact_url_resolved_to_data_url(self, tmp_path, monkeypatch):
        """Relative ``/api/sessions/.../artifacts/...`` URLs are not
        valid URLs to Codex's validator. Resolver reads the on-disk
        artifact and rewrites the URL to a ``data:`` URL inline.
        """
        from kohakuterrarium.llm import codex_format

        # Lay out a fake session + artifacts dir matching the URL.
        sessions = tmp_path / "sessions"
        sessions.mkdir()
        artifacts = sessions / "graph_demo.artifacts"
        (artifacts / "tool_outputs").mkdir(parents=True)
        png_bytes = b"\x89PNG\r\n\x1a\nFAKE"
        (artifacts / "tool_outputs" / "shot.png").write_bytes(png_bytes)

        # codex_format imports ``_session_dir`` at module top, so the
        # patched name lives there; the original-module binding is no
        # longer the one read by ``_resolve_artifact_url``.
        monkeypatch.setattr(
            "kohakuterrarium.llm.codex_format._session_dir",
            lambda: sessions,
        )

        messages = [
            {
                "role": "tool",
                "tool_call_id": "call_img",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "/api/sessions/graph_demo/artifacts/tool_outputs/shot.png"
                        },
                    },
                ],
            }
        ]
        result = codex_format.to_responses_input(messages)
        assert len(result) == 1
        out = result[0]["output"]
        assert isinstance(out, list)
        url = out[0]["image_url"]
        assert url.startswith("data:image/png;base64,")
        # Decode round-trip to confirm the bytes match.
        import base64

        decoded = base64.b64decode(url.split(",", 1)[1])
        assert decoded == png_bytes

    def test_system_messages_skipped(self):
        messages = [{"role": "system", "content": "You are helpful."}]
        result = CodexOAuthProvider._to_responses_input(messages)
        assert result == []

    def test_full_conversation(self):
        """Multi-turn conversation with tool use round-trip."""
        messages = [
            {"role": "user", "content": "List files"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {"name": "bash", "arguments": '{"command":"ls"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "a.py\nb.py"},
            {"role": "assistant", "content": "Found a.py and b.py."},
            {"role": "user", "content": "Read a.py"},
        ]
        result = CodexOAuthProvider._to_responses_input(messages)
        assert len(result) == 5
        assert result[0]["role"] == "user"
        assert result[1]["type"] == "function_call"
        assert result[2]["type"] == "function_call_output"
        assert result[3]["role"] == "assistant"
        assert result[4]["role"] == "user"

    def test_empty_messages(self):
        result = CodexOAuthProvider._to_responses_input([])
        assert result == []

    def test_unknown_role_skipped(self):
        """Messages with unknown roles are silently ignored."""
        messages = [{"role": "unknown", "content": "???"}]
        result = CodexOAuthProvider._to_responses_input(messages)
        assert result == []


# =========================================================================
# _fix_tool_call_pairing — ensures Responses API tool call ordering
# =========================================================================


class TestFixToolCallPairing:
    """Tests for function_call / function_call_output pairing and ordering.

    The Responses API requires every function_call to be immediately followed
    by its function_call_output with matching call_id. These tests verify that
    _fix_tool_call_pairing enforces this invariant under all conditions
    (normal, truncated, compacted, reordered, etc.).
    """

    @staticmethod
    def _fix(items: list[dict]) -> list[dict]:
        return CodexOAuthProvider._fix_tool_call_pairing(items)

    @staticmethod
    def _make_call(call_id: str, name: str = "bash") -> dict:
        return {
            "type": "function_call",
            "call_id": call_id,
            "name": name,
            "arguments": "{}",
        }

    @staticmethod
    def _make_output(call_id: str, output: str = "ok") -> dict:
        return {
            "type": "function_call_output",
            "call_id": call_id,
            "output": output,
        }

    @staticmethod
    def _make_user(text: str = "hello") -> dict:
        return {"role": "user", "content": [{"type": "input_text", "text": text}]}

    @staticmethod
    def _make_assistant(text: str = "sure") -> dict:
        return {
            "role": "assistant",
            "content": [{"type": "output_text", "text": text}],
        }

    # --- Normal cases (already correct) ---

    def test_normal_single_tool_call(self):
        """Correct pairing should be preserved as-is."""
        items = [
            self._make_user(),
            self._make_call("c1"),
            self._make_output("c1"),
            self._make_assistant(),
        ]
        result = self._fix(items)
        assert result == items

    def test_normal_parallel_tool_calls(self):
        """Multiple tool calls in one turn, all with outputs."""
        items = [
            self._make_user(),
            self._make_call("c1", "bash"),
            self._make_call("c2", "read"),
            self._make_output("c1", "files"),
            self._make_output("c2", "content"),
            self._make_assistant(),
        ]
        result = self._fix(items)
        # Each call should be immediately followed by its output
        assert result[0] == self._make_user()
        assert result[1] == self._make_call("c1", "bash")
        assert result[2] == self._make_output("c1", "files")
        assert result[3] == self._make_call("c2", "read")
        assert result[4] == self._make_output("c2", "content")
        assert result[5] == self._make_assistant()

    def test_normal_multi_turn_tool_use(self):
        """Multiple tool-use turns in sequence."""
        items = [
            self._make_user("list files"),
            self._make_call("c1", "bash"),
            self._make_output("c1", "a.py"),
            self._make_call("c2", "read"),
            self._make_output("c2", "code"),
            self._make_assistant("done"),
            self._make_user("thanks"),
        ]
        result = self._fix(items)
        assert result == items  # Already in correct order

    # --- Missing output (truncation / compaction removed tool result) ---

    def test_missing_output_adds_placeholder(self):
        """function_call without output gets a placeholder inserted."""
        items = [
            self._make_user(),
            self._make_call("c1", "bash"),
            self._make_assistant(),
        ]
        result = self._fix(items)
        assert len(result) == 4
        assert result[1] == self._make_call("c1", "bash")
        assert result[2]["type"] == "function_call_output"
        assert result[2]["call_id"] == "c1"
        assert "bash" in result[2]["output"]
        assert result[3] == self._make_assistant()

    def test_missing_output_parallel_calls(self):
        """Two calls, only one has output — placeholder added for the other."""
        items = [
            self._make_call("c1", "bash"),
            self._make_call("c2", "read"),
            self._make_output("c2", "content"),
        ]
        result = self._fix(items)
        assert result[0] == self._make_call("c1", "bash")
        assert result[1]["type"] == "function_call_output"
        assert result[1]["call_id"] == "c1"
        assert result[2] == self._make_call("c2", "read")
        assert result[3] == self._make_output("c2", "content")

    def test_all_outputs_missing(self):
        """All outputs lost — placeholders for every call."""
        items = [
            self._make_user(),
            self._make_call("c1"),
            self._make_call("c2"),
            self._make_assistant(),
        ]
        result = self._fix(items)
        assert len(result) == 6
        assert result[1]["type"] == "function_call"
        assert result[2]["type"] == "function_call_output"
        assert result[2]["call_id"] == "c1"
        assert result[3]["type"] == "function_call"
        assert result[4]["type"] == "function_call_output"
        assert result[4]["call_id"] == "c2"

    # --- Orphan outputs (truncation removed the assistant+tool_calls) ---

    def test_orphan_output_dropped(self):
        """function_call_output without matching call is removed."""
        items = [
            self._make_output("c_old", "stale result"),
            self._make_user(),
            self._make_call("c1"),
            self._make_output("c1"),
        ]
        result = self._fix(items)
        assert len(result) == 3
        assert result[0] == self._make_user()
        assert result[1] == self._make_call("c1")
        assert result[2] == self._make_output("c1")

    def test_multiple_orphan_outputs_dropped(self):
        """Several orphan outputs from compacted history."""
        items = [
            self._make_output("old1"),
            self._make_output("old2"),
            self._make_output("old3"),
            self._make_user(),
            self._make_assistant(),
        ]
        result = self._fix(items)
        assert len(result) == 2
        assert result[0] == self._make_user()
        assert result[1] == self._make_assistant()

    # --- Reordering (output separated from call by later messages) ---

    def test_output_after_later_messages_gets_reordered(self):
        """Output displaced to after user message is pulled back next to call."""
        items = [
            self._make_call("c1", "bash"),
            self._make_user("next question"),
            self._make_assistant("answer"),
            self._make_output("c1", "result"),
        ]
        result = self._fix(items)
        assert result[0] == self._make_call("c1", "bash")
        assert result[1] == self._make_output("c1", "result")
        assert result[2] == self._make_user("next question")
        assert result[3] == self._make_assistant("answer")

    def test_mixed_reorder_and_missing(self):
        """One output displaced, another missing entirely."""
        items = [
            self._make_call("c1", "bash"),
            self._make_call("c2", "read"),
            self._make_user(),
            self._make_output("c1", "files"),
            # c2 output is missing
        ]
        result = self._fix(items)
        assert result[0] == self._make_call("c1", "bash")
        assert result[1] == self._make_output("c1", "files")
        assert result[2] == self._make_call("c2", "read")
        assert result[3]["type"] == "function_call_output"
        assert result[3]["call_id"] == "c2"  # placeholder
        assert result[4] == self._make_user()

    # --- Compact simulation (summary replaces old context) ---

    def test_compact_summary_with_live_zone(self):
        """After compaction: summary + live zone with tool calls."""
        items = [
            self._make_assistant("[Summary of previous context]"),
            self._make_user("continue the task"),
            self._make_call("c1", "bash"),
            self._make_output("c1", "done"),
            self._make_assistant("finished"),
        ]
        result = self._fix(items)
        assert result == items  # Already correct

    def test_compact_breaks_pair_live_zone_has_orphan_output(self):
        """Compact boundary falls between call and output — orphan in live zone."""
        # The assistant(tool_calls) was compacted away, only output remains
        items = [
            self._make_assistant("[Summary]"),
            self._make_output("c_old", "stale"),  # orphan
            self._make_user("next"),
            self._make_call("c1"),
            self._make_output("c1"),
        ]
        result = self._fix(items)
        # Orphan output dropped
        assert len(result) == 4
        assert result[0] == self._make_assistant("[Summary]")
        assert result[1] == self._make_user("next")
        assert result[2] == self._make_call("c1")
        assert result[3] == self._make_output("c1")

    # --- Invariant checks ---

    def test_invariant_every_call_followed_by_output(self):
        """For any input, the output must satisfy the Responses API invariant:
        every function_call is immediately followed by function_call_output."""
        # Worst case: interleaved calls and outputs from messy truncation
        items = [
            self._make_output("c3"),  # orphan
            self._make_call("c1"),
            self._make_user(),
            self._make_call("c2"),
            self._make_output("c1"),
            self._make_assistant(),
            self._make_call("c4"),  # missing output
        ]
        result = self._fix(items)

        # Verify invariant
        for i, item in enumerate(result):
            if item.get("type") == "function_call":
                next_item = result[i + 1]
                assert next_item["type"] == "function_call_output", (
                    f"function_call at index {i} (call_id={item['call_id']}) "
                    f"not followed by output, got {next_item.get('type')}"
                )
                assert next_item["call_id"] == item["call_id"], (
                    f"call_id mismatch at index {i}: "
                    f"call={item['call_id']}, output={next_item['call_id']}"
                )

    def test_invariant_no_orphan_outputs(self):
        """No function_call_output should exist without a preceding function_call."""
        items = [
            self._make_output("orphan1"),
            self._make_output("orphan2"),
            self._make_call("c1"),
            self._make_output("c1"),
            self._make_output("orphan3"),
        ]
        result = self._fix(items)
        for i, item in enumerate(result):
            if item.get("type") == "function_call_output":
                prev_item = result[i - 1]
                assert (
                    prev_item.get("type") == "function_call"
                ), f"Orphan output at index {i} (call_id={item['call_id']})"

    def test_empty_input(self):
        result = self._fix([])
        assert result == []

    def test_no_tool_calls(self):
        """Conversation without any tool use passes through unchanged."""
        items = [
            self._make_user("hello"),
            self._make_assistant("hi"),
            self._make_user("bye"),
        ]
        result = self._fix(items)
        assert result == items

    # --- End-to-end: _to_responses_input + _fix_tool_call_pairing ---

    def test_e2e_truncated_conversation(self):
        """Simulate _maybe_truncate removing old messages that break pairs."""
        # Full conversation (what it was before truncation):
        #   user, assistant(tc=[c1,c2]), tool(c1), tool(c2), user, assistant(tc=[c3]), tool(c3), assistant
        # After truncation (lost assistant with tool_calls for c1,c2):
        truncated_messages = [
            {"role": "tool", "tool_call_id": "c1", "content": "orphan1"},
            {"role": "tool", "tool_call_id": "c2", "content": "orphan2"},
            {"role": "user", "content": "next"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "c3", "function": {"name": "bash", "arguments": "{}"}}
                ],
            },
            {"role": "tool", "tool_call_id": "c3", "content": "result3"},
            {"role": "assistant", "content": "done"},
        ]
        api_input = CodexOAuthProvider._to_responses_input(truncated_messages)
        fixed = CodexOAuthProvider._fix_tool_call_pairing(api_input)

        # Orphans c1, c2 should be gone; c3 pair intact
        call_ids = [
            item["call_id"] for item in fixed if item.get("type") == "function_call"
        ]
        output_ids = [
            item["call_id"]
            for item in fixed
            if item.get("type") == "function_call_output"
        ]
        assert "c1" not in call_ids and "c1" not in output_ids
        assert "c2" not in call_ids and "c2" not in output_ids
        assert "c3" in call_ids and "c3" in output_ids

        # Verify ordering invariant
        for i, item in enumerate(fixed):
            if item.get("type") == "function_call":
                assert fixed[i + 1]["type"] == "function_call_output"
                assert fixed[i + 1]["call_id"] == item["call_id"]

    def test_e2e_compact_removes_old_tool_calls(self):
        """After compact, old tool calls are summarized away but live zone intact."""
        # Post-compact conversation: summary + live zone
        post_compact_messages = [
            {"role": "assistant", "content": "[Summary: used bash and read tools]"},
            {"role": "user", "content": "now run tests"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c_new",
                        "function": {
                            "name": "bash",
                            "arguments": '{"command":"pytest"}',
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "c_new", "content": "3 passed"},
            {"role": "assistant", "content": "All tests pass."},
        ]
        api_input = CodexOAuthProvider._to_responses_input(post_compact_messages)
        fixed = CodexOAuthProvider._fix_tool_call_pairing(api_input)

        # Should be clean — no orphans, pair intact
        calls = [i for i in fixed if i.get("type") == "function_call"]
        outputs = [i for i in fixed if i.get("type") == "function_call_output"]
        assert len(calls) == 1 and calls[0]["call_id"] == "c_new"
        assert len(outputs) == 1 and outputs[0]["call_id"] == "c_new"


class TestConstants:
    """Verify critical constants are correct."""

    def test_codex_endpoint(self):
        assert CODEX_BASE_URL == "https://chatgpt.com/backend-api/codex"

    def test_default_token_path(self):
        assert (
            DEFAULT_TOKEN_PATH == Path.home() / ".kohakuterrarium" / "codex-auth.json"
        )

    def test_codex_cli_token_path(self):
        assert CODEX_CLI_TOKEN_PATH == Path.home() / ".codex" / "auth.json"

    def test_client_id(self):
        assert CLIENT_ID == "app_EMoamEEZ73f0CkXaXp7hrann"
