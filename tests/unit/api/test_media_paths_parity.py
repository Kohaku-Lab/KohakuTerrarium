"""Parity: the Host's ``mediaPaths.cjs`` accept/reject set matches the backend.

This drives the REAL FastAPI artifact route and raw-file route over a REAL
uvicorn server on an ephemeral loopback port — not ``TestClient`` — because the
httpx ASGI transport applies one extra percent decode before routing, so it is
*not* faithful to the ASGI server the Host actually talks to. uvicorn decodes
the request path once, and the artifact handler ``unquote``s the filepath a
second time; a filepath therefore survives exactly two decode levels.

The table below is the evidence the Host rules are written against: every name
the backend serves (a literal ``%`` the backend emits as ``%25``, ``#``,
``?``, unicode, and nested percent-looking names) plus the traversal forms the
backend itself refuses.
"""

import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from kohakuterrarium.api.app import kt_error_handler
from kohakuterrarium.api.routes.attach import files as attach_files_mod
from kohakuterrarium.api.routes.persistence import artifacts as art_mod
from kohakuterrarium.errors import KTError

# Host-unchanged names -> the wire form the backend's ``quote(..., safe='/')``
# emits, and the file the backend resolves it to.
ACCEPTED = [
    ("plain", "img.png", "img.png"),
    ("space", "a%20b.png", "a b.png"),
    ("literal-percent", "a%25b.png", "a%b.png"),
    ("trailing-literal-percent", "100%25.png", "100%.png"),
    ("hash", "a%23b.png", "a#b.png"),
    ("unicode", "%C3%A9.png", "é.png"),
    # ``a%20b.png`` on disk, reached through a twice-encoded wire form.
    ("nested-percent-looking", "a%252520b.png", "a%20b.png"),
    # A ``%2f`` decodes to a real subpath separator after two decodes.
    ("encoded-slash-subpath", "a%252fb.png", "a/b.png"),
    # ``%25252e%25252e%25252f`` decays to a literal ``%2e%2e%2f`` NAME, never ``..``.
    (
        "triple-encoded-literal",
        "%25252e%25252e%25252fsecret.png",
        "%2e%2e%2fsecret.png",
    ),
]

# Wire forms the backend refuses as traversal (must never serve the sibling file).
REJECTED = [
    "..%2fsecret.png",
    "%2e%2e%2fsecret.png",
    "%252e%252e%252fsecret.png",
]


def _artifacts(tmp: Path) -> None:
    root = tmp / "graph_1.artifacts"
    root.mkdir()
    for name in [
        "img.png",
        "a b.png",
        "a%b.png",
        "100%.png",
        "a#b.png",
        "é.png",
        "a%20b.png",
        "%2e%2e%2fsecret.png",
    ]:
        (root / name).write_bytes(f"DATA:{name}".encode())
    (root / "a").mkdir()
    (root / "a" / "b.png").write_bytes(b"DATA:a/b.png")
    # The sibling the traversal probes try to escape to, one level above artifacts.
    (tmp / "secret.png").write_bytes(b"SECRET")
    if sys.platform != "win32":
        (root / "a?b.png").write_bytes(b"DATA:a?b.png")


@pytest.fixture(scope="module")
def backend(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("media-parity")
    _artifacts(tmp)

    app = FastAPI()
    app.include_router(art_mod.router, prefix="/api/sessions")
    app.include_router(attach_files_mod.router, prefix="/api/files")
    app.add_exception_handler(KTError, kt_error_handler)

    seen: list[str] = []
    original_session_dir = art_mod.persistence_store._session_dir
    original_resolve = art_mod.resolve_artifact_file

    def spy(artifacts, filepath):
        seen.append(filepath)
        return original_resolve(artifacts, filepath)

    art_mod.persistence_store._session_dir = lambda: tmp
    art_mod.resolve_artifact_file = spy

    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 20
    while not server.started:
        if time.time() > deadline:
            raise RuntimeError("uvicorn did not start")
        time.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}", tmp, seen
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        art_mod.persistence_store._session_dir = original_session_dir
        art_mod.resolve_artifact_file = original_resolve


@pytest.mark.parametrize("label,wire,resolved", ACCEPTED, ids=[a[0] for a in ACCEPTED])
def test_artifact_route_serves_every_valid_backend_name(backend, label, wire, resolved):
    base, _tmp, seen = backend
    seen.clear()
    response = httpx.get(f"{base}/api/sessions/graph_1/artifacts/{wire}", timeout=10)
    assert response.status_code == 200, f"{label}: {response.text}"
    # The exact bytes of the file the backend resolved (proves the decode chain).
    assert response.content == f"DATA:{resolved}".encode()
    assert seen == [resolved]


def test_artifact_route_decodes_question_mark_name(backend):
    if sys.platform == "win32":
        pytest.skip("Windows forbids '?' in filenames")
    base, _tmp, seen = backend
    seen.clear()
    response = httpx.get(f"{base}/api/sessions/graph_1/artifacts/a%3Fb.png", timeout=10)
    assert response.status_code == 200
    assert response.content == b"DATA:a?b.png"
    assert seen == ["a?b.png"]


@pytest.mark.parametrize("wire", REJECTED)
def test_artifact_route_refuses_traversal(backend, wire):
    base, _tmp, _seen = backend
    response = httpx.get(f"{base}/api/sessions/graph_1/artifacts/{wire}", timeout=10)
    assert response.status_code == 400, f"{wire}: {response.status_code}"
    assert b"SECRET" not in response.content


def test_raw_route_delegates_dot_and_dotdot_to_backend_resolution(backend):
    base, tmp, _seen = backend
    for wire in [f"{tmp}/sub/../secret.png", f"{tmp}/./secret.png"]:
        response = httpx.get(f"{base}/api/files/raw", params={"path": wire}, timeout=10)
        assert response.status_code == 200, wire
        assert response.content == b"SECRET"
    missing = httpx.get(
        f"{base}/api/files/raw", params={"path": f"{tmp}/missing.png"}, timeout=10
    )
    assert missing.status_code == 404
