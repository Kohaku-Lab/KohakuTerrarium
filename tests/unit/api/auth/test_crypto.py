"""Unit tests for :mod:`api.auth.crypto`."""

import re

from kohakuterrarium.api.auth.crypto import (
    generate_session_id,
    generate_token,
    hash_invitation_token,
    hash_password,
    hash_token,
    verify_password,
)


class TestPasswordHash:
    def test_round_trip(self):
        h = hash_password("hunter2", rounds=4)  # low cost for fast tests
        assert verify_password("hunter2", h) is True
        assert verify_password("nope", h) is False

    def test_two_hashes_of_same_password_differ(self):
        # bcrypt embeds salt → two hashes never collide.
        a = hash_password("same", rounds=4)
        b = hash_password("same", rounds=4)
        assert a != b
        assert verify_password("same", a)
        assert verify_password("same", b)

    def test_verify_handles_garbage_hash_safely(self):
        # Wrong shape → False, not exception.
        assert verify_password("anything", "not-a-bcrypt-hash") is False

    def test_verify_handles_empty_hash(self):
        assert verify_password("x", "") is False

    def test_rounds_is_honoured(self):
        # The bcrypt hash carries the cost factor in its prefix.
        # ``$2b$04$...`` for rounds=4.
        h = hash_password("x", rounds=4)
        assert h.startswith(("$2b$04$", "$2a$04$", "$2y$04$"))


class TestTokenGeneration:
    def test_generate_token_is_hex_64(self):
        tok = generate_token()
        assert re.match(r"^[0-9a-f]{64}$", tok)

    def test_tokens_are_unique(self):
        # CSPRNG — even 100 samples should never collide.
        samples = {generate_token() for _ in range(100)}
        assert len(samples) == 100

    def test_session_id_is_urlsafe(self):
        sid = generate_session_id()
        # URL-safe base64 alphabet: letters, digits, -, _.
        assert re.match(r"^[A-Za-z0-9_-]+$", sid)
        # 32 bytes → 43 chars of base64url (no padding).
        assert len(sid) == 43

    def test_session_ids_are_unique(self):
        samples = {generate_session_id() for _ in range(100)}
        assert len(samples) == 100


class TestTokenHash:
    def test_deterministic(self):
        a = hash_token("foo")
        b = hash_token("foo")
        assert a == b

    def test_sha3_512_hex_length(self):
        # SHA3-512 → 64 bytes → 128 hex chars.
        h = hash_token("anything")
        assert len(h) == 128
        assert re.match(r"^[0-9a-f]{128}$", h)

    def test_different_inputs_different_hashes(self):
        assert hash_token("a") != hash_token("b")

    def test_invitation_token_hash_uses_same_function(self):
        # Alias — they MUST collide so the lookup column matches.
        x = "invitation-test"
        assert hash_invitation_token(x) == hash_token(x)
