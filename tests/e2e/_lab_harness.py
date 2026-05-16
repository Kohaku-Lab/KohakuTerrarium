"""Real-deployment-like multi-node test harness.

This boots the **actual** ``kt serve --mode lab-host`` stack and **actual**
``kt lab-client`` workers — no hand-wired rig, no ``InProcTransport``:

* :class:`RealLabHost` runs the real :func:`create_app` FastAPI app under
  a real ``uvicorn.Server`` on an ephemeral port. Its lifespan starts a
  real :class:`HostEngine` over a real :class:`WebSocketTransport` (also
  on an ephemeral port). HTTP + WebSocket are served on a real socket.
* :class:`RealLabWorker` replicates ``cli/lab_client.py::_run_worker``
  verbatim — a real :class:`ClientConnector` over a real
  :class:`WebSocketTransport`, the full ten-adapter stack, a real
  :class:`Terrarium` engine, :class:`WorkerSessionAttacher`, and
  :class:`IdentityCache`.  Shares the test's event loop — fast, but
  cannot exhibit cross-process timing / concurrency bugs.
* :class:`RealLabSubprocessWorker` runs the worker as a **real OS
  subprocess** via ``python -m kohakuterrarium lab-client ...``.  Each
  worker has its own process, event loop, ``KT_CONFIG_DIR``, and
  ``KT_SESSION_DIR`` — identical isolation to a real deployment.  This
  is the only harness that can reproduce concurrency / RPC-stall /
  identity-isolation bugs faithfully.

The **only** seam is the LLM provider — in-process via
:func:`install_scripted_llm` for :class:`RealLabWorker`; cross-process
via the ``KT_TEST_LLM_SCRIPT`` env hook (read by
:func:`kohakuterrarium.testing.subprocess_seam.maybe_install_test_llm_seam`)
for :class:`RealLabSubprocessWorker`.  Everything else — transport,
engine, session stores, the FastAPI app — is real.

Because the in-process pieces all share one event loop, a loop-blocking
call anywhere freezes the whole stack: every awaited operation in a
test should be wrapped in :func:`asyncio.wait_for` (see
:data:`OP_TIMEOUT`) so a deadlock surfaces as a ``TimeoutError``
instead of hanging the run.
"""

import asyncio
import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx
import uvicorn
import websockets

from kohakuterrarium.api.app import create_app
from kohakuterrarium.bootstrap import agent_init as _agent_init_mod
from kohakuterrarium.bootstrap import llm as _bootstrap_llm_mod
from kohakuterrarium.core import agent_compact as _agent_compact_mod
from kohakuterrarium.core import agent_model as _agent_model_mod
from kohakuterrarium.laboratory import ClientConfig
from kohakuterrarium.laboratory._internal.client import ClientConnector
from kohakuterrarium.laboratory._internal.transport_ws import WebSocketTransport
from kohakuterrarium.laboratory.adapters import (
    StudioCatalogAdapter,
    StudioDeployAdapter,
    TerrariumAttachAdapter,
    TerrariumBroadcastAdapter,
    TerrariumEventsAdapter,
    TerrariumFilesAdapter,
    TerrariumOutputWireAdapter,
    TerrariumPtyAdapter,
    TerrariumRuntimeAdapter,
    TerrariumSessionAdapter,
)
from kohakuterrarium.laboratory.adapters._worker_session import WorkerSessionAttacher
from kohakuterrarium.laboratory.identity_cache import IdentityCache
from kohakuterrarium.llm.api_keys import (
    clear_api_key_resolver,
    register_api_key_resolver,
)
from kohakuterrarium.terrarium import Terrarium
from kohakuterrarium.testing.llm import ScriptedLLM

# Generous-but-finite: a healthy multi-node op (spawn / chat turn / stop)
# completes in well under a second over loopback. A deadlock blows past
# this. Kept under the pytest per-test timeout so the wait_for fires
# first and names the stuck operation.
OP_TIMEOUT = 8.0

LAB_TOKEN = "harness-token"


# ---------------------------------------------------------------------------
# LLM seam
# ---------------------------------------------------------------------------


def install_scripted_llm(
    monkeypatch, *, script: list[Any] | None = None
) -> dict[str, list[Any]]:
    """Patch every LLM-provider bind point to a shared :class:`ScriptedLLM`.

    Three functions reach the live backend and must all be seamed or an
    agent build / model switch escapes to a real provider:

    * ``bootstrap.llm.create_llm_provider`` — the canonical creature
      factory; ``bootstrap.agent_init`` imports it by name (second site).
    * ``bootstrap.llm.create_llm_from_profile_name`` — used by
      ``core.agent_model.switch_model`` and ``core.agent_compact``
      (imported by name at both sites).

    Returns a holder ``{"script": [...]}`` — mutate ``holder["script"]``
    before driving a turn to control the next reply.
    """
    holder: dict[str, list[Any]] = {"script": list(script or ["OK"])}

    def _fake_create(config, llm_override=None):
        return ScriptedLLM(holder["script"])

    def _fake_from_profile(name):
        return ScriptedLLM(holder["script"])

    monkeypatch.setattr(_bootstrap_llm_mod, "create_llm_provider", _fake_create)
    monkeypatch.setattr(_agent_init_mod, "create_llm_provider", _fake_create)
    monkeypatch.setattr(
        _bootstrap_llm_mod, "create_llm_from_profile_name", _fake_from_profile
    )
    monkeypatch.setattr(
        _agent_model_mod, "create_llm_from_profile_name", _fake_from_profile
    )
    monkeypatch.setattr(
        _agent_compact_mod, "create_llm_from_profile_name", _fake_from_profile
    )
    return holder


# ---------------------------------------------------------------------------
# Real lab host — the kt serve --mode lab-host process
# ---------------------------------------------------------------------------


class RealLabHost:
    """Boots the real ``create_app(lab_mode="lab-host")`` under uvicorn.

    Use as an async context manager::

        async with RealLabHost(tmp_path) as host:
            r = await host.http.get("/api/sessions/active")
            ...

    Exposes:

    * ``http`` — an :class:`httpx.AsyncClient` bound to the API base URL.
    * ``http_base`` / ``http_port`` — the API's real address.
    * ``lab_ws_url`` — the ``ws://`` URL workers connect to.
    * ``app`` — the FastAPI app (``app.state.lab_host_engine`` is the
      live :class:`HostEngine` once startup finishes).
    """

    def __init__(self, tmp_path, *, token: str = LAB_TOKEN) -> None:
        self._tmp_path = tmp_path
        self._token = token
        self.app = create_app(
            static_dir=None,
            lab_mode="lab-host",
            lab_bind="127.0.0.1:0",
            lab_token=token,
        )
        self._server: uvicorn.Server | None = None
        self._serve_task: asyncio.Task | None = None
        self.http: httpx.AsyncClient | None = None
        self.http_port: int = 0
        self.http_base: str = ""
        self.lab_ws_url: str = ""

    async def __aenter__(self) -> "RealLabHost":
        config = uvicorn.Config(
            self.app,
            host="127.0.0.1",
            port=0,
            log_level="warning",
            lifespan="on",
        )
        self._server = uvicorn.Server(config)
        # A server run as a loop task must NOT grab the process signal
        # handlers — that's the test runner's loop.
        self._server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
        self._serve_task = asyncio.create_task(self._server.serve())

        # Wait for uvicorn to finish startup (binds the socket AND runs
        # the lifespan, which starts the HostEngine).
        await self._wait_for(lambda: self._server.started, "uvicorn startup")
        self.http_port = self._server.servers[0].sockets[0].getsockname()[1]
        self.http_base = f"http://127.0.0.1:{self.http_port}"
        self.http = httpx.AsyncClient(base_url=self.http_base, timeout=OP_TIMEOUT)

        # The lifespan stashes the live HostEngine on app.state; its
        # transport's real bound port is what workers dial.
        await self._wait_for(
            lambda: getattr(self.app.state, "lab_host_engine", None) is not None,
            "HostEngine start",
        )
        host_engine = self.app.state.lab_host_engine
        addr = host_engine._server.local_addr  # (host, port)
        self.lab_ws_url = f"ws://127.0.0.1:{addr[1]}"
        return self

    async def __aexit__(self, *exc) -> None:
        if self.http is not None:
            await self.http.aclose()
        if self._server is not None:
            self._server.should_exit = True
        if self._serve_task is not None:
            with contextlib.suppress(asyncio.TimeoutError, Exception):
                await asyncio.wait_for(self._serve_task, timeout=OP_TIMEOUT)

    @staticmethod
    async def _wait_for(pred, what: str, timeout: float = OP_TIMEOUT) -> None:
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if pred():
                return
            await asyncio.sleep(0.02)
        raise asyncio.TimeoutError(f"{what} did not complete within {timeout}s")

    @property
    def host_engine(self):
        """The live :class:`HostEngine` (after startup)."""
        return self.app.state.lab_host_engine

    def api_ws(self, path: str):
        """Open a real WebSocket against the API (e.g. a chat stream).

        Returns the ``websockets`` async-context-manager connection.
        """
        url = f"ws://127.0.0.1:{self.http_port}{path}"
        return websockets.connect(url, open_timeout=OP_TIMEOUT)


# ---------------------------------------------------------------------------
# Real lab worker — the kt lab-client process
# ---------------------------------------------------------------------------


class RealLabWorker:
    """Replicates ``cli/lab_client.py::_run_worker`` as an in-loop worker.

    Real :class:`ClientConnector` over a real :class:`WebSocketTransport`,
    a real :class:`Terrarium` engine, and the full adapter stack a
    production worker installs. Use as an async context manager::

        async with RealLabWorker("worker-1", host.lab_ws_url, tmp_path) as w:
            ...

    Exposes ``engine`` (the worker's real Terrarium), ``client`` (the
    connector), and ``node_id`` (the worker's name on the host).
    """

    def __init__(
        self,
        name: str,
        host_ws_url: str,
        session_dir,
        *,
        token: str = LAB_TOKEN,
    ) -> None:
        self.node_id = name
        self._host_ws_url = host_ws_url
        self._session_dir = str(session_dir)
        self._token = token
        self.engine: Terrarium | None = None
        self.client: ClientConnector | None = None
        self.identity_cache: IdentityCache | None = None
        self._session_attacher: WorkerSessionAttacher | None = None
        self._registered_resolver = False

    async def __aenter__(self) -> "RealLabWorker":
        self.client = ClientConnector(
            ClientConfig(
                client_name=self.node_id,
                host_url=self._host_ws_url,
                token=self._token,
                heartbeat_interval_seconds=5.0,
            ),
            WebSocketTransport(),
        )
        self.engine = Terrarium(session_dir=self._session_dir)
        self._session_attacher = WorkerSessionAttacher(
            self.engine, self.client, session_dir=self._session_dir
        )
        identity_cache = IdentityCache(self.client)
        self.identity_cache = identity_cache
        register_api_key_resolver(identity_cache.sync_api_key)
        self._registered_resolver = True

        # The full ten-adapter stack — verbatim from _run_worker.
        TerrariumRuntimeAdapter(
            self.engine,
            self.client,
            session_attacher=self._session_attacher,
            identity_cache=identity_cache,
        )
        TerrariumEventsAdapter(self.engine, self.client)
        TerrariumAttachAdapter(self.engine, self.client)
        TerrariumPtyAdapter(self.engine, self.client)
        TerrariumBroadcastAdapter(self.engine, self.client)
        TerrariumOutputWireAdapter(self.engine, self.client)
        files_adapter = TerrariumFilesAdapter(self.engine, self.client)
        StudioDeployAdapter(self.engine, self.client, files_adapter=files_adapter)
        TerrariumSessionAdapter(self.engine, self.client)
        StudioCatalogAdapter(self.client)

        await asyncio.wait_for(self.client.start(), timeout=OP_TIMEOUT)
        return self

    async def __aexit__(self, *exc) -> None:
        if self._session_attacher is not None:
            with contextlib.suppress(Exception):
                self._session_attacher.close_all()
        if self._registered_resolver:
            with contextlib.suppress(Exception):
                clear_api_key_resolver()
        if self.client is not None:
            with contextlib.suppress(asyncio.TimeoutError, Exception):
                await asyncio.wait_for(self.client.stop(), timeout=OP_TIMEOUT)
        if self.engine is not None:
            with contextlib.suppress(asyncio.TimeoutError, Exception):
                await asyncio.wait_for(self.engine.shutdown(), timeout=OP_TIMEOUT)

    async def disconnect_hard(self) -> None:
        """Drop the worker's transport without graceful teardown.

        Simulates a worker process being killed / network-partitioned:
        the host must notice via heartbeat loss, not a clean LEFT.
        """
        if self.client is not None:
            with contextlib.suppress(asyncio.TimeoutError, Exception):
                await asyncio.wait_for(self.client.stop(), timeout=OP_TIMEOUT)


# ---------------------------------------------------------------------------
# Real lab worker — as a real OS subprocess (faithful prod-like isolation)
# ---------------------------------------------------------------------------


class RealLabSubprocessWorker:
    """Boots a real ``python -m kohakuterrarium lab-client`` subprocess.

    Unlike :class:`RealLabWorker` (which shares the test's event loop),
    this worker is a fully-isolated OS process — its own loop, its own
    ``KT_CONFIG_DIR``, its own ``KT_SESSION_DIR``.  That is the only
    configuration faithful enough to reproduce concurrency / RPC-stall
    / identity-isolation bugs.

    The worker is opaque from the test side: there is no in-process
    ``engine`` handle, no ``client`` handle.  Tests observe state via
    the host's API (``host.host_engine.alive_clients()``, HTTP routes,
    the chat WebSocket) — exactly the surface the real Vue frontend
    sees.

    Usage::

        async with RealLabSubprocessWorker(
            "worker-1", host.lab_ws_url, tmp_path / "worker-1",
            script=["scout reporting in"],
        ) as worker:
            await worker.wait_for_join(host)
            ...

    The constructor writes a ``llm_script.json`` under the worker's
    ``base_dir`` and passes the path via ``KT_TEST_LLM_SCRIPT``.  Tests
    can call :meth:`set_script` between turns to script the next
    provider's replies before spawning the next creature.
    """

    def __init__(
        self,
        name: str,
        host_ws_url: str,
        base_dir: Path,
        *,
        token: str = LAB_TOKEN,
        script: list[Any] | None = None,
        log_level: str = "WARNING",
        extra_env: dict[str, str] | None = None,
        use_test_llm_seam: bool = True,
    ) -> None:
        self.node_id = name
        self._host_ws_url = host_ws_url
        self._base_dir = Path(base_dir)
        self._token = token
        self._log_level = log_level
        self._extra_env = dict(extra_env or {})
        # When ``False``, the ``KT_TEST_LLM_SCRIPT`` env var is NOT
        # exported and the env-var seam at the top of ``_run_worker``
        # stays inactive — the worker takes the real
        # ``create_llm_provider`` path (profile resolution + api-key
        # lookup).  Tests that exercise the credential / identity
        # chain rely on this.
        self._use_test_llm_seam = use_test_llm_seam

        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._kt_config_dir = self._base_dir / "kt-config"
        self._kt_session_dir = self._base_dir / "kt-sessions"
        self._kt_config_dir.mkdir(parents=True, exist_ok=True)
        self._kt_session_dir.mkdir(parents=True, exist_ok=True)

        self._script_path = self._base_dir / "llm_script.json"
        self.set_script(script if script is not None else ["OK"])

        self._stderr_path = self._base_dir / "worker.stderr.log"
        self._stdout_path = self._base_dir / "worker.stdout.log"
        self._stderr_fp = None
        self._stdout_fp = None
        self._proc: subprocess.Popen | None = None

    # -- script control ------------------------------------------------

    def set_script(self, script: list[Any]) -> None:
        """Atomically (re)write the LLM script JSON file.

        The subprocess re-reads this file each time
        :func:`bootstrap.llm.create_llm_provider` is invoked (per
        creature spawn / model switch / compact run), so tests can
        update the script before driving the next operation that
        creates a new provider.
        """
        normalized: list[Any] = []
        for entry in script:
            if isinstance(entry, str):
                normalized.append(entry)
            else:
                # ScriptEntry — flatten to its response text only.  The
                # JSON file format is intentionally simple-strings-only;
                # tests needing match / delay / chunk control should use
                # :class:`RealLabWorker` (in-process) instead.
                normalized.append(getattr(entry, "response", str(entry)))
        # Write to temp then rename — atomic from the subprocess's view.
        tmp = self._script_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"script": normalized}), encoding="utf-8")
        os.replace(tmp, self._script_path)

    # -- lifecycle -----------------------------------------------------

    async def __aenter__(self) -> "RealLabSubprocessWorker":
        env = os.environ.copy()
        # Drop any inherited ``KT_TEST_LLM_SCRIPT`` so the parent
        # process's env doesn't accidentally activate the seam.
        env.pop("KT_TEST_LLM_SCRIPT", None)
        env.update(
            {
                "KT_CONFIG_DIR": str(self._kt_config_dir),
                "KT_SESSION_DIR": str(self._kt_session_dir),
                # Keep the worker quiet by default; tests that need
                # logs can override via ``extra_env``.
                "KT_LOG_STDERR": "1",
                # Force unbuffered I/O so our log captures fill in
                # real time — otherwise a worker crash leaves an
                # empty stderr file and diagnosis is painful.
                "PYTHONUNBUFFERED": "1",
            }
        )
        if self._use_test_llm_seam:
            env["KT_TEST_LLM_SCRIPT"] = str(self._script_path)
        # Propagate coverage subprocess hook so multi-node e2e
        # coverage measurements include worker-side code.  The
        # ``a1_coverage.pth`` site hook in this venv calls
        # ``coverage.process_startup()`` automatically whenever
        # ``COVERAGE_PROCESS_START`` is set.  Without this propagation,
        # only host-side code shows up in ``--cov`` output.
        cov_cfg = os.environ.get("COVERAGE_PROCESS_START")
        if cov_cfg and "COVERAGE_PROCESS_START" not in env:
            env["COVERAGE_PROCESS_START"] = cov_cfg
        env.update(self._extra_env)

        self._stderr_fp = open(self._stderr_path, "wb")
        self._stdout_fp = open(self._stdout_path, "wb")
        # Use the same interpreter the test runner uses — guarantees
        # we hit the editable install of this checkout, not a stray
        # site-packages copy.
        cmd = [
            sys.executable,
            "-m",
            "kohakuterrarium",
            "lab-client",
            "--host",
            self._host_ws_url,
            "--token",
            self._token,
            "--name",
            self.node_id,
            "--log-level",
            self._log_level,
            "--session-dir",
            str(self._kt_session_dir),
        ]
        # Windows: on POSIX we want a fresh process group so we can
        # kill the worker without taking the test runner down.
        popen_kwargs: dict[str, Any] = {
            "env": env,
            "stdout": self._stdout_fp,
            "stderr": self._stderr_fp,
            "stdin": subprocess.DEVNULL,
        }
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True
        self._proc = subprocess.Popen(cmd, **popen_kwargs)
        return self

    async def wait_for_join(self, host, *, timeout: float = OP_TIMEOUT) -> None:
        """Block until the host's ``alive_clients()`` includes this worker.

        Raises :class:`asyncio.TimeoutError` if the worker fails to
        register within ``timeout`` seconds (likely cause: a startup
        crash — inspect :attr:`stderr_path`).
        """
        loop = asyncio.get_event_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if self._proc is not None and self._proc.poll() is not None:
                raise RuntimeError(
                    f"worker {self.node_id!r} exited before join "
                    f"(rc={self._proc.returncode}); "
                    f"stderr: {self.dump_stderr()[:2000]}"
                )
            try:
                alive = set(host.host_engine.alive_clients())
            except Exception:
                alive = set()
            if self.node_id in alive:
                return
            await asyncio.sleep(0.05)
        raise asyncio.TimeoutError(
            f"worker {self.node_id!r} did not join host within {timeout}s; "
            f"stderr: {self.dump_stderr()[:2000]}"
        )

    async def __aexit__(self, *exc) -> None:
        await self._terminate()

    async def _terminate(self) -> None:
        if self._proc is None:
            return
        if self._proc.poll() is None:
            try:
                self._proc.terminate()
            except (ProcessLookupError, OSError):
                pass
            try:
                await asyncio.get_event_loop().run_in_executor(
                    None, self._proc.wait, OP_TIMEOUT
                )
            except subprocess.TimeoutExpired:
                with contextlib.suppress(Exception):
                    self._proc.kill()
                with contextlib.suppress(Exception):
                    self._proc.wait(timeout=2.0)
        if self._stderr_fp is not None:
            with contextlib.suppress(Exception):
                self._stderr_fp.close()
            self._stderr_fp = None
        if self._stdout_fp is not None:
            with contextlib.suppress(Exception):
                self._stdout_fp.close()
            self._stdout_fp = None

    async def kill_hard(self) -> None:
        """Simulate a network partition / crashed worker.

        SIGKILL on POSIX, ``Process.kill()`` on Windows — no graceful
        shutdown, no LEFT message, the host must notice via heartbeat
        loss.
        """
        if self._proc is None or self._proc.poll() is not None:
            return
        with contextlib.suppress(Exception):
            self._proc.kill()
        with contextlib.suppress(Exception):
            await asyncio.get_event_loop().run_in_executor(
                None, self._proc.wait, OP_TIMEOUT
            )

    # -- observability -------------------------------------------------

    @property
    def returncode(self) -> int | None:
        return self._proc.returncode if self._proc is not None else None

    @property
    def stderr_path(self) -> Path:
        return self._stderr_path

    @property
    def stdout_path(self) -> Path:
        return self._stdout_path

    @property
    def kt_config_dir(self) -> Path:
        return self._kt_config_dir

    @property
    def kt_session_dir(self) -> Path:
        return self._kt_session_dir

    def dump_stderr(self) -> str:
        """Return the worker's stderr as captured so far.

        The file is opened in binary mode and may contain partial
        UTF-8 sequences — decoded with ``errors="replace"`` so
        diagnostics never raise.
        """
        try:
            return self._stderr_path.read_bytes().decode("utf-8", errors="replace")
        except OSError:
            return ""

    def dump_stdout(self) -> str:
        try:
            return self._stdout_path.read_bytes().decode("utf-8", errors="replace")
        except OSError:
            return ""


__all__ = [
    "OP_TIMEOUT",
    "LAB_TOKEN",
    "install_scripted_llm",
    "RealLabHost",
    "RealLabWorker",
    "RealLabSubprocessWorker",
]
