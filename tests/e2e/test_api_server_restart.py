"""E2E journey: a crashed server comes back with its sessions still working.

Three real ``python -m kohakuterrarium.api.main`` processes share one config
and session directory; the only seam is the LLM (``KT_TEST_LLM_SCRIPT``).

1. Server 1 hosts: ``alpha`` (idle after a turn), ``beta`` (mid-turn), a
   terrarium whose ``delta`` the user stopped, and ``gamma-gone`` (stopped by
   the user as a whole). Then the process is hard-killed — no shutdown.
2. Server 2 boots and restores on its own: alpha and the terrarium come back
   running, delta stays stopped, beta is told the turn was cut off and keeps
   working, the user-stopped session stays stopped; alpha chats again.
3. Server 3 boots with ``KT_AUTO_RESUME=0`` after a graceful stop of
   server 2: nothing is restored.
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.timeout(240)

LONG_REPLY = "Working through the long task step by step. " * 40


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _write_fixtures(root: Path) -> tuple[Path, Path, Path]:
    creature = root / "creatures" / "probe"
    creature.mkdir(parents=True)
    (creature / "config.yaml").write_text(
        "name: probe\nversion: '1.0'\nsystem_prompt: You are a test creature.\n"
        "tools: []\nsubagents: []\n",
        encoding="utf-8",
    )
    recipe = root / "terrariums" / "pair"
    recipe.mkdir(parents=True)
    rel = os.path.relpath(creature, recipe).replace("\\", "/")
    (recipe / "terrarium.yaml").write_text(
        "terrarium:\n  name: pair\n  creatures:\n"
        f"    - {{ name: gamma, base_config: '{rel}' }}\n"
        f"    - {{ name: delta, base_config: '{rel}' }}\n",
        encoding="utf-8",
    )
    script = root / "llm-script.json"
    script.write_text(
        json.dumps(
            {
                "script": [
                    {"response": "Quick answer.", "match": "quick question"},
                    {
                        "response": LONG_REPLY,
                        "match": "long task",
                        "delay_per_chunk": 0.2,
                    },
                    {"response": "Continuing the task.", "match": "server restarted"},
                    "OK",
                ]
            }
        ),
        encoding="utf-8",
    )
    return creature, recipe, script


class _Server:
    def __init__(self, root: Path, script: Path, *, auto_resume: bool = True) -> None:
        self.port = _free_port()
        env = {
            **os.environ,
            "KT_CONFIG_DIR": str(root / "config"),
            "KT_SESSION_DIR": str(root / "sessions"),
            "KT_TEST_LLM_SCRIPT": str(script),
            "KT_AUTO_RESUME": "1" if auto_resume else "0",
        }
        self.log = open(
            root / f"server-{self.port}.log", "w", encoding="utf-8"
        )  # noqa: SIM115
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "kohakuterrarium.api.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
            ],
            env=env,
            stdout=self.log,
            stderr=subprocess.STDOUT,
        )
        self.base = f"http://127.0.0.1:{self.port}"
        self.client = httpx.Client(base_url=self.base, timeout=60)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                if self.client.get("/healthz").status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            if self.proc.poll() is not None:
                raise RuntimeError(f"server exited early, see {self.log.name}")
            time.sleep(0.3)
        raise RuntimeError("server did not come up")

    def kill(self) -> None:
        self.proc.kill()
        self.proc.wait()
        self.client.close()
        self.log.close()

    def stop(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        self.client.close()
        self.log.close()


def _wait(predicate, timeout=60.0, step=0.3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    raise AssertionError("condition not met in time")


def _creatures(client: httpx.Client, sid: str) -> list[dict]:
    return client.get(f"/api/sessions/active/{sid}/creatures").json()


def _history(client: httpx.Client, sid: str, cid: str) -> dict:
    return client.get(
        f"/api/sessions/{sid}/creatures/{cid}/history", params={"stream": "snapshot"}
    ).json()


def _sessions_by_name(client: httpx.Client) -> dict[str, dict]:
    return {row["name"]: row for row in client.get("/api/sessions/active").json()}


def test_crashed_server_restores_running_sessions(tmp_path):
    creature, recipe, script = _write_fixtures(tmp_path)
    pwd = str(tmp_path)

    # --- server 1: four sessions in four states, then a hard kill --------
    s1 = _Server(tmp_path, script)
    try:
        alpha = s1.client.post(
            "/api/sessions/active/agents",
            json={"config_path": str(creature), "pwd": pwd, "name": "alpha"},
        ).json()
        assert s1.client.post(
            f"/api/sessions/{alpha['session_id']}/creatures/{alpha['agent_id']}/chat",
            json={"message": "quick question"},
        ).json() == {"response": "Quick answer."}

        gone = s1.client.post(
            "/api/sessions/active/agents",
            json={"config_path": str(creature), "pwd": pwd, "name": "gamma-gone"},
        ).json()
        assert (
            s1.client.delete(
                f"/api/sessions/active/agents/{gone['agent_id']}"
            ).status_code
            == 200
        )

        pair = s1.client.post(
            "/api/sessions/active/terrariums",
            json={"config_path": str(recipe), "pwd": pwd, "name": "pair-run"},
        ).json()
        pair_sid = pair["terrarium_id"]
        delta = next(c for c in _creatures(s1.client, pair_sid) if c["name"] == "delta")
        assert (
            s1.client.post(
                f"/api/sessions/{pair_sid}/creatures/{delta['creature_id']}/stop"
            ).status_code
            == 200
        )

        beta = s1.client.post(
            "/api/sessions/active/agents",
            json={"config_path": str(creature), "pwd": pwd, "name": "beta"},
        ).json()
        threading.Thread(
            target=lambda: _quiet_post(
                s1.base,
                f"/api/sessions/{beta['session_id']}/creatures/{beta['agent_id']}/chat",
                {"message": "long task"},
            ),
            daemon=True,
        ).start()
        _wait(
            lambda: _history(s1.client, beta["session_id"], beta["agent_id"]).get(
                "is_processing"
            )
        )
        time.sleep(6)  # past one heartbeat, mid-reply
    finally:
        s1.kill()

    # --- server 2: restores on its own -----------------------------------
    s2 = _Server(tmp_path, script)
    try:
        state = _wait(
            lambda: (lambda st: st if not st["running"] else None)(
                s2.client.get("/api/sessions/restore-state").json()
            ),
            timeout=120,
        )
        assert state["enabled"] is True
        assert [o["status"] for o in state["outcomes"]] == ["restored"] * 3, state[
            "outcomes"
        ]
        assert [(r["restored_this_boot"], r["failed"]) for r in state["rows"]] == [
            (True, None)
        ] * 3
        sessions = _sessions_by_name(s2.client)
        names = {row["session_id"]: name for name, row in sessions.items()}
        by_name = {names[o["session_id"]]: o for o in state["outcomes"]}
        pair_name = next(n for n in by_name if n not in ("alpha", "beta"))
        assert set(by_name) == {"alpha", "beta", pair_name}
        assert (by_name["beta"]["interrupted"], by_name["beta"]["stopped"]) == (
            ["beta"],
            [],
        )
        assert (by_name["alpha"]["interrupted"], by_name["alpha"]["stopped"]) == (
            [],
            [],
        )
        assert (by_name[pair_name]["interrupted"], by_name[pair_name]["stopped"]) == (
            [],
            ["delta"],
        )
        assert "gamma-gone" not in sessions
        pair_creatures = {
            c["name"]: c
            for c in _creatures(s2.client, sessions[pair_name]["session_id"])
        }
        assert pair_creatures["gamma"]["running"] is True
        assert pair_creatures["delta"]["running"] is False

        beta2 = _creatures(s2.client, sessions["beta"]["session_id"])[0]

        seen: list = []

        def _beta_continued():
            msgs = _history(
                s2.client, sessions["beta"]["session_id"], beta2["creature_id"]
            )["messages"]
            seen[:] = [(m["role"], str(m.get("content"))[:80]) for m in msgs]
            users = [
                m["content"]
                for m in msgs
                if m["role"] == "user" and isinstance(m["content"], str)
            ]
            replies = [m["content"] for m in msgs if m["role"] == "assistant"]
            return any("server restarted" in u for u in users) and any(
                "Continuing the task." in (r or "") for r in replies
            )

        try:
            _wait(_beta_continued, timeout=60)
        except AssertionError:
            raise AssertionError(f"beta never continued; history: {seen}") from None

        alpha2 = _creatures(s2.client, sessions["alpha"]["session_id"])[0]
        assert s2.client.post(
            f"/api/sessions/{sessions['alpha']['session_id']}/creatures/{alpha2['creature_id']}/chat",
            json={"message": "quick question"},
        ).json() == {"response": "Quick answer."}
        alpha_users = [
            m["content"]
            for m in _history(
                s2.client, sessions["alpha"]["session_id"], alpha2["creature_id"]
            )["messages"]
            if m["role"] == "user"
        ]
        assert alpha_users == ["quick question", "quick question"]
    finally:
        s2.stop()

    # --- server 3: auto-resume off ----------------------------------------
    s3 = _Server(tmp_path, script, auto_resume=False)
    try:
        state = s3.client.get("/api/sessions/restore-state").json()
        assert state["enabled"] is False and state["outcomes"] == []
        assert s3.client.get("/api/sessions/active").json() == []
        assert len(state["rows"]) == 3

        # History: persisted names, first-turn summaries, the latest exchange,
        # and why each session is down (server 2 stopped; gamma-gone by the user).
        listing = s3.client.get(
            "/api/sessions", params={"refresh": "true", "limit": 50}
        ).json()["sessions"]
        by_title = {row["title"]: row for row in listing if row["title"]}
        assert {"alpha", "beta", "pair-run", "gamma-gone"} <= set(by_title)
        alpha_row = by_title["alpha"]
        assert (alpha_row["summary"], alpha_row["summary_source"]) == (
            "quick question",
            "heuristic",
        )
        assert (
            alpha_row["last_user"],
            alpha_row["last_reply"],
            alpha_row["turn_count"],
        ) == ("quick question", "Quick answer.", 2)
        down = "crash" if sys.platform == "win32" else "shutdown"
        assert (alpha_row["stop_reason"], by_title["gamma-gone"]["stop_reason"]) == (
            down,
            "user",
        )
        quote = s3.client.get(
            f"/api/sessions/{alpha_row['name']}/exchanges", params={"limit": 3}
        ).json()
        assert [(e["turn"], e["user"], e["reply"]) for e in quote["exchanges"]] == [
            (1, "quick question", "Quick answer."),
            (2, "quick question", "Quick answer."),
        ]
        edited = s3.client.put(
            f"/api/sessions/{alpha_row['name']}/summary/text",
            json={"text": "Alpha smoke run"},
        ).json()
        assert edited["summary"]["source"] == "user"
        found = s3.client.get("/api/sessions", params={"search": "smoke"}).json()[
            "sessions"
        ]
        assert [row["title"] for row in found] == ["alpha"]
        assert (
            s3.client.get("/api/settings/session-summary").json()["source_override"]
            == "heuristic"
        )
    finally:
        s3.stop()


def _quiet_post(base: str, path: str, body: dict) -> None:
    try:
        httpx.post(base + path, json=body, timeout=120)
    except httpx.HTTPError:
        pass
