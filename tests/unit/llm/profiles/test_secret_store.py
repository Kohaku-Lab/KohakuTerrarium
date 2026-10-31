"""API keys in the operating-system keychain instead of the key file."""

import pytest

keyring = pytest.importorskip("keyring")
from keyring.backend import KeyringBackend  # noqa: E402
from keyring.backends import fail  # noqa: E402
from keyring.errors import PasswordDeleteError  # noqa: E402

from kohakuterrarium.llm.profiles import api_keys, secret_store  # noqa: E402


class MemoryKeyring(KeyringBackend):
    """A keychain held in memory."""

    priority = 5

    def __init__(self):
        super().__init__()
        self.items: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self.items.get((service, username))

    def set_password(self, service, username, password):
        self.items[(service, username)] = password

    def delete_password(self, service, username):
        if (service, username) not in self.items:
            raise PasswordDeleteError("missing")
        del self.items[(service, username)]


class CorruptingKeyring(MemoryKeyring):
    """A keychain that stores something other than what it was given."""

    def set_password(self, service, username, password):
        super().set_password(service, username, password + "!")


@pytest.fixture
def vault():
    previous = keyring.get_keyring()
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    yield backend
    keyring.set_keyring(previous)


def _file_text():
    path = api_keys._keys_path()
    return path.read_text(encoding="utf-8") if path.exists() else ""


class TestFileStoreIsTheDefault:
    def test_keys_stay_in_the_file_and_the_keychain_is_untouched(self, vault):
        api_keys.save_api_key("openai", "sk-file-secret")

        assert "sk-file-secret" in _file_text()
        assert vault.items == {}
        assert secret_store.configured_mode() == secret_store.MODE_FILE
        assert not secret_store.active()
        assert api_keys.get_api_key("openai") == "sk-file-secret"


class TestSwitchingToTheKeychain:
    def test_existing_keys_move_and_leave_no_plaintext(self, vault):
        api_keys.save_api_key("openai", "sk-openai-secret")
        api_keys.save_api_key("anthropic", "sk-anthropic-secret")
        api_keys.save_api_key("gemini", "")

        moved = api_keys.switch_secret_store(secret_store.MODE_KEYCHAIN)

        assert moved == 2
        assert vault.items[("KohakuTerrarium", "openai")] == "sk-openai-secret"
        text = _file_text()
        assert "sk-openai-secret" not in text and "sk-anthropic-secret" not in text
        assert secret_store.active()
        assert sorted(secret_store.stored_providers()) == ["anthropic", "openai"]
        assert api_keys.get_api_key("openai") == "sk-openai-secret"
        assert api_keys.has_api_key("anthropic")
        assert api_keys.list_api_keys()["openai"] == "sk-o...cret"

    def test_new_and_cleared_keys_follow_the_keychain(self, vault):
        api_keys.switch_secret_store(secret_store.MODE_KEYCHAIN)

        api_keys.save_api_key("openai", "sk-new-secret")
        assert vault.items[("KohakuTerrarium", "openai")] == "sk-new-secret"
        assert "sk-new-secret" not in _file_text()

        api_keys.save_api_key("openai", "")
        assert ("KohakuTerrarium", "openai") not in vault.items
        assert not api_keys.has_api_key("openai")
        assert secret_store.stored_providers() == []

    def test_a_leftover_plaintext_copy_is_removed_when_the_key_is_saved_again(
        self, vault
    ):
        api_keys.save_api_key("openai", "sk-old")
        api_keys.switch_secret_store(secret_store.MODE_KEYCHAIN)
        api_keys._write_file_keys({"openai": "sk-stale"})

        api_keys.save_api_key("openai", "sk-fresh")

        assert "sk-stale" not in _file_text()
        assert api_keys.get_api_key("openai") == "sk-fresh"

    def test_a_keychain_that_stores_the_wrong_value_changes_nothing(self):
        previous = keyring.get_keyring()
        keyring.set_keyring(CorruptingKeyring())
        try:
            api_keys.save_api_key("openai", "sk-keep-me")

            with pytest.raises(secret_store.SecretStoreError, match="did not store"):
                api_keys.switch_secret_store(secret_store.MODE_KEYCHAIN)

            assert "sk-keep-me" in _file_text()
            assert secret_store.configured_mode() == secret_store.MODE_FILE
            assert api_keys.get_api_key("openai") == "sk-keep-me"
        finally:
            keyring.set_keyring(previous)

    def test_no_keychain_means_no_switch(self):
        previous = keyring.get_keyring()
        keyring.set_keyring(fail.Keyring())
        try:
            api_keys.save_api_key("openai", "sk-keep-me")
            assert not secret_store.keychain_available()
            assert secret_store.backend_name() == ""

            with pytest.raises(
                secret_store.SecretStoreError, match="no system keychain"
            ):
                api_keys.switch_secret_store(secret_store.MODE_KEYCHAIN)

            assert "sk-keep-me" in _file_text()
        finally:
            keyring.set_keyring(previous)


class TestSwitchingBack:
    def test_keys_return_to_the_file_and_leave_the_keychain(self, vault):
        api_keys.save_api_key("openai", "sk-openai-secret")
        api_keys.switch_secret_store(secret_store.MODE_KEYCHAIN)

        moved = api_keys.switch_secret_store(secret_store.MODE_FILE)

        assert moved == 1
        assert "sk-openai-secret" in _file_text()
        assert vault.items == {}
        assert secret_store.stored_providers() == []
        assert not secret_store.active()
        assert api_keys.get_api_key("openai") == "sk-openai-secret"

    def test_an_unknown_store_is_refused(self, vault):
        with pytest.raises(secret_store.SecretStoreError, match="unknown"):
            api_keys.switch_secret_store("vault-of-doom")
        with pytest.raises(secret_store.SecretStoreError, match="unknown"):
            secret_store.set_configured_mode("vault-of-doom")


class TestWhenTheKeychainDisappears:
    def test_the_key_file_and_environment_take_over_without_error(
        self, vault, monkeypatch
    ):
        api_keys.save_api_key("openai", "sk-in-keychain")
        api_keys.switch_secret_store(secret_store.MODE_KEYCHAIN)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
        previous = keyring.get_keyring()
        keyring.set_keyring(fail.Keyring())
        try:
            assert not secret_store.active()
            assert api_keys.get_api_key("openai") == "sk-from-env"
            api_keys.save_api_key("gemini", "sk-new")
            assert "sk-new" in _file_text()
        finally:
            keyring.set_keyring(previous)


class TestModeSelection:
    def test_the_environment_overrides_the_saved_choice(self, vault, monkeypatch):
        monkeypatch.setenv(secret_store.MODE_ENV, "keychain")
        assert secret_store.configured_mode() == secret_store.MODE_KEYCHAIN
        monkeypatch.setenv(secret_store.MODE_ENV, "nonsense")
        assert secret_store.configured_mode() == secret_store.MODE_FILE

    def test_a_corrupt_settings_file_means_the_key_file(self, vault):
        path = secret_store._settings_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        assert secret_store.configured_mode() == secret_store.MODE_FILE
        path.write_text("[1, 2]", encoding="utf-8")
        assert secret_store.configured_mode() == secret_store.MODE_FILE

    def test_the_backend_is_named_when_one_is_available(self, vault):
        assert secret_store.keychain_available()
        assert secret_store.backend_name() == "MemoryKeyring"

    def test_reading_a_missing_or_failing_key_gives_nothing(self, vault, monkeypatch):
        assert secret_store.get("nothing") == ""
        monkeypatch.setattr(
            vault,
            "get_password",
            lambda *_: (_ for _ in ()).throw(RuntimeError("locked")),
        )
        assert secret_store.get("openai") == ""
