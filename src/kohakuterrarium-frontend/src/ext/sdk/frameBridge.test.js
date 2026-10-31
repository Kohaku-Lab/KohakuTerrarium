import { describe, expect, it, vi } from "vitest"

import {
  FRAME_CLIENT_SOURCE,
  FRAME_MESSAGES as M,
  FRAME_PROTOCOL,
  createFrameBridge,
  isSafeApiPath,
} from "@/ext/sdk/frameBridge"

/** A frame window that records what the host posts to it. */
function fakeFrame() {
  const win = { posted: [], postMessage: (m) => win.posted.push(m) }
  return win
}

function setup(handlers = {}) {
  const win = fakeFrame()
  const bridge = createFrameBridge({ target: () => win, handlers, listenOn: null })
  const send = (data, source = win) =>
    bridge.handle({ source, data: { protocol: FRAME_PROTOCOL, ...data } })
  return { win, bridge, send }
}

describe("isSafeApiPath", () => {
  it.each(["state", "a/b", "a/b?x=1", "items/1#top"])("accepts %s", (path) => {
    expect(isSafeApiPath(path)).toBe(true)
  })
  it.each([
    "",
    "../x",
    "a/../x",
    "./x",
    "//evil.test/x",
    "https://evil.test/x",
    "javascript:alert(1)",
    "a\\b",
    5,
    null,
    "x".repeat(2049),
  ])("refuses %s", (path) => {
    expect(isSafeApiPath(path)).toBe(false)
  })
})

describe("frame bridge (dashboard side)", () => {
  it("answers hello with the context and ignores other windows and other protocols", () => {
    const { win, bridge, send } = setup({ context: () => ({ package: "alpha", dark: true }) })
    send({ type: M.HELLO })
    expect(win.posted).toEqual([
      { protocol: FRAME_PROTOCOL, type: M.CONTEXT, context: { package: "alpha", dark: true } },
    ])
    win.posted.length = 0
    send({ type: M.HELLO }, { other: true })
    bridge.handle({ source: win, data: { type: M.HELLO } })
    bridge.handle({ source: win, data: { protocol: "other/1", type: M.HELLO } })
    bridge.handle({ source: win, data: "text" })
    bridge.handle(null)
    expect(win.posted).toEqual([])
  })

  it("proxies an api request and returns data or a typed failure", async () => {
    const api = vi.fn(async ({ method, path }) => {
      if (path === "boom") {
        const err = new Error("nope")
        err.response = { status: 409, data: { detail: { error: "conflict", message: "taken" } } }
        throw err
      }
      return { status: 200, data: { method, path } }
    })
    const { win, send } = setup({ api })
    send({ type: M.API_REQUEST, id: "r1", method: "post", path: "items", data: { n: 1 } })
    await vi.waitFor(() => expect(win.posted).toHaveLength(1))
    expect(api).toHaveBeenCalledWith({ method: "POST", path: "items", data: { n: 1 } })
    expect(win.posted[0]).toMatchObject({
      type: M.API_RESPONSE,
      id: "r1",
      ok: true,
      status: 200,
      data: { method: "POST", path: "items" },
    })

    send({ type: M.API_REQUEST, id: "r2", path: "boom" })
    await vi.waitFor(() => expect(win.posted).toHaveLength(2))
    expect(win.posted[1]).toMatchObject({
      id: "r2",
      ok: false,
      status: 409,
      error: { code: "conflict", message: "taken" },
    })
  })

  it("refuses paths outside the package API and unknown methods without calling out", async () => {
    const api = vi.fn()
    const { win, send } = setup({ api })
    send({ type: M.API_REQUEST, id: "a", path: "../../admin" })
    send({ type: M.API_REQUEST, id: "b", path: "https://evil.test" })
    send({ type: M.API_REQUEST, id: "c", path: "ok", method: "TRACE" })
    expect(api).not.toHaveBeenCalled()
    expect(win.posted.map((m) => [m.id, m.ok, m.status])).toEqual([
      ["a", false, 400],
      ["b", false, 400],
      ["c", false, 400],
    ])
    send({ type: M.API_REQUEST, path: "ok" })
    expect(win.posted).toHaveLength(3)
  })

  it("limits the requests in flight", async () => {
    let release
    const gate = new Promise((resolve) => (release = resolve))
    const { win, send } = setup({ api: () => gate.then(() => ({ status: 200, data: 1 })) })
    for (let i = 0; i < 33; i += 1) send({ type: M.API_REQUEST, id: `r${i}`, path: "x" })
    expect(win.posted).toHaveLength(1)
    expect(win.posted[0]).toMatchObject({ id: "r32", ok: false, status: 429 })
    release()
    await vi.waitFor(() => expect(win.posted).toHaveLength(33))
  })

  it("forwards owner calls, notifications, tab opens, resizes and replies with validation", async () => {
    const handlers = {
      ownerCall: vi.fn(async (action, args) => ({ action, args })),
      notify: vi.fn(),
      openTab: vi.fn(),
      resize: vi.fn(),
      say: vi.fn(),
    }
    const { win, send } = setup(handlers)
    send({ type: M.OWNER_CALL, id: "o1", action: "reset", args: { a: 1 } })
    await vi.waitFor(() => expect(win.posted).toHaveLength(1))
    expect(win.posted[0]).toMatchObject({
      id: "o1",
      ok: true,
      data: { action: "reset", args: { a: 1 } },
    })

    send({ type: M.OWNER_CALL, id: "o2" })
    expect(handlers.ownerCall).toHaveBeenCalledTimes(1)

    send({ type: M.NOTIFY, message: "x".repeat(900), kind: "bogus" })
    expect(handlers.notify).toHaveBeenCalledWith("x".repeat(500), "info")
    send({ type: M.NOTIFY, message: 5 })
    expect(handlers.notify).toHaveBeenCalledTimes(1)

    send({ type: M.OPEN_TAB, tab: "board" })
    send({ type: M.OPEN_TAB, tab: "../x" })
    expect(handlers.openTab.mock.calls).toEqual([["board"]])

    send({ type: M.RESIZE, height: 5 })
    send({ type: M.RESIZE, height: 99999 })
    send({ type: M.RESIZE, height: "tall" })
    expect(handlers.resize.mock.calls).toEqual([[16], [4000]])

    send({ type: M.SAY, text: "hello" })
    send({ type: M.SAY, text: "" })
    expect(handlers.say.mock.calls).toEqual([["hello"]])
  })

  it("ignores a message whose handler is missing", () => {
    const { win, send } = setup({})
    for (const type of [M.NOTIFY, M.OPEN_TAB, M.RESIZE, M.SAY]) {
      send({ type, message: "m", tab: "t", height: 40, text: "s" })
    }
    send({ type: M.OWNER_CALL, id: "x", action: "a" })
    send({ type: M.API_REQUEST, id: "y", path: "p" })
    expect(win.posted).toEqual([])
  })

  it("pushes owner state and events as cloneable data", () => {
    const { win, bridge } = setup({})
    bridge.postOwnerState({ turns: 3, fn() {} })
    bridge.postOwnerEvent("tick", { n: 1 })
    expect(win.posted).toEqual([
      { protocol: FRAME_PROTOCOL, type: M.OWNER_STATE, state: { turns: 3 } },
      { protocol: FRAME_PROTOCOL, type: M.OWNER_EVENT, event: "tick", data: { n: 1 } },
    ])
  })

  it("posts nothing once the frame window is gone", () => {
    const bridge = createFrameBridge({ target: () => null, handlers: {}, listenOn: null })
    expect(() => bridge.postContext()).not.toThrow()
  })

  it("listens on the given window until stopped", () => {
    const listenOn = { addEventListener: vi.fn(), removeEventListener: vi.fn() }
    const bridge = createFrameBridge({ target: () => null, handlers: {}, listenOn })
    bridge.start()
    bridge.start()
    expect(listenOn.addEventListener).toHaveBeenCalledTimes(1)
    bridge.stop()
    bridge.stop()
    expect(listenOn.removeEventListener).toHaveBeenCalledTimes(1)
  })
})

/** Run the injected client against a bridge, with two fake windows posting to each other. */
function connect(handlers) {
  const style = new Map()
  const attrs = new Map()
  const documentStub = {
    documentElement: {
      style: { setProperty: (k, v) => style.set(k, v), colorScheme: "" },
      setAttribute: (k, v) => attrs.set(k, v),
      scrollHeight: 120,
    },
  }
  const listeners = []
  const frameWindow = {
    addEventListener: (type, fn) => type === "message" && listeners.push(fn),
  }
  const host = {}
  let bridge
  const parent = {
    postMessage: (data) => queueMicrotask(() => bridge.handle({ source: frameWin, data })),
  }
  const frameWin = {
    postMessage: (data) =>
      queueMicrotask(() => listeners.forEach((fn) => fn({ source: parent, data }))),
  }
  bridge = createFrameBridge({ target: () => frameWin, handlers, listenOn: null })
  const run = new Function("window", "parent", "document", "ResizeObserver", FRAME_CLIENT_SOURCE)
  run(frameWindow, parent, documentStub, undefined)
  return { kt: frameWindow.kt, bridge, style, attrs, host }
}

describe("frame client script (inside the frame)", () => {
  it("receives the context, applies the theme and resolves ready", async () => {
    const { kt, style, attrs } = connect({
      context: () => ({
        package: "alpha",
        dark: true,
        tokens: { "--color-bg": "#000" },
        subject: { kind: "file", path: "a.csv" },
      }),
    })
    const ctx = await kt.ready
    expect(ctx.package).toBe("alpha")
    expect(kt.context.subject).toEqual({ kind: "file", path: "a.csv" })
    expect(style.get("--color-bg")).toBe("#000")
    expect(attrs.get("data-theme")).toBe("dark")
    const seen = vi.fn()
    kt.onContext(seen)
    expect(seen).toHaveBeenCalledWith(ctx)
  })

  it("round-trips api and owner calls and surfaces failures as errors", async () => {
    const { kt } = connect({
      context: () => ({ package: "alpha" }),
      api: async ({ method, path, data }) => {
        if (path === "bad")
          throw Object.assign(new Error("denied"), { code: "forbidden", status: 403 })
        return { status: 200, data: { method, path, data } }
      },
      ownerCall: async (action) => ({ done: action }),
    })
    await kt.ready
    expect(await kt.api.post("things", { a: 1 })).toEqual({
      method: "POST",
      path: "things",
      data: { a: 1 },
    })
    expect(await kt.api.get("things")).toEqual({ method: "GET", path: "things", data: null })
    expect(await kt.owner.call("reset")).toEqual({ done: "reset" })
    await expect(kt.api.get("bad")).rejects.toMatchObject({
      message: "denied",
      code: "forbidden",
      status: 403,
    })
    await expect(kt.api.get("../out")).rejects.toMatchObject({ code: "bad_request", status: 400 })
  })

  it("delivers owner state and events and sends its own messages up", async () => {
    const calls = []
    const { kt, bridge } = connect({
      context: () => ({ package: "alpha" }),
      notify: (m, t) => calls.push(["notify", m, t]),
      openTab: (t) => calls.push(["tab", t]),
      say: (t) => calls.push(["say", t]),
      resize: (h) => calls.push(["resize", h]),
    })
    await kt.ready
    const states = []
    const events = []
    kt.owner.onState((s) => states.push(s))
    kt.owner.on("tick", (data, event) => events.push([event, data]))
    bridge.postOwnerState({ turns: 1 })
    bridge.postOwnerEvent("tick", { n: 2 })
    bridge.postOwnerEvent("other", { n: 9 })
    await vi.waitFor(() => expect(events).toHaveLength(1))
    expect(states).toEqual([{ turns: 1 }])
    expect(kt.owner.state).toEqual({ turns: 1 })
    expect(events).toEqual([["tick", { n: 2 }]])

    const late = vi.fn()
    kt.owner.onState(late)
    expect(late).toHaveBeenCalledWith({ turns: 1 })

    kt.notify("saved", "success")
    kt.openTab("board")
    kt.say("picked")
    kt.resize(77)
    await vi.waitFor(() => expect(calls).toHaveLength(4))
    expect(calls).toEqual([
      ["notify", "saved", "success"],
      ["tab", "board"],
      ["say", "picked"],
      ["resize", 77],
    ])
  })
})
