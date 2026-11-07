"""ktui as a client of a KT server (``kt serve`` / ``kt web``): one session that runs there,
driven like a local one.

:class:`RemoteSession` reads the session's creatures and channels over REST, opens the chat
socket (``/ws/sessions/{sid}/creatures/{cid}/chat``, which carries every creature of the
graph) and turns its frames into the calls a creature's output makes, so ktui's own output
module draws them. Messages, replies to questions and approvals go back as socket frames;
interrupt, stop, background, model and folder switches and commands as REST calls (the
stand-ins in ``remote_proxies.py``).

A dropped connection is retried with a growing pause; once back, every transcript is drawn
again from the server's history and live frames go on (running jobs come back with it).
"""

import asyncio
import base64
import json
from collections import deque
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake

from kohakuterrarium.builtins.ktui.remote_frames import FrameDispatch
from kohakuterrarium.builtins.ktui.remote_proxies import (
    RemoteCreature,
    RemoteEngine,
    server_token,
)
from kohakuterrarium.terrarium.events import EngineEvent, EventKind
from kohakuterrarium.utils.logging import get_logger

logger = get_logger(__name__)

HISTORY_LIMIT = 400
# a long history is read this many pages back at most
HISTORY_PAGES = 25
# a picture larger than this stays a link
MEDIA_MAX_BYTES = 8 * 1024 * 1024
RETRY_PAUSES = (1.0, 2.0, 4.0, 8.0, 15.0)
POLL_SECONDS = 5.0


class RemoteError(RuntimeError):
    pass


class RemoteSession(FrameDispatch):
    def __init__(self, base_url: str, session_id: str, token: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.session_id = session_id
        self.token = token if token is not None else server_token()
        headers = {"X-KT-Host-Token": self.token} if self.token else {}
        self.http = httpx.AsyncClient(
            base_url=self.base_url, headers=headers, timeout=30
        )
        self.engine = RemoteEngine(self)
        self.creatures: dict[str, RemoteCreature] = {}
        self.channels: list[dict] = []
        self.channel_history: dict[str, list[dict]] = {}
        self.connected = False
        # frames to send; one leaves only once the socket took it (kept across a drop)
        self._outbox: deque[dict] = deque()
        self._has_mail = asyncio.Event()
        self._closing = asyncio.Event()
        self._seen_messages: set[str] = set()
        # pictures fetched from the server, by URL
        self._media: dict[str, str] = {}
        self._subscribers: list[tuple[Any, asyncio.Queue]] = []
        self._tasks: set[asyncio.Task] = set()
        self._closed = False
        self._ws: Any = None
        # a drop asked for (new members): no "connection lost" for it
        self._rejoining = False
        self._rejoin_wanted = False
        # set by the launcher: told about connection changes and errors; redraws after a
        # reconnect
        self.on_state: Callable[[str, str], None] = lambda level, text: None
        self.on_reconnect: Callable[[], Any] | None = None

    # -- REST -----------------------------------------------------------

    async def _call(
        self, method: str, path: str, body: Any = None, timeout: Any = 30
    ) -> Any:
        try:
            response = await self.http.request(method, path, json=body, timeout=timeout)
        except httpx.HTTPError as exc:
            raise RemoteError(f"server not reachable: {exc}") from exc
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except Exception:
                detail = response.text
            raise RemoteError(f"{response.status_code}: {detail}")
        try:
            return response.json()
        except ValueError:
            return None

    async def get(self, path: str, **params: Any) -> Any:
        if params:
            path = f"{path}?{httpx.QueryParams(params)}"
        return await self._call("GET", path)

    async def post(self, path: str, body: Any = None, timeout: Any = 30) -> Any:
        """``timeout=None`` for calls that last a whole turn (a re-run)."""
        return await self._call("POST", path, body if body is not None else {}, timeout)

    async def put(self, path: str, body: Any = None) -> Any:
        return await self._call("PUT", path, body)

    async def delete(self, path: str) -> Any:
        return await self._call("DELETE", path)

    def spawn(self, coro: Any) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(self._quiet(coro))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _quiet(self, coro: Any) -> Any:
        try:
            return await coro
        except Exception as exc:
            self.on_state("error", str(exc))
            return None

    # -- the session ----------------------------------------------------

    async def open(self) -> None:
        """Reads the session (creatures, channels and their history, commands)."""
        await self.refresh_creatures(announce=False)
        if not self.creatures:
            raise RemoteError(f"session {self.session_id} has no creatures")
        await asyncio.gather(
            *(c.agent.load_commands() for c in self.creatures.values()),
            *(c.agent.load_state() for c in self.creatures.values()),
        )

    async def refresh_creatures(self, announce: bool = True) -> None:
        listed = (
            await self.get(f"/api/sessions/active/{self.session_id}/creatures") or []
        )
        seen = set()
        changed = False
        for info in listed:
            cid = str(info.get("creature_id") or info.get("agent_id") or "")
            if not cid:
                continue
            seen.add(cid)
            if cid in self.creatures:
                self.creatures[cid].update(info)
            else:
                self.creatures[cid] = RemoteCreature(self, info)
                changed = True
        for cid in [c for c in self.creatures if c not in seen]:
            del self.creatures[cid]
            changed = True
        try:
            channels = await self.get(
                f"/api/sessions/topology/{self.session_id}/channels"
            )
        except RemoteError:
            channels = []
        names = {c.get("name") for c in self.channels}
        self.channels = [c for c in channels or [] if c.get("name")]
        for c in self.channels:
            self.channel_history.setdefault(c["name"], [])
        changed = changed or names != {c.get("name") for c in self.channels}
        if changed and announce:
            self._publish(
                EngineEvent(EventKind.TOPOLOGY_CHANGED, graph_id=self.session_id)
            )
            self._rejoin_wanted = True
        if self._rejoin_wanted and self.connected and not self._busy():
            # the socket carries the members it was opened with: open it again (between
            # turns, so no streamed reply is cut)
            self._rejoin_wanted = False
            self._rejoining = True
            self.spawn(self.drop())

    async def history(self, creature: RemoteCreature) -> list[dict]:
        """The creature's recorded events, page by page from the newest (up to
        ``HISTORY_PAGES`` pages), for drawing its transcript and its versions."""
        path = (
            f"/api/sessions/{self.session_id}/creatures/{creature.creature_id}/history"
        )
        pages: list[list[dict]] = []
        before: str | None = None
        try:
            for _ in range(HISTORY_PAGES):
                params: dict[str, Any] = {"limit": HISTORY_LIMIT}
                if before:
                    params["before"] = before
                body = await self.get(path, **params) or {}
                pages.append(list(body.get("events") or []))
                page = body.get("history_page") or {}
                before = page.get("before")
                if not page.get("has_older") or not before:
                    break
        except RemoteError as exc:
            self.on_state("warning", f"history of {creature.name} not read: {exc}")
            return list(creature.agent.session_store.events)
        events = [e for page in reversed(pages) for e in page]
        for event in events:
            # pictures in tool results: fetched in so a redraw draws them
            data = event.get("data") if isinstance(event.get("data"), dict) else event
            if isinstance(data.get("output"), list):
                data["output"] = await self.inline_parts(data["output"])
        # kept for what reads the history again (versions, redraws)
        creature.agent.session_store.events = events
        return events

    def events(self, event_filter: Any = None) -> AsyncIterator[EngineEvent]:
        async def follow() -> AsyncIterator[EngineEvent]:
            # registered once iterated, removed when the iteration ends
            queue: asyncio.Queue = asyncio.Queue()
            self._subscribers.append((event_filter, queue))
            try:
                while True:
                    yield await queue.get()
            finally:
                self._subscribers[:] = [
                    s for s in self._subscribers if s[1] is not queue
                ]

        return follow()

    def _publish(self, ev: EngineEvent) -> None:
        for event_filter, queue in self._subscribers:
            if event_filter is None or event_filter.matches(ev):
                queue.put_nowait(ev)

    async def inline_media(self, url: str) -> str:
        """A picture the server serves (``/api/...``) as a data URL (cached); the URL itself
        when it cannot be fetched or is too large."""
        if not url.startswith("/"):
            return url
        if url in self._media:
            return self._media[url]
        try:
            response = await self.http.get(url)
            data = response.content if response.status_code < 400 else b""
        except httpx.HTTPError:
            data = b""
        kind = response.headers.get("content-type", "") if data else ""
        if not data or len(data) > MEDIA_MAX_BYTES or not kind.startswith("image/"):
            return url
        inlined = f"data:{kind.split(';')[0]};base64,{base64.b64encode(data).decode()}"
        self._media[url] = inlined
        return inlined

    def _busy(self) -> bool:
        return any(c.agent.processing for c in self.creatures.values())

    def send_frame(self, frame: dict) -> None:
        """Queued while disconnected; sent in order once the socket is up."""
        self._outbox.append(frame)
        self._has_mail.set()

    # -- the socket -----------------------------------------------------

    def _ws_url(self) -> str:
        first = next(iter(self.creatures))
        scheme = "wss" if self.base_url.startswith("https") else "ws"
        host = self.base_url.split("://", 1)[-1]
        return f"{scheme}://{host}/ws/sessions/{self.session_id}/creatures/{first}/chat"

    async def run(self) -> None:
        """Keeps the socket up until :meth:`close`, reconnecting after a drop."""
        failures = 0
        first = True
        poll = asyncio.get_running_loop().create_task(self._poll())
        try:
            while not self._closed:
                try:
                    if not first:
                        # the creature the socket names may have left meanwhile
                        await self.refresh_creatures()
                    if not self.creatures:
                        raise RemoteError("the session has no creatures left")
                    protocols = [f"kt-token.{self.token}"] if self.token else None
                    async with ws_connect(
                        self._ws_url(), subprotocols=protocols, max_size=None
                    ) as ws:
                        self.connected, failures = True, 0
                        # opened now: it carries every current member
                        self._rejoin_wanted = False
                        # ktui draws kt.ui blocks and widgets: the model may use them
                        await ws.send(
                            json.dumps(
                                {"type": "client_hello", "renderers": {"kt.ui": 1}}
                            )
                        )
                        if not first:
                            if not self._rejoining:
                                self.on_state(
                                    "success", "reconnected · history redrawn"
                                )
                            self._rejoining = False
                            if self.on_reconnect is not None:
                                try:
                                    await self.on_reconnect()
                                except Exception as exc:  # the link stays up regardless
                                    logger.warning(
                                        "remote redraw failed",
                                        error=str(exc),
                                        exc_info=True,
                                    )
                        first = False
                        await self._serve(ws)
                except (
                    OSError,
                    ConnectionClosed,
                    InvalidHandshake,
                    RemoteError,
                ) as exc:
                    logger.debug("remote socket dropped", error=str(exc))
                if self._closed:
                    break
                if self.connected and not self._rejoining:
                    self.on_state("warning", "connection lost · reconnecting…")
                if not self.connected:
                    # an attempt failed: whatever comes next is a real reconnect
                    self._rejoining = False
                self.connected = False
                pause = RETRY_PAUSES[min(failures, len(RETRY_PAUSES) - 1)]
                try:
                    await asyncio.wait_for(self._closing.wait(), pause)
                except asyncio.TimeoutError:
                    pass
                failures += 1
        finally:
            poll.cancel()

    async def _serve(self, ws: Any) -> None:
        self._ws = ws
        sender = asyncio.get_running_loop().create_task(self._send_loop(ws))
        try:
            async for raw in ws:
                try:
                    frame = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(frame, dict):
                    try:
                        await self.dispatch(frame)
                    except Exception as exc:  # one bad frame must not drop the link
                        logger.warning(
                            "remote frame failed", error=str(exc), exc_info=True
                        )
        finally:
            sender.cancel()

    async def _send_loop(self, ws: Any) -> None:
        while True:
            while not self._outbox:
                self._has_mail.clear()
                await self._has_mail.wait()
            frame = self._outbox[0]
            try:
                await ws.send(json.dumps(frame, default=str))
            except ConnectionClosed:
                # still first in line: sent after the reconnect
                return
            self._outbox.popleft()

    async def _poll(self) -> None:
        """Creatures joining or leaving, model and folder changes made elsewhere."""
        while not self._closed:
            await asyncio.sleep(POLL_SECONDS)
            if self.connected:
                await self._quiet(self.refresh_creatures())
                await asyncio.gather(
                    *(c.agent.load_state() for c in list(self.creatures.values())),
                    return_exceptions=True,
                )

    async def drop(self) -> None:
        """Closes the socket as a network drop would (it is opened again)."""
        if self._ws is not None:
            await self._ws.close()

    async def close(self) -> None:
        self._closed = True
        self._closing.set()
        await self.drop()
        for task in list(self._tasks):
            task.cancel()
        await self.http.aclose()
