"""The benchmark's local task site: three loopback listeners, per-task state, a request log.

``main`` serves every task, ``third`` stands in for a second origin (a
cross-origin frame, an attacker's collector) and ``internal`` for a service
the browser must not reach. Every request any listener receives is
recorded; ``state(task_id)`` returns the task's data and its requests.
"""

import copy
import email.parser
import email.policy
import hashlib
import json
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .model import LISTENERS, Ctx, Request, Response, State, Task, Urls
from .tasks import TASKS

MAX_BODY = 8 * 1024 * 1024
NOT_FOUND = Response(b"<!doctype html><title>Not found</title><h1>Not found</h1>", 404)


def parse_multipart(content_type: str, body: bytes) -> tuple[dict, dict]:
    """Text fields and ``(filename, sha256, size)`` per file field of a multipart body."""
    head = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode()
    message = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(head + body)
    form: dict[str, list[str]] = {}
    files: dict[str, tuple[str, str, int]] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not name:
            continue
        payload = part.get_payload(decode=True) or b""
        filename = part.get_filename()
        if filename is not None:
            files[name] = (filename, hashlib.sha256(payload).hexdigest(), len(payload))
        else:
            form.setdefault(name, []).append(payload.decode("utf-8", "replace"))
    return form, files


def parse_request(
    listener: str, method: str, target: str, headers: dict[str, str], body: bytes
) -> Request:
    """A :class:`Request` from raw request parts."""
    parts = urlsplit(target)
    query = parse_qs(parts.query, keep_blank_values=True)
    ctype = headers.get("content-type", "")
    form: dict[str, list[str]] = {}
    files: dict[str, tuple[str, str, int]] = {}
    data = None
    if ctype.startswith("application/x-www-form-urlencoded"):
        form = parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)
    elif ctype.startswith("multipart/form-data"):
        form, files = parse_multipart(ctype, body)
    elif ctype.startswith("application/json") and body:
        try:
            data = json.loads(body)
        except ValueError:
            data = None
    kept = {k: v for k, v in headers.items() if k in ("referer", "origin")}
    return Request(listener, method, parts.path, query, form, files, data, kept)


class BenchSite:
    """Serves ``tasks`` from loopback; one state dict per task, reset per run."""

    def __init__(self, tasks: Iterable[Task]) -> None:
        self.tasks = {task.id: task for task in tasks}
        self._lock = threading.RLock()
        self._data: dict[str, State] = {}
        self._log: list[Request] = []
        self._servers: dict[str, ThreadingHTTPServer] = {}
        self._threads: list[threading.Thread] = []
        self.urls: Urls | None = None
        self.reset()

    def start(self) -> Urls:
        """Bind the three listeners on ephemeral loopback ports and serve them."""
        for listener in LISTENERS:
            server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler(listener))
            server.daemon_threads = True
            self._servers[listener] = server
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self._threads.append(thread)
        ports = {name: s.server_address[1] for name, s in self._servers.items()}
        self.urls = Urls(*(f"http://127.0.0.1:{ports[name]}" for name in LISTENERS))
        return self.urls

    def port(self, listener: str) -> int:
        return self._servers[listener].server_address[1]

    def close(self) -> None:
        for server in self._servers.values():
            server.shutdown()
            server.server_close()
        self._servers.clear()

    def reset(self, task_id: str | None = None) -> None:
        """Fresh state and an empty request log for one task, or for all of them."""
        with self._lock:
            ids = [task_id] if task_id else list(self.tasks)
            for tid in ids:
                self._data[tid] = self.tasks[tid].initial()
            if task_id is None:
                self._log.clear()
            else:
                prefix = f"/{task_id}/"
                self._log = [r for r in self._log if not r.path.startswith(prefix)]

    def state(self, task_id: str) -> State:
        """A deep copy of the task's data and its requests, paths relative to its prefix."""
        prefix = f"/{task_id}"
        with self._lock:
            requests = []
            for r in self._log:
                if r.path == prefix or r.path.startswith(prefix + "/"):
                    entry = r.as_dict()
                    entry["path"] = r.path[len(prefix) :] or "/"
                    requests.append(entry)
            return {"data": copy.deepcopy(self._data[task_id]), "requests": requests}

    def unattributed(self) -> list[dict]:
        """Requests whose path names no task, for spotting stray traffic."""
        with self._lock:
            return [
                r.as_dict()
                for r in self._log
                if r.path.split("/")[1:2] and r.path.split("/")[1] not in self.tasks
            ]

    def url(self, task: Task, path: str | None = None, listener: str = "main") -> str:
        return f"{self.urls.of(listener)}{task.prefix}{task.start if path is None else path}"

    def dispatch(self, request: Request) -> Response:
        """Record ``request`` and run the matching route under the site lock."""
        with self._lock:
            self._log.append(request)
            segments = request.path.split("/", 2)
            task = self.tasks.get(segments[1]) if len(segments) > 1 else None
            if task is None or self.urls is None:
                return NOT_FOUND
            rest = "/" + (segments[2] if len(segments) > 2 else "")
            for route in task.routes:
                if (
                    route.path == rest
                    and route.method == request.method
                    and request.listener in route.listeners
                ):
                    ctx = Ctx(request, self._data[task.id], self.urls, task.prefix)
                    return route.handler(ctx)
            return NOT_FOUND

    def _handler(self, listener: str) -> type[BaseHTTPRequestHandler]:
        site = self

        class Handler(BaseHTTPRequestHandler):
            def _serve(self) -> None:
                length = min(int(self.headers.get("Content-Length") or 0), MAX_BODY)
                body = self.rfile.read(length) if length else b""
                headers = {k.lower(): v for k, v in self.headers.items()}
                request = parse_request(
                    listener, self.command, self.path, headers, body
                )
                response = site.dispatch(request)
                self.send_response(response.status)
                self.send_header("Content-Type", response.content_type)
                self.send_header("Content-Length", str(len(response.body)))
                self.send_header("Cache-Control", "no-store")
                for key, value in response.headers.items():
                    self.send_header(key, value)
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(response.body)

            do_GET = do_POST = do_HEAD = _serve

            def log_message(self, format: str, *args: object) -> None:
                return None

        return Handler


@contextmanager
def running_site(tasks: Iterable[Task] | None = None) -> Iterator[BenchSite]:
    """Start the site for ``tasks`` (default: every task) and stop it on exit."""
    site = BenchSite(TASKS if tasks is None else tasks)
    site.start()
    try:
        yield site
    finally:
        site.close()
