import ctypes
import socket
from types import SimpleNamespace

import pytest

from kohakuterrarium.llm.antigravity import store


@pytest.fixture
def service(monkeypatch):
    state = SimpleNamespace(
        raw=b"test-secret",
        count=1,
        locked=False,
        item_locked=False,
        missing=False,
        alias_missing=False,
        failure=None,
        reads=0,
        closes=0,
        paths=[],
        aliases=[],
        queries=[],
    )

    class StorageError(Exception):
        pass

    class Missing(StorageError):
        pass

    class Locked(StorageError):
        pass

    class Connection:
        def close(self):
            state.closes += 1

    class Item:
        def is_locked(self):
            return state.item_locked

        def get_secret(self):
            state.reads += 1
            return state.raw

    class Collection:
        def is_locked(self):
            return state.locked

        def search_items(self, attributes):
            state.queries.append(attributes)
            return [Item() for _ in range(state.count)]

    def collection(connection, path):
        state.paths.append(path)
        if state.failure:
            raise state.failure
        if state.missing:
            raise Missing
        return Collection()

    def alias(connection, name):
        state.aliases.append(name)
        if state.alias_missing:
            raise Missing
        return Collection()

    monkeypatch.setattr(store, "_connect_linux", Connection)
    monkeypatch.setattr(
        store,
        "secretstorage",
        SimpleNamespace(
            Collection=collection,
            get_collection_by_alias=alias,
            ItemNotFoundException=Missing,
            LockedException=Locked,
            SecretStorageException=StorageError,
        ),
    )
    monkeypatch.setattr(store, "DBusErrorResponse", StorageError, raising=False)
    monkeypatch.setattr(store.sys, "platform", "linux")
    return state


def test_exact_lookup_and_alias_fallback(service):
    assert store.read_keyring() == ("test-secret", "linux_secret_service")
    assert service.paths == ["/org/freedesktop/secrets/collection/login"]
    assert service.queries == [{"service": "gemini", "username": "antigravity"}]
    assert service.aliases == []
    assert service.reads == service.closes == 1
    service.count = 0
    assert store.read_keyring() == (None, "linux_secret_service")
    assert service.aliases == []
    service.missing = True
    service.count = 1
    assert store.read_keyring()[0] == "test-secret"
    assert service.aliases == ["default"]
    service.alias_missing = True
    assert store.read_keyring()[0] is None
    assert service.closes == 4


@pytest.mark.parametrize(
    "field,value,error",
    [
        ("locked", True, "credential_store_locked"),
        ("item_locked", True, "credential_store_locked"),
        ("count", 2, "ambiguous_sources"),
        ("raw", b"\xff", "malformed_credential"),
        ("raw", b"x" * 65537, "malformed_credential"),
        ("failure", TimeoutError("secret-canary"), "credential_store_unavailable"),
        ("failure", OSError("secret-canary"), "credential_store_unavailable"),
        ("failure", KeyError("secret-canary"), "credential_store_unavailable"),
    ],
)
def test_fail_closed_and_redacted(service, field, value, error):
    setattr(service, field, value)
    with pytest.raises(store.AntigravityError, match=error) as caught:
        store.read_keyring()
    assert "secret-canary" not in str(caught.value)
    assert service.closes == 1
    assert service.reads == (1 if field == "raw" else 0)


def test_missing_dependency_and_unsupported_platform(monkeypatch):
    monkeypatch.setattr(store, "secretstorage", None)
    monkeypatch.setattr(store.sys, "platform", "linux")
    with pytest.raises(store.AntigravityError, match="credential_store_unavailable"):
        store.read_keyring()
    monkeypatch.setattr(store.sys, "platform", "darwin")
    with pytest.raises(store.AntigravityError, match="unsupported_platform"):
        store.read_keyring()


def test_deadline_applies_to_hello_and_subsequent_replies(monkeypatch):
    pytest.importorskip("secretstorage")
    blocking = pytest.importorskip("jeepney.io.blocking")
    clock, limits, replies = [100.0], [], []
    monkeypatch.setattr(store.time, "monotonic", lambda: clock[0])

    def initialize(self, sock):
        self.sock = sock
        self.send_and_get_reply("Hello")

    def send(self, message, *, timeout=None):
        replies.append((message, timeout))
        return "reply"

    monkeypatch.setattr(blocking.DBusConnection, "__init__", initialize)
    monkeypatch.setattr(blocking.DBusConnection, "send_and_get_reply", send)
    connection = store._DeadlineConnection(
        SimpleNamespace(settimeout=limits.append), 105
    )
    clock[0] = 102
    assert connection.send_and_get_reply("SearchItems", timeout=1) == "reply"
    assert replies == [("Hello", 5), ("SearchItems", 1)]
    assert limits == [5, 3]
    clock[0] = 105
    with pytest.raises(TimeoutError):
        connection.send_and_get_reply("GetSecret")
    assert len(replies) == 2


def test_failed_hello_closes_socket_and_selector(monkeypatch):
    pytest.importorskip("secretstorage")
    blocking = pytest.importorskip("jeepney.io.blocking")
    local, peer = socket.socketpair()
    connections = []

    def reject(self, *args, **kwargs):
        connections.append(self)
        raise TimeoutError

    monkeypatch.setattr(blocking.DBusConnection, "send_and_get_reply", reject)
    monkeypatch.setattr(store, "get_bus", lambda bus: "test-bus")
    monkeypatch.setattr(store, "prep_socket", lambda address, timeout: local)
    try:
        with pytest.raises(TimeoutError):
            store._connect_linux()
        assert local.fileno() == -1
        assert connections[0].selector.get_map() is None
    finally:
        local.close()
        peer.close()


@pytest.mark.parametrize(
    "raw,error,expected",
    [
        (b"windows-secret", None, "windows-secret"),
        (b"\xff", None, "malformed_credential"),
        (b"x" * 65537, None, "malformed_credential"),
        (b"", 1168, None),
        (b"", 5, "credential_store_unavailable"),
    ],
)
def test_windows_target_and_cleanup(monkeypatch, raw, error, expected):
    blob = ctypes.create_string_buffer(raw)
    credential = store._WindowsCredential()
    credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_byte))
    credential.CredentialBlobSize = len(raw)
    calls, freed = [], []

    def read(target, kind, flags, output):
        calls.append((target, kind, flags))
        ctypes.cast(output, ctypes.POINTER(ctypes.POINTER(store._WindowsCredential)))[
            0
        ] = ctypes.pointer(credential)
        return error is None

    library = SimpleNamespace(
        CredReadW=read, CredFree=lambda pointer: freed.append(True)
    )
    monkeypatch.setattr(store.sys, "platform", "win32")
    monkeypatch.setattr(store.ctypes, "WinDLL", lambda *a, **k: library, raising=False)
    monkeypatch.setattr(store.ctypes, "get_last_error", lambda: error, raising=False)
    if expected in ("malformed_credential", "credential_store_unavailable"):
        with pytest.raises(store.AntigravityError, match=expected):
            store.read_keyring()
    else:
        assert store.read_keyring() == (expected, "windows_keyring")
    assert calls == [("gemini:antigravity", 1, 0)]
    assert freed == ([True] if error is None else [])
