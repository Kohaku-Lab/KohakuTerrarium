"""Unit tests for kohakuterrarium.browser.secrets."""

import pytest

from kohakuterrarium.browser.secrets import (
    MIN_REDACT_LENGTH,
    SecretStore,
    parse_reference,
    value_forms,
)
from kohakuterrarium.errors import ConfigError, NotFoundError


@pytest.fixture
def env(monkeypatch):
    for name in ("KT_T_PW", "KT_T_USER", "KT_T_SHORT", "KT_T_EMPTY", "KT_T_LONG"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_parse_reference():
    assert parse_reference("env:BANK_PW") == "BANK_PW"
    assert parse_reference(" env:_X1 ") == "_X1"
    for bad in ("BANK_PW", "env:", "env:1ABC", "env:A-B", "file:/x", "ENV:X", 3, None):
        with pytest.raises(ConfigError):
            parse_reference(bad)


def test_construction_rejects_bad_references_and_names():
    with pytest.raises(ConfigError) as info:
        SecretStore({"pw": "literal-password"})
    assert "pw" in str(info.value)
    assert "literal-password" not in str(info.value)
    with pytest.raises(ConfigError):
        SecretStore({"bad name": "env:X"})
    with pytest.raises(ConfigError):
        SecretStore({"": "env:X"})


def test_resolve_reads_environment_at_call_time(env):
    store = SecretStore({"pw": "env:KT_T_PW"})
    env.setenv("KT_T_PW", "first-value")
    assert store.resolve("pw") == "first-value"
    env.setenv("KT_T_PW", "second-value")
    assert store.resolve("pw") == "second-value"
    assert store.names == ("pw",)
    assert "pw" in store and "nope" not in store


def test_resolve_unknown_name(env):
    store = SecretStore({"pw": "env:KT_T_PW"})
    with pytest.raises(NotFoundError) as info:
        store.resolve("other")
    assert "other" in str(info.value)


def test_resolve_unset_or_empty_variable_names_secret_not_value(env):
    store = SecretStore({"pw": "env:KT_T_PW", "empty": "env:KT_T_EMPTY"})
    with pytest.raises(ConfigError) as info:
        store.resolve("pw")
    assert "pw" in str(info.value) and "KT_T_PW" in str(info.value)
    env.setenv("KT_T_EMPTY", "")
    with pytest.raises(ConfigError):
        store.resolve("empty")


def test_resolve_refuses_values_too_short_to_redact(env):
    env.setenv("KT_T_SHORT", "a1b")
    env.setenv("KT_T_PW", "a1b2")
    store = SecretStore({"pin": "env:KT_T_SHORT", "pw": "env:KT_T_PW"})
    with pytest.raises(ConfigError) as info:
        store.resolve("pin")
    message = str(info.value)
    assert "pin" in message and str(MIN_REDACT_LENGTH) in message
    assert "a1b" not in message
    assert store.resolve("pw") == "a1b2"


def test_value_forms_lists_raw_and_url_encoded_spellings():
    assert value_forms("p@ss word") == ("p@ss word", "p%40ss%20word", "p%40ss+word")
    assert value_forms("a/b") == ("a/b", "a%2Fb")
    # a value with nothing to encode is registered once
    assert value_forms("hunter22") == ("hunter22",)


def test_redact_covers_url_encoded_forms(env):
    env.setenv("KT_T_PW", "p@ss word")
    store = SecretStore({"pw": "env:KT_T_PW"})
    text = "GET /login?u=a&p=p%40ss%20word and p%40ss+word and p@ss word"
    assert store.redact(text) == (
        "GET /login?u=a&p=[secret:pw] and [secret:pw] and [secret:pw]"
    )
    assert store.redact("typed q&a=1 / q%26a%3D1", extra=["q&a=1"]) == (
        "typed [redacted] / [redacted]"
    )


def test_store_is_independent_of_the_source_mapping(env):
    refs = {"pw": "env:KT_T_PW"}
    store = SecretStore(refs)
    refs["user"] = "env:KT_T_USER"
    assert store.names == ("pw",)


def test_redact_replaces_every_occurrence(env):
    env.setenv("KT_T_PW", "hunter22")
    env.setenv("KT_T_USER", "alice@example.com")
    store = SecretStore({"pw": "env:KT_T_PW", "user": "env:KT_T_USER"})
    text = "user=alice@example.com pw=hunter22 again hunter22"
    assert store.redact(text) == ("user=[secret:user] pw=[secret:pw] again [secret:pw]")


def test_redact_prefers_longer_values(env):
    env.setenv("KT_T_PW", "pass")
    env.setenv("KT_T_LONG", "password123")
    store = SecretStore({"pw": "env:KT_T_PW", "long": "env:KT_T_LONG"})
    assert store.redact("password123 / pass") == "[secret:long] / [secret:pw]"


def test_redact_does_not_reprocess_its_own_markers(env):
    env.setenv("KT_T_PW", "secret")
    store = SecretStore({"secret": "env:KT_T_PW"})
    assert store.redact("a secret b") == "a [secret:secret] b"


def test_redact_skips_short_and_unresolvable_values(env):
    short = "x" * (MIN_REDACT_LENGTH - 1)
    exact = "y" * MIN_REDACT_LENGTH
    env.setenv("KT_T_SHORT", short)
    env.setenv("KT_T_USER", exact)
    store = SecretStore(
        {"short": "env:KT_T_SHORT", "user": "env:KT_T_USER", "pw": "env:KT_T_PW"}
    )
    text = f"{short} {exact}"
    assert store.redact(text) == f"{short} [secret:user]"


def test_redact_reflects_environment_changes(env):
    store = SecretStore({"pw": "env:KT_T_PW"})
    assert store.redact("old-value new-value") == "old-value new-value"
    env.setenv("KT_T_PW", "old-value")
    assert store.redact("old-value new-value") == "[secret:pw] new-value"
    env.setenv("KT_T_PW", "new-value")
    assert store.redact("old-value new-value") == "old-value [secret:pw]"


def test_redact_extra_values(env):
    env.setenv("KT_T_PW", "hunter22")
    store = SecretStore({"pw": "env:KT_T_PW"})
    text = "typed s3cret! and hunter22 and ab and hunter22x"
    out = store.redact(text, extra=["s3cret!", "ab", "", "hunter22"])
    assert out == "typed [redacted] and [secret:pw] and ab and [secret:pw]x"


def test_redact_handles_regex_metacharacters(env):
    env.setenv("KT_T_PW", "a.b*c(d)")
    store = SecretStore({"pw": "env:KT_T_PW"})
    assert store.redact("axbxc a.b*c(d)") == "axbxc [secret:pw]"


def test_redact_with_nothing_to_redact():
    store = SecretStore({})
    assert store.redact("plain text") == "plain text"
    assert store.redact("") == ""
    assert store.names == ()
