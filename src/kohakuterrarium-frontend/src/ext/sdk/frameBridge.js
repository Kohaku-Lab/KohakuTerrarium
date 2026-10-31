/**
 * ``isolation: "frame"`` contributions: a package page in a sandboxed iframe
 * that talks to the dashboard only through ``postMessage``.
 *
 * ``createFrameBridge`` is the dashboard side: it answers the messages a frame
 * may send and nothing else. ``FRAME_CLIENT_SOURCE`` is the classic script the
 * dashboard injects into the frame; it defines ``window.kt`` there. Both speak
 * ``FRAME_PROTOCOL``.
 */

export const FRAME_PROTOCOL = "kt-ui-frame/1"

export const FRAME_MESSAGES = Object.freeze({
  HELLO: "kt:frame:hello",
  READY: "kt:frame:ready",
  CONTEXT: "kt:frame:context",
  API_REQUEST: "kt:frame:api-request",
  API_RESPONSE: "kt:frame:api-response",
  NOTIFY: "kt:frame:notify",
  OPEN_TAB: "kt:frame:open-tab",
  RESIZE: "kt:frame:resize",
  OWNER_CALL: "kt:frame:owner-call",
  OWNER_STATE: "kt:frame:owner-state",
  OWNER_EVENT: "kt:frame:owner-event",
  SAY: "kt:frame:say",
})

export const FRAME_SUPPORTED = true

export class FrameIsolationUnsupportedError extends Error {
  constructor(reason = `a frame bridge needs a target window and handlers`) {
    super(`${FRAME_PROTOCOL}: ${reason}`)
    this.name = "FrameIsolationUnsupportedError"
  }
}

const METHODS = new Set(["GET", "POST", "PUT", "PATCH", "DELETE"])
const NOTIFY_TYPES = new Set(["info", "success", "warning", "error"])
const MAX_PENDING = 32
const MAX_PATH = 2048
const MAX_MESSAGE = 500
const MIN_HEIGHT = 16
const MAX_HEIGHT = 4000
const TAB_ID = /^[a-z0-9][a-z0-9._-]{0,63}$/

/** Whether ``path`` is a relative path inside the package's own API prefix. */
export function isSafeApiPath(path) {
  if (typeof path !== "string" || !path || path.length > MAX_PATH) return false
  if (/^[a-z][a-z0-9+.-]*:/i.test(path) || path.startsWith("//") || path.includes("\\"))
    return false
  const trimmed = path.replace(/[?#].*$/, "")
  return !trimmed.split("/").some((part) => part === ".." || part === ".")
}

function clone(value) {
  if (value === undefined) return null
  try {
    return JSON.parse(JSON.stringify(value))
  } catch (_err) {
    return null
  }
}

function failure(err) {
  const detail = err?.response?.data?.detail
  return {
    ok: false,
    status: err?.response?.status ?? err?.status ?? 0,
    error: {
      code: String(detail?.error ?? err?.code ?? "failed"),
      message: String(detail?.message ?? err?.message ?? err ?? "failed"),
    },
  }
}

/**
 * The dashboard end of a frame. ``target()`` returns the frame's window;
 * ``handlers`` are `context()`, `api({method, path, data})`,
 * `ownerCall(action, args)`, `notify(message, type)`, `openTab(id)`,
 * `resize(height)` and `say(text)`; any may be omitted, and a message whose
 * handler is missing is ignored. Only messages sent by ``target()`` and
 * carrying ``FRAME_PROTOCOL`` are read.
 */
export function createFrameBridge(options) {
  if (!options || typeof options.target !== "function" || !options.handlers) {
    throw new FrameIsolationUnsupportedError()
  }
  const { target, handlers } = options
  const listenOn = options.listenOn || (typeof window === "undefined" ? null : window)
  let pending = 0
  let started = false

  function post(type, body = {}) {
    const win = target()
    if (!win) return
    win.postMessage({ protocol: FRAME_PROTOCOL, type, ...body }, "*")
  }

  async function answer(id, work) {
    if (pending >= MAX_PENDING) {
      post(FRAME_MESSAGES.API_RESPONSE, {
        id,
        ok: false,
        status: 429,
        error: { code: "too_many_requests", message: "too many requests in flight" },
      })
      return
    }
    pending += 1
    try {
      const result = await work()
      post(FRAME_MESSAGES.API_RESPONSE, {
        id,
        ok: true,
        status: result?.status ?? 200,
        data: clone(result?.data),
      })
    } catch (err) {
      post(FRAME_MESSAGES.API_RESPONSE, { id, ...failure(err) })
    } finally {
      pending -= 1
    }
  }

  function onMessage(event) {
    if (!event || event.source !== target()) return
    const m = event.data
    if (!m || typeof m !== "object" || m.protocol !== FRAME_PROTOCOL) return
    const M = FRAME_MESSAGES
    switch (m.type) {
      case M.HELLO:
        postContext()
        handlers.hello?.()
        break
      case M.API_REQUEST: {
        const method = String(m.method || "GET").toUpperCase()
        if (typeof m.id !== "string" || !handlers.api) return
        if (!METHODS.has(method) || !isSafeApiPath(m.path)) {
          post(M.API_RESPONSE, {
            id: m.id,
            ok: false,
            status: 400,
            error: { code: "bad_request", message: "the path must stay inside the package API" },
          })
          return
        }
        void answer(m.id, () => handlers.api({ method, path: m.path, data: clone(m.data) }))
        break
      }
      case M.OWNER_CALL:
        if (typeof m.id !== "string" || typeof m.action !== "string" || !handlers.ownerCall) return
        void answer(m.id, async () => ({
          status: 200,
          data: await handlers.ownerCall(m.action, clone(m.args) ?? {}),
        }))
        break
      case M.NOTIFY:
        if (typeof m.message === "string" && handlers.notify) {
          handlers.notify(
            m.message.slice(0, MAX_MESSAGE),
            NOTIFY_TYPES.has(m.kind) ? m.kind : "info",
          )
        }
        break
      case M.OPEN_TAB:
        if (typeof m.tab === "string" && TAB_ID.test(m.tab)) handlers.openTab?.(m.tab)
        break
      case M.RESIZE:
        if (Number.isFinite(m.height)) {
          handlers.resize?.(Math.min(MAX_HEIGHT, Math.max(MIN_HEIGHT, Math.round(m.height))))
        }
        break
      case M.SAY:
        if (typeof m.text === "string" && m.text && handlers.say) {
          handlers.say(m.text.slice(0, 20000))
        }
        break
      default:
    }
  }

  function postContext() {
    post(FRAME_MESSAGES.CONTEXT, { context: clone(handlers.context?.() ?? {}) })
  }

  return {
    start() {
      if (started || !listenOn) return
      started = true
      listenOn.addEventListener("message", onMessage)
    },
    stop() {
      if (!started) return
      started = false
      listenOn.removeEventListener("message", onMessage)
    },
    postContext,
    postOwnerState: (state) => post(FRAME_MESSAGES.OWNER_STATE, { state: clone(state) }),
    postOwnerEvent: (event, data) => post(FRAME_MESSAGES.OWNER_EVENT, { event, data: clone(data) }),
    /** Deliver a message as if the frame had sent it; for tests. */
    handle: onMessage,
  }
}

/**
 * The script the dashboard injects into a frame. It defines ``window.kt``:
 * ``package``, ``context`` (theme tokens, locale, ``subject``), ``ready``,
 * ``api.get/post/put/patch/delete/request``, ``owner.call/state/on/onState``,
 * ``notify``, ``openTab``, ``say``, ``resize`` and ``onContext``.
 */
export const FRAME_CLIENT_SOURCE = `(function () {
  var P = ${JSON.stringify(FRAME_PROTOCOL)};
  var M = ${JSON.stringify(FRAME_MESSAGES)};
  var pending = {}, seq = 0, ctx = {}, ctxFns = [], stateFns = [], state = null, evFns = {};
  var resolveReady, ready = new Promise(function (r) { resolveReady = r; });
  function send(type, body) {
    var m = body || {};
    m.protocol = P; m.type = type;
    parent.postMessage(m, "*");
  }
  function request(type, body) {
    return new Promise(function (resolve, reject) {
      var id = "r" + (++seq);
      var timer = setTimeout(function () { delete pending[id]; reject(new Error("request timed out")); }, 30000);
      pending[id] = { resolve: resolve, reject: reject, timer: timer };
      body.id = id;
      send(type, body);
    });
  }
  function applyTheme() {
    var root = document.documentElement, tokens = ctx.tokens || {};
    for (var name in tokens) root.style.setProperty(name, tokens[name]);
    root.setAttribute("data-theme", ctx.dark ? "dark" : "light");
    root.style.colorScheme = ctx.dark ? "dark" : "light";
  }
  window.addEventListener("message", function (e) {
    if (e.source !== parent) return;
    var m = e.data;
    if (!m || m.protocol !== P) return;
    if (m.type === M.CONTEXT) {
      ctx = m.context || {}; kt.context = ctx; applyTheme();
      ctxFns.forEach(function (fn) { fn(ctx); });
      resolveReady(ctx);
    } else if (m.type === M.API_RESPONSE) {
      var p = pending[m.id];
      if (!p) return;
      clearTimeout(p.timer); delete pending[m.id];
      if (m.ok) p.resolve(m.data);
      else {
        var err = new Error(m.error && m.error.message || "request failed");
        err.code = m.error && m.error.code; err.status = m.status;
        p.reject(err);
      }
    } else if (m.type === M.OWNER_STATE) {
      state = m.state; kt.owner.state = state;
      stateFns.forEach(function (fn) { fn(state); });
    } else if (m.type === M.OWNER_EVENT) {
      (evFns[m.event] || []).concat(evFns["*"] || []).forEach(function (fn) { fn(m.data, m.event); });
    }
  });
  function api(method) {
    return function (path, data) {
      return request(M.API_REQUEST, { method: method, path: path, data: data === undefined ? null : data });
    };
  }
  var kt = window.kt = {
    protocol: P,
    context: ctx,
    ready: ready,
    api: { get: api("GET"), post: api("POST"), put: api("PUT"), patch: api("PATCH"), delete: api("DELETE"),
      request: function (c) { return request(M.API_REQUEST, { method: c.method || "GET", path: c.path, data: c.data === undefined ? null : c.data }); } },
    owner: {
      state: null,
      call: function (action, args) { return request(M.OWNER_CALL, { action: action, args: args || {} }); },
      onState: function (fn) { stateFns.push(fn); if (state !== null) fn(state); },
      on: function (event, fn) { (evFns[event] = evFns[event] || []).push(fn); }
    },
    notify: function (message, kind) { send(M.NOTIFY, { message: String(message), kind: kind || "info" }); },
    openTab: function (tab) { send(M.OPEN_TAB, { tab: String(tab) }); },
    say: function (text) { send(M.SAY, { text: String(text) }); },
    resize: function (height) { send(M.RESIZE, { height: Number(height) }); },
    onContext: function (fn) { ctxFns.push(fn); if (ctx.package) fn(ctx); }
  };
  if (typeof ResizeObserver !== "undefined") {
    new ResizeObserver(function () { kt.resize(document.documentElement.scrollHeight); }).observe(document.documentElement);
  }
  send(M.HELLO, {});
})();`
