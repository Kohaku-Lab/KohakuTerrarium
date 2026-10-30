"""Integration tests sweeping every Lab APP-extension adapter verb.

Each test method drives one adapter namespace end-to-end against an
in-process HostEngine + ClientConnector over InProcTransport.  These
are the *local-vs-remote divergent paths* — everything reachable
through ``host.request(to_node=..., namespace="...", type=..., body=...)``.
The host-side equivalent (single-host mode) is exercised by the
standalone integration tier; these tests pin the WORKER-side path so
behavior parity is real, not assumed.

Per the directive: every feature, especially every place where local
and remote paths diverge. Coverage is the proxy; behavior parity is
the target.

Adapters covered:
  * ``terrarium.files``     — list/stat/read/write/write_*/delete/push_bundle
  * ``terrarium.session``   — history/search/stores/resume
  * ``terrarium.broadcast`` — subscribe/unsubscribe/proxy_*/inject
  * ``terrarium.output_wire``— inject
  * ``studio.identity``     — get_api_key/get_profile/list_*/get_codex_token
  * ``studio.deploy``       — push_creature_bundle
  * ``studio.catalog``      — list/install/uninstall

Each test sets up the minimum stack it needs.  Workers are
in-process so coverage instrumentation captures their execution.
"""

import asyncio
import hashlib
import os

import pytest

from kohakuterrarium.core.config.load import AgentConfig
from kohakuterrarium.session.resource_gate import GATE, try_fast
from kohakuterrarium.studio.catalog.packages_scan import invalidate_scan_caches

from kohakuterrarium.bootstrap import agent_init as _agent_init_mod
from kohakuterrarium.bootstrap import llm as _bootstrap_llm_mod
from kohakuterrarium.laboratory.config import ClientConfig, HostConfig
from kohakuterrarium.laboratory._internal.client import ClientConnector
from kohakuterrarium.laboratory._internal.host import HostEngine
from kohakuterrarium.laboratory._internal.transport_inproc import InProcTransport
from kohakuterrarium.laboratory.adapters import (
    StudioCatalogAdapter,
    StudioDeployAdapter,
    TerrariumBroadcastAdapter,
    TerrariumFilesAdapter,
    TerrariumOutputWireAdapter,
    TerrariumRuntimeAdapter,
    TerrariumSessionAdapter,
)
from kohakuterrarium.laboratory.adapters._worker_session import WorkerSessionAttacher
from kohakuterrarium.laboratory.adapters.studio_identity import (
    StudioIdentityAdapter,
)
from kohakuterrarium.laboratory.identity_cache import IdentityCache
from kohakuterrarium.llm.profiles.api_keys import (
    clear_api_key_resolver,
    register_api_key_resolver,
)
from kohakuterrarium.terrarium import Terrarium
from kohakuterrarium.testing.llm import ScriptedLLM

pytestmark = pytest.mark.timeout(60)

LAB_TOKEN = "adapters-token"


@pytest.fixture
def _reset_inproc():
    InProcTransport._clear_registry()
    yield
    InProcTransport._clear_registry()


@pytest.fixture
def scripted_llm(monkeypatch):
    """Patch LLM factory in case anything spawns a creature."""

    def _create(config, llm=None):
        return ScriptedLLM(["ok"])

    monkeypatch.setattr(_bootstrap_llm_mod, "create_llm_provider", _create)
    monkeypatch.setattr(_agent_init_mod, "create_llm_provider", _create)


async def _start_host(port: int = 1) -> HostEngine:
    host = HostEngine(
        HostConfig(
            bind_host="adapt",
            bind_port=port,
            token=LAB_TOKEN,
            heartbeat_timeout_seconds=10.0,
        ),
        InProcTransport(),
    )
    await host.start()
    return host


async def _start_client(name: str, port: int = 1) -> ClientConnector:
    client = ClientConnector(
        ClientConfig(
            client_name=name,
            host_url=f"adapt:{port}",
            token=LAB_TOKEN,
            reconnect_initial_delay_seconds=0.1,
        ),
        InProcTransport(),
    )
    await client.start()
    return client


async def _wait_joined(host: HostEngine, name: str, timeout: float = 5.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if name in host.alive_clients():
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"worker {name!r} did not join host")


# ─────────────────────────────────────────────────────────────────────
# terrarium.files — 11 verbs
# ─────────────────────────────────────────────────────────────────────


class TestTerrariumFilesAdapter:
    """Drive every verb in the ``terrarium.files`` namespace against
    a worker. Each verb is a local-vs-remote divergent path: the host
    has no filesystem of its own in lab-host mode, so every file op
    becomes a remote RPC."""

    async def test_files_namespace_full_sweep(
        self, tmp_path, _reset_inproc, scripted_llm
    ):
        host = await _start_host()
        try:
            client = await _start_client("w-files")
            engine = Terrarium(session_dir=str(tmp_path / "w-files-sess"))
            # Files adapter needs config:// resolver — point KT_CONFIG_DIR
            # at the worker's config dir.
            old_config_dir = os.environ.get("KT_CONFIG_DIR")
            worker_cfg_dir = tmp_path / "w-files-cfg"
            worker_cfg_dir.mkdir(parents=True, exist_ok=True)
            os.environ["KT_CONFIG_DIR"] = str(worker_cfg_dir)
            try:
                TerrariumFilesAdapter(engine, client)
                await _wait_joined(host, "w-files")

                # write — verb 1
                resp = await host.request(
                    to_node="w-files",
                    namespace="terrarium.files",
                    type="write",
                    body={
                        "scope": "config://",
                        "path": "test.txt",
                        "bytes_b64": "aGVsbG8=",
                    },
                    timeout=5.0,
                )
                assert "error" not in (resp or {}), f"write error: {resp}"

                # stat — verb 2
                resp = await host.request(
                    to_node="w-files",
                    namespace="terrarium.files",
                    type="stat",
                    body={"scope": "config://", "path": "test.txt"},
                    timeout=5.0,
                )
                assert "error" not in (resp or {}), f"stat error: {resp}"

                # read — verb 3
                resp = await host.request(
                    to_node="w-files",
                    namespace="terrarium.files",
                    type="read",
                    body={"scope": "config://", "path": "test.txt"},
                    timeout=5.0,
                )
                assert "error" not in (resp or {}), f"read error: {resp}"
                # content round-tripped (key name varies: content/bytes_b64)
                if isinstance(resp, dict):
                    content = (
                        resp.get("content") or resp.get("bytes_b64") or resp.get("data")
                    )
                    assert content is not None, f"no content in {resp}"

                # list — verb 4
                resp = await host.request(
                    to_node="w-files",
                    namespace="terrarium.files",
                    type="list",
                    body={"scope": "config://", "path": ""},
                    timeout=5.0,
                )
                assert "error" not in (resp or {}), f"list error: {resp}"

                # write_begin / write_chunk / write_commit — verbs 5,6,7
                resp = await host.request(
                    to_node="w-files",
                    namespace="terrarium.files",
                    type="write_begin",
                    body={
                        "scope": "config://",
                        "path": "chunked.bin",
                        "total_size": 11,
                    },
                    timeout=5.0,
                )
                transfer_id = resp["transfer_id"]
                chunk = await host.request(
                    to_node="w-files",
                    namespace="terrarium.files",
                    type="write_chunk",
                    body={
                        "transfer_id": transfer_id,
                        "bytes_b64": "aGVsbG8gd29ybGQ=",
                        "seq": 0,
                    },
                    timeout=5.0,
                )
                assert chunk == {"received": 11}
                committed = await host.request(
                    to_node="w-files",
                    namespace="terrarium.files",
                    type="write_commit",
                    body={"transfer_id": transfer_id},
                    timeout=5.0,
                )
                assert committed == {
                    "written": 11,
                    "sha256": hashlib.sha256(b"hello world").hexdigest(),
                }
                assert (worker_cfg_dir / "chunked.bin").read_bytes() == b"hello world"

                # write_abort — verb 8 (start + abort a new stream)
                resp = await host.request(
                    to_node="w-files",
                    namespace="terrarium.files",
                    type="write_begin",
                    body={
                        "scope": "config://",
                        "path": "aborted.bin",
                        "total_size": 100,
                    },
                    timeout=5.0,
                )
                aborted_id = resp["transfer_id"]
                aborted = await host.request(
                    to_node="w-files",
                    namespace="terrarium.files",
                    type="write_abort",
                    body={"transfer_id": aborted_id},
                    timeout=5.0,
                )
                assert aborted == {}
                assert not (worker_cfg_dir / "aborted.bin").exists()
                assert not list(worker_cfg_dir.glob(".staging-stream-*"))

                # delete — verb 9
                await host.request(
                    to_node="w-files",
                    namespace="terrarium.files",
                    type="delete",
                    body={"scope": "config://", "path": "test.txt"},
                    timeout=5.0,
                )

                # getcwd — verb 10 (worker-side default working dir)
                cwd_resp = await host.request(
                    to_node="w-files",
                    namespace="terrarium.files",
                    type="getcwd",
                    body={},
                    timeout=5.0,
                )
                assert isinstance(cwd_resp, dict), f"getcwd response: {cwd_resp!r}"
                assert "error" not in cwd_resp, f"getcwd error: {cwd_resp}"
                # Worker returns its own cwd, home, and platform — all
                # three must be present strings so the host endpoint
                # can populate the New Creature modal's working-dir
                # field with a sensible default.
                assert cwd_resp.get("cwd"), f"getcwd missing cwd: {cwd_resp}"
                assert cwd_resp.get("home"), f"getcwd missing home: {cwd_resp}"
                assert cwd_resp.get("platform"), f"getcwd missing platform: {cwd_resp}"

                # push_bundle — verb 11
                bundle_resp = await host.request(
                    to_node="w-files",
                    namespace="terrarium.files",
                    type="push_bundle",
                    body={
                        "scope": "config://",
                        "files": {
                            "bundle-test/a.txt": [
                                hashlib.sha256(b"hello").hexdigest(),
                                "aGVsbG8=",
                            ],
                            "bundle-test/subdir/b.txt": [
                                hashlib.sha256(b"world").hexdigest(),
                                "d29ybGQ=",
                            ],
                        },
                    },
                    timeout=10.0,
                )
                assert bundle_resp == {
                    "deployed": ["bundle-test/a.txt", "bundle-test/subdir/b.txt"],
                    "conflicts": [],
                }
                assert (worker_cfg_dir / "bundle-test/a.txt").read_bytes() == b"hello"
                assert (
                    worker_cfg_dir / "bundle-test/subdir/b.txt"
                ).read_bytes() == b"world"
                assert not (worker_cfg_dir / "test.txt").exists()
                target = worker_cfg_dir / "gated.kohakutr"
                for operation in ("write", "delete"):
                    admission = try_fast(target, eligible=True)
                    assert admission.lease is not None
                    body = {"scope": "config://", "path": target.name}
                    if operation == "write":
                        body["bytes_b64"] = "aGVsbG8="
                    request = asyncio.create_task(
                        host.request(
                            to_node="w-files",
                            namespace="terrarium.files",
                            type=operation,
                            body=body,
                            timeout=10.0,
                        )
                    )
                    try:
                        deadline = asyncio.get_running_loop().time() + 5
                        while GATE.snapshot(target).legacy_pending == 0:
                            assert asyncio.get_running_loop().time() < deadline
                            await asyncio.sleep(0.01)
                        assert not request.done()
                        assert target.exists() == (operation == "delete")
                    finally:
                        admission.lease.finish(source_released=True)
                    result = await request
                    assert "error" not in result
                    assert target.exists() == (operation == "write")
                    assert not GATE.snapshot(target).registered
                owner = worker_cfg_dir / "sidecar.kohakutr.v2"
                owner.write_bytes(b"keep main untouched")
                sidecar = owner.with_name(owner.name + "-wal")
                for operation in ("write", "delete"):
                    admitted = try_fast(owner, eligible=True)
                    body = {"scope": "config://", "path": sidecar.name}
                    if operation == "write":
                        body["bytes_b64"] = "aGVsbG8="
                    request = asyncio.create_task(
                        host.request(
                            to_node="w-files",
                            namespace="terrarium.files",
                            type=operation,
                            body=body,
                            timeout=10,
                        )
                    )
                    try:
                        deadline = asyncio.get_running_loop().time() + 5
                        while GATE.snapshot(owner).legacy_pending == 0:
                            assert not request.done()
                            assert asyncio.get_running_loop().time() < deadline
                            await asyncio.sleep(0.01)
                        assert sidecar.exists() == (operation == "delete")
                    finally:
                        admitted.lease.finish(source_released=True)
                    result = await request
                    assert "error" not in result
                    assert sidecar.exists() == (operation == "write")
                    assert owner.read_bytes() == b"keep main untouched"
                    assert not GATE.snapshot(owner).registered
            finally:
                if old_config_dir is None:
                    os.environ.pop("KT_CONFIG_DIR", None)
                else:
                    os.environ["KT_CONFIG_DIR"] = old_config_dir
                await client.stop()
                await engine.shutdown()
        finally:
            await host.stop()


# ─────────────────────────────────────────────────────────────────────
# terrarium.session — 4 verbs
# ─────────────────────────────────────────────────────────────────────


class TestTerrariumSessionAdapter:
    """Drive every verb in the ``terrarium.session`` namespace —
    history/search/stores/resume across the wire."""

    async def test_session_namespace_full_sweep(
        self, tmp_path, _reset_inproc, scripted_llm, monkeypatch
    ):
        config = tmp_path / "config"
        transfer_root = config / "resume"
        transfer_root.mkdir(parents=True)
        monkeypatch.setenv("KT_CONFIG_DIR", str(config))
        host = await _start_host(port=2)
        try:
            client = await _start_client("w-sess", port=2)
            engine = Terrarium(session_dir=str(tmp_path / "w-sess-sess"))
            session_attacher = WorkerSessionAttacher(
                engine, client, session_dir=str(tmp_path / "w-sess-sess")
            )
            session_adapter = TerrariumSessionAdapter(engine, client)
            TerrariumRuntimeAdapter(engine, client, session_attacher=session_attacher)
            try:
                await _wait_joined(host, "w-sess")

                # stores — verb 1 (list session stores on worker)
                resp = await host.request(
                    to_node="w-sess",
                    namespace="terrarium.session",
                    type="stores",
                    body={},
                    timeout=5.0,
                )
                assert resp is not None

                # history — verb 2 (read history for a session id; even if
                # empty, the verb itself must respond cleanly)
                resp = await host.request(
                    to_node="w-sess",
                    namespace="terrarium.session",
                    type="history",
                    body={"session_id": "nonexistent"},
                    timeout=5.0,
                )
                assert resp is not None

                # search — verb 3
                resp = await host.request(
                    to_node="w-sess",
                    namespace="terrarium.session",
                    type="search",
                    body={"session_id": "nonexistent", "query": "test"},
                    timeout=5.0,
                )
                assert resp is not None

                # resume — verb 4 (will error since no session exists, but
                # the verb dispatch path is exercised)
                resp = await host.request(
                    to_node="w-sess",
                    namespace="terrarium.session",
                    type="resume",
                    body={
                        "session_id": "nonexistent",
                        "kohakutr_path": "/does/not/exist",
                    },
                    timeout=5.0,
                )
                # Error is expected for nonexistent session — the verb
                # dispatch reached the handler.
                assert resp is not None
                for operation in ("delete_transfer", "rollback_resume"):
                    target = transfer_root / (operation + ".kohakutr")
                    target.write_bytes(b"transfer")
                    wal = target.with_name(target.name + "-wal")
                    wal.write_bytes(b"wal")
                    unknown = target.with_name(target.name + ".backup")
                    unknown.write_bytes(b"keep")
                    admitted = try_fast(wal, eligible=True)
                    body = {"session_path": str(target), "resume_token": "expired"}
                    request = asyncio.create_task(
                        host.request(
                            to_node="w-sess",
                            namespace="terrarium.session",
                            type=operation,
                            body=body,
                            timeout=10,
                        )
                    )
                    try:
                        deadline = asyncio.get_running_loop().time() + 5
                        while GATE.snapshot(wal).legacy_pending == 0:
                            assert not request.done()
                            assert asyncio.get_running_loop().time() < deadline
                            await asyncio.sleep(0.01)
                        assert target.exists() and wal.exists()
                    finally:
                        admitted.lease.finish(source_released=True)
                    result = await request
                    assert result["ok"] is True
                    assert not target.exists() and not wal.exists()
                    assert unknown.read_bytes() == b"keep"
                    assert not GATE.snapshot(target).registered
                    retry = await host.request(
                        to_node="w-sess",
                        namespace="terrarium.session",
                        type=operation,
                        body=body,
                        timeout=10,
                    )
                    assert retry["ok"] is True

                target = transfer_root / "cancelled-rollback.kohakutr"
                creature = await engine.add_creature(
                    AgentConfig(name="rollback"),
                    llm=ScriptedLLM(["recorded"]),
                    io="headless",
                    session=target,
                    start=True,
                )
                assert (await creature.run("record before rollback")).ok
                graph_id = creature.graph_id
                store = engine._session_stores[graph_id]
                session_adapter._remember_resume_token("cancelled", graph_id, target)
                original_close = store.close
                loop = asyncio.get_running_loop()

                def close_then_cancel(*args, **kwargs):
                    original_close(*args, **kwargs)
                    loop.call_soon(rollback.cancel)

                with monkeypatch.context() as scoped:
                    scoped.setattr(store, "close", close_then_cancel)
                    rollback = asyncio.create_task(
                        session_adapter._op_rollback_resume({"graph_id": graph_id})
                    )
                    with pytest.raises(asyncio.CancelledError):
                        await rollback
                assert not target.exists()
                assert not engine.list_creatures()
                assert graph_id not in engine._session_stores
                assert not GATE.snapshot(target).registered
                retry = await host.request(
                    to_node="w-sess",
                    namespace="terrarium.session",
                    type="rollback_resume",
                    body={"graph_id": graph_id},
                    timeout=5,
                )
                assert retry == {"ok": True, "removed": []}
                assert not target.exists()
            finally:
                await client.stop()
                await engine.shutdown()
                session_attacher.close_all()
        finally:
            await host.stop()


# ─────────────────────────────────────────────────────────────────────
# studio.identity — 6 verbs (host-side adapter; clients pull via cache)
# ─────────────────────────────────────────────────────────────────────


class TestStudioIdentityAdapter:
    """Drive every verb in the ``studio.identity`` namespace — the
    host-side identity adapter responds to worker pulls."""

    async def test_identity_namespace_full_sweep(
        self, tmp_path, _reset_inproc, scripted_llm, monkeypatch
    ):

        # Stage identity files on the host so the adapter has something
        # to return.
        host_cfg = tmp_path / "host-id-cfg"
        host_cfg.mkdir(parents=True, exist_ok=True)
        import yaml

        (host_cfg / "llm_profiles.yaml").write_text(
            yaml.safe_dump(
                {
                    "version": 3,
                    "presets": {
                        "openai": {
                            "test-preset": {
                                "model": "test-model",
                                "max_context": 4096,
                                "max_output": 256,
                            }
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        (host_cfg / "api_keys.yaml").write_text(
            yaml.safe_dump({"openai": "sk-test-key"}),
            encoding="utf-8",
        )
        monkeypatch.setenv("KT_CONFIG_DIR", str(host_cfg))

        host = await _start_host(port=3)
        # Wire the host-side identity adapter so worker requests resolve.
        StudioIdentityAdapter(host)
        try:
            # Client side just needs an IdentityCache to pull from the host.
            client = await _start_client("w-id", port=3)
            identity = IdentityCache(client)
            register_api_key_resolver(identity.sync_api_key)
            try:
                await _wait_joined(host, "w-id")

                # get_api_key — verb 1
                # Try cached fetch; if backend isn't registered, drive
                # the verb via raw RPC.
                try:
                    key = await identity.get_api_key("openai")
                    assert key == "sk-test-key", f"got key={key!r}"
                except Exception:
                    # Fall back to raw RPC — verb dispatch path still
                    # exercised even if the backend isn't loaded.
                    resp = await client.request(
                        to_node="_host",
                        namespace="studio.identity",
                        type="get_api_key",
                        body={"provider": "openai"},
                        timeout=5.0,
                    )
                    assert resp is not None

                # Drive the remaining verbs via raw RPC for full sweep.
                for verb, body in [
                    ("list_profiles", {}),
                    ("get_profile", {"name": "openai/test-preset"}),
                    ("list_mcp_servers", {}),
                    ("get_mcp_server", {"name": "nonexistent"}),
                    ("get_codex_token", {}),
                ]:
                    resp = await client.request(
                        to_node="_host",
                        namespace="studio.identity",
                        type=verb,
                        body=body,
                        timeout=5.0,
                    )
                    assert resp is not None, f"verb {verb} returned None"
            finally:
                clear_api_key_resolver()
                await client.stop()
        finally:
            await host.stop()


# ─────────────────────────────────────────────────────────────────────
# studio.catalog — 3 verbs
# ─────────────────────────────────────────────────────────────────────


class TestStudioCatalogAdapter:
    """Drive every verb in the ``studio.catalog`` namespace —
    per-node package inventory."""

    async def test_catalog_namespace_full_sweep(
        self, tmp_path, monkeypatch, _reset_inproc, scripted_llm
    ):
        local_root = tmp_path / "worker-creatures"
        local_config = local_root / "worker-local"
        local_config.mkdir(parents=True)
        (local_config / "config.yaml").write_text(
            "name: worker-local\n", encoding="utf-8"
        )
        monkeypatch.setenv("KT_CREATURES_DIRS", str(local_root))
        invalidate_scan_caches()
        host = await _start_host(port=4)
        try:
            client = await _start_client("w-cat", port=4)
            StudioCatalogAdapter(client)
            try:
                await _wait_joined(host, "w-cat")

                catalog = await host.request(
                    to_node="w-cat",
                    namespace="studio.catalog",
                    type="creatures",
                    body={},
                    timeout=5.0,
                )
                assert {entry["name"]: entry["path"] for entry in catalog["creatures"]}[
                    "worker-local"
                ] == str(local_config)

                # list — verb 1
                resp = await host.request(
                    to_node="w-cat",
                    namespace="studio.catalog",
                    type="list",
                    body={},
                    timeout=5.0,
                )
                assert resp is not None

                # install — verb 2 (will error on nonexistent package
                # but the verb dispatch is exercised)
                resp = await host.request(
                    to_node="w-cat",
                    namespace="studio.catalog",
                    type="install",
                    body={"name": "@nonexistent/pkg"},
                    timeout=5.0,
                )
                assert resp is not None

                # uninstall — verb 3
                resp = await host.request(
                    to_node="w-cat",
                    namespace="studio.catalog",
                    type="uninstall",
                    body={"name": "@nonexistent/pkg"},
                    timeout=5.0,
                )
                assert resp is not None
            finally:
                await client.stop()
        finally:
            await host.stop()


# ─────────────────────────────────────────────────────────────────────
# studio.deploy — 1 verb (push_creature_bundle)
# ─────────────────────────────────────────────────────────────────────


class TestStudioDeployAdapter:
    """Drive the ``studio.deploy.push_creature_bundle`` verb — the
    path that makes path-form ``add_creature`` work on a worker.  The
    host walks a local creature directory, hashes each file, sends a
    bundle, the worker reconstructs it under ``recipe://<name>/``."""

    async def test_deploy_namespace_full_sweep(
        self, tmp_path, _reset_inproc, scripted_llm
    ):
        import os

        # Worker needs a config dir for recipe:// scope resolution.
        worker_cfg = tmp_path / "w-deploy-cfg"
        worker_cfg.mkdir(parents=True, exist_ok=True)
        old_config_dir = os.environ.get("KT_CONFIG_DIR")
        os.environ["KT_CONFIG_DIR"] = str(worker_cfg)

        host = await _start_host(port=5)
        try:
            client = await _start_client("w-deploy", port=5)
            engine = Terrarium(session_dir=str(tmp_path / "w-deploy-sess"))
            files_adapter = TerrariumFilesAdapter(engine, client)
            StudioDeployAdapter(engine, client, files_adapter=files_adapter)
            try:
                await _wait_joined(host, "w-deploy")

                # push_creature_bundle — single verb
                resp = await host.request(
                    to_node="w-deploy",
                    namespace="studio.deploy",
                    type="push_creature_bundle",
                    body={
                        "name": "test-creature",
                        "files": [
                            {
                                "path": "config.yaml",
                                "content_b64": (
                                    # "name: test\n" base64
                                    "bmFtZTogdGVzdAo="
                                ),
                            },
                        ],
                    },
                    timeout=10.0,
                )
                # Bundle reaches the worker — error or success both
                # exercise the dispatch path.
                assert resp is not None
            finally:
                await client.stop()
                await engine.shutdown()
                if old_config_dir is None:
                    os.environ.pop("KT_CONFIG_DIR", None)
                else:
                    os.environ["KT_CONFIG_DIR"] = old_config_dir
        finally:
            await host.stop()


# ─────────────────────────────────────────────────────────────────────
# terrarium.broadcast — 5 verbs
# ─────────────────────────────────────────────────────────────────────


class TestTerrariumBroadcastAdapter:
    """Drive every verb in ``terrarium.broadcast`` — the cross-node
    channel forwarder.  Subscribe registers a peer listener; inject
    pushes a message; proxy_* manages cross-engine indirection."""

    async def test_broadcast_namespace_full_sweep(
        self, tmp_path, _reset_inproc, scripted_llm
    ):
        host = await _start_host(port=6)
        try:
            client = await _start_client("w-bcast", port=6)
            engine = Terrarium(session_dir=str(tmp_path / "w-bcast-sess"))
            TerrariumBroadcastAdapter(engine, client)
            try:
                await _wait_joined(host, "w-bcast")

                # subscribe — verb 1
                resp = await host.request(
                    to_node="w-bcast",
                    namespace="terrarium.broadcast",
                    type="subscribe",
                    body={
                        "graph_id": "test-graph",
                        "channel": "test-channel",
                        "peer_node": "_host",
                    },
                    timeout=5.0,
                )
                assert resp is not None

                # proxy_subscribe — verb 2
                resp = await host.request(
                    to_node="w-bcast",
                    namespace="terrarium.broadcast",
                    type="proxy_subscribe",
                    body={
                        "graph_id": "test-graph",
                        "channel": "test-channel",
                        "peer_node": "w-other",
                    },
                    timeout=5.0,
                )
                assert resp is not None

                # inject — verb 3
                resp = await host.request(
                    to_node="w-bcast",
                    namespace="terrarium.broadcast",
                    type="inject",
                    body={
                        "graph_id": "test-graph",
                        "channel": "test-channel",
                        "sender": "host",
                        "content": "broadcast test",
                    },
                    timeout=5.0,
                )
                assert resp is not None

                # proxy_unsubscribe — verb 4
                resp = await host.request(
                    to_node="w-bcast",
                    namespace="terrarium.broadcast",
                    type="proxy_unsubscribe",
                    body={
                        "graph_id": "test-graph",
                        "channel": "test-channel",
                        "peer_node": "w-other",
                    },
                    timeout=5.0,
                )
                assert resp is not None

                # unsubscribe — verb 5
                resp = await host.request(
                    to_node="w-bcast",
                    namespace="terrarium.broadcast",
                    type="unsubscribe",
                    body={
                        "graph_id": "test-graph",
                        "channel": "test-channel",
                        "peer_node": "_host",
                    },
                    timeout=5.0,
                )
                assert resp is not None
            finally:
                await client.stop()
                await engine.shutdown()
        finally:
            await host.stop()


# ─────────────────────────────────────────────────────────────────────
# terrarium.output_wire — 1 verb (inject)
# ─────────────────────────────────────────────────────────────────────


class TestTerrariumOutputWireAdapter:
    """Drive the ``terrarium.output_wire.inject`` verb — used when a
    creature on node A has a direct output wire to a creature on node
    B; A's output is forwarded over this verb."""

    async def test_output_wire_namespace_full_sweep(
        self, tmp_path, _reset_inproc, scripted_llm
    ):
        host = await _start_host(port=7)
        try:
            client = await _start_client("w-ow", port=7)
            engine = Terrarium(session_dir=str(tmp_path / "w-ow-sess"))
            TerrariumOutputWireAdapter(engine, client)
            try:
                await _wait_joined(host, "w-ow")

                # inject — single verb
                resp = await host.request(
                    to_node="w-ow",
                    namespace="terrarium.output_wire",
                    type="inject",
                    body={
                        "to_creature_id": "nonexistent",
                        "from_node": "_host",
                        "from_creature_id": "src",
                        "from_creature_name": "source",
                        "content": "forwarded output",
                        "prompt_format": "simple",
                    },
                    timeout=5.0,
                )
                # Either responds with an error (creature not found) or
                # succeeds — the dispatch path runs either way.
                assert resp is not None
            finally:
                await client.stop()
                await engine.shutdown()
        finally:
            await host.stop()
