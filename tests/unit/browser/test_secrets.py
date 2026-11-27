"""Unit tests for kohakuterrarium.browser.secrets."""

import pytest

from kohakuterrarium.browser.secrets import (
    MIN_REDACT_LENGTH,
    SecretStore,
    parse_origin,
    value_forms,
    web_origin,
)
from kohakuterrarium.errors import ConfigError, NotFoundError

SHOP = "https://shop.test"


def spec(variable: str, *origins: str) -> dict:
    """One secret's configuration: its environment variable and origins."""
    return {"env": variable, "origins": list(origins or (SHOP,))}


@pytest.fixture
def env(monkeypatch):
    for name in ("KT_T_PW", "KT_T_USER", "KT_T_SHORT", "KT_T_EMPTY", "KT_T_LONG"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def test_parse_origin_normalises_scheme_host_and_port():
    assert parse_origin("https://Shop.Test") == "https://shop.test"
    assert parse_origin(" https://shop.test/ ") == "https://shop.test"
    assert parse_origin("https://shop.test:443") == "https://shop.test"
    assert parse_origin("http://shop.test:80") == "http://shop.test"
    assert parse_origin("http://127.0.0.1:8080") == "http://127.0.0.1:8080"
    assert parse_origin("https://Bücher.example") == "https://xn--bcher-kva.example"
    assert parse_origin("http://[::1]:3000") == "http://[::1]:3000"


@pytest.mark.parametrize(
    "bad",
    [
        "shop.test",
        "ftp://shop.test",
        "https://",
        "https://shop.test/login",
        "https://shop.test/?a=1",
        "https://shop.test#x",
        "https://user:pw@shop.test",
        "https://*.shop.test",
        "*",
        "https://shop.test:99999",
        "https://shop.test:x",
        "https://bad host.test",
        "",
        3,
        None,
    ],
)
def test_parse_origin_rejects_anything_but_an_origin(bad):
    with pytest.raises(ConfigError):
        parse_origin(bad)


def test_web_origin_of_page_urls():
    assert web_origin("https://Shop.test:443/a/b?c#d") == "https://shop.test"
    assert web_origin("http://u:p@127.0.0.1:8080/x") == "http://127.0.0.1:8080"
    for url in ("about:blank", "about:srcdoc", "data:text/html,x", "blob:x", "", "::"):
        assert web_origin(url) is None, url


def test_construction_rejects_bad_specs_and_names():
    for bad in (
        "env:PW",
        {"env": "PW"},
        {"env": "PW", "origins": []},
        {"env": "PW", "origins": "https://shop.test"},
        {"env": "PW", "origins": ["https://shop.test/login"]},
        {"env": "PW", "origins": ["https://*.shop.test"]},
        {"env": "env:PW", "origins": [SHOP]},
        {"env": "1PW", "origins": [SHOP]},
        {"origins": [SHOP]},
        {"env": "PW", "origins": [SHOP], "extra": 1},
    ):
        with pytest.raises(ConfigError) as info:
            SecretStore({"pw": bad})
        assert "'pw'" in str(info.value), (bad, str(info.value))
    with pytest.raises(ConfigError):
        SecretStore({"bad name": spec("X")})
    with pytest.raises(ConfigError):
        SecretStore({"": spec("X")})


def test_origins_are_normalised_and_judged(env):
    store = SecretStore(
        {"pw": spec("KT_T_PW", "https://Shop.test:443/", "http://localhost:3000")}
    )
    assert store.origins("pw") == ("https://shop.test", "http://localhost:3000")
    assert store.allows("pw", "https://shop.test")
    assert store.allows("pw", "http://localhost:3000")
    for other in ("http://shop.test", "https://evil.test", "http://localhost", None):
        assert not store.allows("pw", other), other
    assert not store.allows("nope", "https://shop.test")


def test_resolve_reads_environment_at_call_time(env):
    store = SecretStore({"pw": spec("KT_T_PW")})
    env.setenv("KT_T_PW", "first-value")
    assert store.resolve("pw") == "first-value"
    env.setenv("KT_T_PW", "second-value")
    assert store.resolve("pw") == "second-value"
    assert store.names == ("pw",)
    assert "pw" in store and "nope" not in store


def test_resolve_unknown_name(env):
    store = SecretStore({"pw": spec("KT_T_PW")})
    with pytest.raises(NotFoundError) as info:
        store.resolve("other")
    assert "other" in str(info.value)


def test_resolve_unset_or_empty_variable_names_secret_not_value(env):
    store = SecretStore({"pw": spec("KT_T_PW"), "empty": spec("KT_T_EMPTY")})
    with pytest.raises(ConfigError) as info:
        store.resolve("pw")
    assert "pw" in str(info.value) and "KT_T_PW" in str(info.value)
    env.setenv("KT_T_EMPTY", "")
    with pytest.raises(ConfigError):
        store.resolve("empty")


def test_resolve_refuses_values_too_short_to_redact(env):
    env.setenv("KT_T_SHORT", "a1b")
    env.setenv("KT_T_PW", "a1b2")
    store = SecretStore({"pin": spec("KT_T_SHORT"), "pw": spec("KT_T_PW")})
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


def test_value_forms_cover_json_and_yaml_quoting():
    forms = value_forms('Pa"ss\\word-42')
    assert 'Pa\\"ss\\\\word-42' in forms
    assert "it''s-secret" in value_forms("it's-secret")


def test_redact_covers_escaped_spellings_of_secrets_and_extras(env):
    env.setenv("KT_T_PW", 'Pa"ss\\word-42')
    store = SecretStore({"pw": spec("KT_T_PW")})
    text = 'note: "Pa\\"ss\\\\word-42: x" and {"v": "Pa\\"ss\\\\word-42"}'
    assert store.redact(text) == 'note: "[secret:pw]: x" and {"v": "[secret:pw]"}'
    extra = 'say "a\\\\b\\"c-pw"'
    assert store.redact(extra, extra=['a\\b"c-pw']) == 'say "[redacted]"'


def test_redact_covers_url_encoded_forms(env):
    env.setenv("KT_T_PW", "p@ss word")
    store = SecretStore({"pw": spec("KT_T_PW")})
    text = "GET /login?u=a&p=p%40ss%20word and p%40ss+word and p@ss word"
    assert store.redact(text) == (
        "GET /login?u=a&p=[secret:pw] and [secret:pw] and [secret:pw]"
    )
    assert store.redact("typed q&a=1 / q%26a%3D1", extra=["q&a=1"]) == (
        "typed [redacted] / [redacted]"
    )


def test_store_is_independent_of_the_source_mapping(env):
    refs = {"pw": spec("KT_T_PW")}
    store = SecretStore(refs)
    refs["user"] = spec("KT_T_USER")
    assert store.names == ("pw",)


def test_redact_replaces_every_occurrence(env):
    env.setenv("KT_T_PW", "hunter22")
    env.setenv("KT_T_USER", "alice@example.com")
    store = SecretStore({"pw": spec("KT_T_PW"), "user": spec("KT_T_USER")})
    text = "user=alice@example.com pw=hunter22 again hunter22"
    assert store.redact(text) == ("user=[secret:user] pw=[secret:pw] again [secret:pw]")


def test_redact_prefers_longer_values(env):
    env.setenv("KT_T_PW", "pass")
    env.setenv("KT_T_LONG", "password123")
    store = SecretStore({"pw": spec("KT_T_PW"), "long": spec("KT_T_LONG")})
    assert store.redact("password123 / pass") == "[secret:long] / [secret:pw]"


def test_redact_does_not_reprocess_its_own_markers(env):
    env.setenv("KT_T_PW", "secret")
    store = SecretStore({"secret": spec("KT_T_PW")})
    assert store.redact("a secret b") == "a [secret:secret] b"


def test_redact_skips_short_and_unresolvable_values(env):
    short = "x" * (MIN_REDACT_LENGTH - 1)
    exact = "y" * MIN_REDACT_LENGTH
    env.setenv("KT_T_SHORT", short)
    env.setenv("KT_T_USER", exact)
    store = SecretStore(
        {"short": spec("KT_T_SHORT"), "user": spec("KT_T_USER"), "pw": spec("KT_T_PW")}
    )
    text = f"{short} {exact}"
    assert store.redact(text) == f"{short} [secret:user]"


def test_redact_reflects_environment_changes(env):
    store = SecretStore({"pw": spec("KT_T_PW")})
    assert store.redact("old-value new-value") == "old-value new-value"
    env.setenv("KT_T_PW", "old-value")
    assert store.redact("old-value new-value") == "[secret:pw] new-value"
    env.setenv("KT_T_PW", "new-value")
    assert store.redact("old-value new-value") == "old-value [secret:pw]"


def test_redact_extra_values(env):
    env.setenv("KT_T_PW", "hunter22")
    store = SecretStore({"pw": spec("KT_T_PW")})
    text = "typed s3cret! and hunter22 and ab and hunter22x"
    out = store.redact(text, extra=["s3cret!", "ab", "", "hunter22"])
    assert out == "typed [redacted] and [secret:pw] and ab and [secret:pw]x"


def test_redact_handles_regex_metacharacters(env):
    env.setenv("KT_T_PW", "a.b*c(d)")
    store = SecretStore({"pw": spec("KT_T_PW")})
    assert store.redact("axbxc a.b*c(d)") == "axbxc [secret:pw]"


def test_redact_with_nothing_to_redact():
    store = SecretStore({})
    assert store.redact("plain text") == "plain text"
    assert store.redact("") == ""
    assert store.names == ()
