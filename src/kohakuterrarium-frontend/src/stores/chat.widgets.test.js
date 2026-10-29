import { createPinia, setActivePinia } from "pinia"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import { _replayEvents, useChatStore } from "./chat.js"
import { registerWidgetRenderer, unregisterWidgetRenderer } from "@/ext/widgets"

let storage
beforeEach(() => {
  storage = new Map()
  vi.stubGlobal("localStorage", {
    getItem: (k) => (storage.has(k) ? storage.get(k) : null),
    setItem: (k, v) => storage.set(k, String(v)),
    removeItem: (k) => storage.delete(k),
    clear: () => storage.clear(),
    get length() {
      return storage.size
    },
    key: (i) => Array.from(storage.keys())[i] ?? null,
  })
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.useRealTimers()
})

function store() {
  const chat = useChatStore()
  chat.messagesByTab = { main: [] }
  chat.tabs = ["main"]
  const sent = []
  chat._ws = { readyState: WebSocket.OPEN, send: (raw) => sent.push(JSON.parse(raw)) }
  return { chat, sent }
}

const legacyConfirm = {
  type: "widget",
  source: "main",
  event_id: "w_c1",
  surface: "chat",
  interactive: true,
  payload: {
    renderer: "kt.confirm",
    version: 1,
    title: "",
    spec: { prompt: "Allow bash?", options: [{ id: "allow" }, { id: "deny" }] },
    state: {},
    fallback: "Allow bash?",
    routing: "drop",
  },
}

describe("chat store: legacy kinds as widget frames", () => {
  it("renders a legacy widget as a ui_event message with the spec as payload", () => {
    const { chat } = store()
    chat._onMessage(legacyConfirm)
    const [msg] = chat.messagesByTab.main
    expect(msg.role).toBe("ui_event")
    expect(msg.uiEventType).toBe("confirm")
    expect(msg.eventId).toBe("w_c1")
    expect(msg.payload.prompt).toBe("Allow bash?")
    expect(msg.viaWidget).toBe(true)
    expect(msg.interactive).toBe(true)
  })

  it("replies with widget_msg (name = action id) and marks the message answered", () => {
    const { chat, sent } = store()
    chat._onMessage(legacyConfirm)
    expect(chat.submitUIReply("main", "w_c1", "allow", {})).toBe(true)
    expect(sent).toHaveLength(1)
    expect(sent[0]).toMatchObject({
      type: "widget_msg",
      target: "main",
      event_id: "w_c1",
      name: "allow",
      data: {},
      origin: "renderer",
    })
    expect(sent[0].type).not.toBe("ui_reply")
    const [msg] = chat.messagesByTab.main
    expect(msg.replied).toBe(true)
    expect(msg.repliedActionId).toBe("allow")
  })

  it("still sends ui_reply for an event that arrived as an old frame", () => {
    const { chat, sent } = store()
    chat._onMessage({
      ...legacyConfirm.payload.spec,
      type: "confirm",
      source: "main",
      event_id: "ev_old",
      interactive: true,
      payload: legacyConfirm.payload.spec,
    })
    chat.submitUIReply("main", "ev_old", "deny", {})
    expect(sent[0].type).toBe("ui_reply")
    expect(sent[0].action_id).toBe("deny")
  })

  it("maps widget_ack statuses like ui_reply_ack", () => {
    const { chat } = store()
    chat._onMessage(legacyConfirm)
    const [msg] = chat.messagesByTab.main
    chat.submitUIReply("main", "w_c1", "allow", {})
    chat._onMessage({
      type: "widget_ack",
      source: "main",
      event_id: "w_c1",
      kind: "message",
      status: "rate_limited",
    })
    expect(msg.replied).toBe(false)
    expect(msg.repliedActionId).toBe("")

    chat.submitUIReply("main", "w_c1", "allow", {})
    chat._onMessage({
      type: "widget_ack",
      source: "main",
      event_id: "w_c1",
      kind: "message",
      status: "unknown",
    })
    expect(msg.timedOut).toBe(true)
    expect(msg.replied).toBe(false)
  })

  it("a superseded ack does not undo our own answer", () => {
    const { chat } = store()
    chat._onMessage(legacyConfirm)
    const [msg] = chat.messagesByTab.main
    chat.submitUIReply("main", "w_c1", "allow", {})
    chat._onMessage({
      type: "widget_ack",
      source: "main",
      event_id: "w_c1",
      kind: "message",
      status: "superseded",
    })
    expect(msg.replied).toBe(true)
    expect(msg.superseded).toBe(false)
  })

  it("widget_close marks timed out or answered elsewhere, never over our own answer", () => {
    const { chat } = store()
    chat._onMessage(legacyConfirm)
    chat._onMessage({ ...legacyConfirm, event_id: "w_c2" })
    chat._onMessage({ ...legacyConfirm, event_id: "w_c3" })
    const [one, two, three] = chat.messagesByTab.main
    chat._onMessage({
      type: "widget_close",
      source: "main",
      event_id: "w_c1",
      payload: { reason: "timeout" },
    })
    chat._onMessage({ type: "widget_close", source: "main", event_id: "w_c2" })
    chat.submitUIReply("main", "w_c3", "allow", {})
    chat._onMessage({
      type: "widget_close",
      source: "main",
      event_id: "w_c3",
      payload: { reason: "replied" },
    })
    expect(one.timedOut).toBe(true)
    expect(two.superseded).toBe(true)
    expect(three.replied).toBe(true)
    expect(three.superseded).toBe(false)
  })

  it("a progress update replaces the spec", () => {
    const { chat } = store()
    chat._onMessage({
      type: "widget",
      source: "main",
      event_id: "w_p",
      payload: { renderer: "kt.progress", spec: { label: "indexing", value: 0, max: 10 } },
    })
    chat._onMessage({
      type: "widget",
      source: "main",
      event_id: "w_p",
      update_target: "w_p",
      payload: { spec: { label: "indexing", value: 5, max: 10 } },
    })
    const list = chat.messagesByTab.main
    expect(list).toHaveLength(1)
    expect(list[0].payload).toEqual({ label: "indexing", value: 5, max: 10 })
  })

  it("a toast notification widget does not enter the chat list", () => {
    const { chat } = store()
    chat._onMessage({
      type: "widget",
      source: "main",
      event_id: "w_n",
      surface: "toast",
      payload: { renderer: "kt.notification", spec: { text: "saved", level: "success" } },
    })
    expect(chat.messagesByTab.main).toHaveLength(0)
  })
})

describe("chat store: generic widgets", () => {
  const appFrame = {
    type: "widget",
    source: "main",
    event_id: "w_a",
    surface: "canvas",
    interactive: true,
    payload: {
      renderer: "pkg.board",
      version: 1,
      title: "Board",
      spec: { cells: 9 },
      state: { turn: "x" },
      fallback: "Board (open in web UI)",
      routing: "agent",
    },
  }

  it("adds a widget message and patches it by update_target", () => {
    const { chat } = store()
    chat._onMessage(appFrame)
    const [msg] = chat.messagesByTab.main
    expect(msg).toMatchObject({
      role: "widget",
      eventId: "w_a",
      renderer: "pkg.board",
      title: "Board",
      surface: "canvas",
      interactive: true,
    })
    chat._onMessage({
      type: "widget",
      source: "main",
      event_id: "w_a",
      update_target: "w_a",
      payload: { state: { winner: "o" }, title: "Board 2" },
    })
    expect(msg.state).toEqual({ turn: "x", winner: "o" })
    expect(msg.title).toBe("Board 2")
    expect(msg.spec).toEqual({ cells: 9 })
    chat._onMessage({
      type: "widget",
      source: "main",
      event_id: "w_a",
      update_target: "w_a",
      payload: { spec: { cells: 16 } },
    })
    expect(msg.spec).toEqual({ cells: 16 })
  })

  it("ignores a duplicate open and an update for an unknown widget", () => {
    const { chat } = store()
    chat._onMessage(appFrame)
    chat._onMessage(appFrame)
    chat._onMessage({
      type: "widget",
      source: "main",
      update_target: "w_missing",
      payload: { state: {} },
    })
    expect(chat.messagesByTab.main).toHaveLength(1)
  })

  it("widget_state replaces state, widget_close closes, widget_ack is kept", () => {
    const { chat } = store()
    chat._onMessage(appFrame)
    const [msg] = chat.messagesByTab.main
    chat._onMessage({ type: "widget_state", source: "main", event_id: "w_a", state: { turn: "o" } })
    expect(msg.state).toEqual({ turn: "o" })
    chat._onMessage({
      type: "widget_ack",
      source: "main",
      event_id: "w_a",
      kind: "message",
      status: "rate_limited",
    })
    expect(msg.lastAck).toEqual({ kind: "message", status: "rate_limited" })
    chat._onMessage({ type: "widget_close", source: "main", event_id: "w_a" })
    expect(msg.closed).toBe(true)
  })

  it("sends widget_msg and debounces widget_state per widget", () => {
    vi.useFakeTimers()
    const { chat, sent } = store()
    chat._onMessage(appFrame)
    chat.submitWidgetMessage("main", "w_a", "move", { cell: 4 })
    expect(sent.at(-1)).toMatchObject({
      type: "widget_msg",
      event_id: "w_a",
      name: "move",
      data: { cell: 4 },
      origin: "renderer",
    })

    chat.setWidgetState("main", "w_a", { a: 1 })
    chat.setWidgetState("main", "w_a", { b: 2 })
    expect(chat.messagesByTab.main[0].state).toMatchObject({ a: 1, b: 2 })
    expect(sent.filter((f) => f.type === "widget_state")).toHaveLength(0)
    vi.advanceTimersByTime(499)
    expect(sent.filter((f) => f.type === "widget_state")).toHaveLength(0)
    vi.advanceTimersByTime(1)
    const states = sent.filter((f) => f.type === "widget_state")
    expect(states).toEqual([
      expect.objectContaining({ target: "main", event_id: "w_a", state: { a: 1, b: 2 } }),
    ])
  })

  it("origin is only ever renderer or app", () => {
    const { chat, sent } = store()
    chat.submitWidgetMessage("main", "w_a", "x", {}, "app")
    chat.submitWidgetMessage("main", "w_a", "x", {}, "admin")
    expect(sent.map((f) => f.origin)).toEqual(["app", "renderer"])
  })
})

describe("chat store: client_hello", () => {
  function installFakeWebSocket() {
    const sockets = []
    class FakeWebSocket {
      static CONNECTING = 0
      static OPEN = 1
      static CLOSING = 2
      static CLOSED = 3
      constructor(url) {
        this.url = url
        this.readyState = FakeWebSocket.CONNECTING
        this.sent = []
        this.send = (raw) => this.sent.push(JSON.parse(raw))
        this.close = vi.fn()
        sockets.push(this)
      }
    }
    vi.stubGlobal("WebSocket", FakeWebSocket)
    return sockets
  }

  it("announces renderers on open before onOpen runs, then again on registry change", async () => {
    vi.useFakeTimers()
    const sockets = installFakeWebSocket()
    const chat = useChatStore()
    const order = []
    chat._openWs({
      generation: chat._instanceGeneration,
      url: "ws://x",
      onOpen: () => order.push("onOpen"),
      reconnect: vi.fn(),
    })
    const ws = sockets[0]
    const originalSend = ws.send
    ws.send = (raw) => {
      order.push(JSON.parse(raw).type)
      originalSend(raw)
    }
    ws.readyState = 1
    ws.onopen()
    expect(order).toEqual(["client_hello", "onOpen"])
    const hello = ws.sent[0]
    expect(hello).toMatchObject({ type: "client_hello", protocol: 1 })
    expect(Object.keys(hello.renderers).some((id) => id.startsWith("kt.card"))).toBe(false)

    registerWidgetRenderer("test.board", { render: () => null }, { version: 2 })
    await Promise.resolve()
    vi.advanceTimersByTime(299)
    expect(ws.sent.filter((f) => f.type === "client_hello")).toHaveLength(1)
    vi.advanceTimersByTime(1)
    const hellos = ws.sent.filter((f) => f.type === "client_hello")
    expect(hellos).toHaveLength(2)
    expect(hellos[1].renderers["test.board"]).toBe(2)
    unregisterWidgetRenderer("test.board")
    chat._helloWatchStop?.()
  })
})

describe("history replay: old and new widget rows", () => {
  it("rebuilds old UI events and new widget rows in order", () => {
    const events = [
      {
        type: "card",
        ui_event_id: "card_old",
        interactive: true,
        payload: { title: "Old card", actions: [{ id: "ok" }] },
      },
      { type: "ui_supersede", ui_event_id: "card_old" },
      {
        type: "widget",
        widget_id: "w_conf",
        renderer: "kt.confirm",
        version: 1,
        surface: "chat",
        interactive: true,
        title: "",
        spec: { prompt: "Proceed?", options: [{ id: "yes" }] },
        state: {},
        fallback: "Proceed?",
        routing: "drop",
      },
      {
        type: "widget_message",
        widget_id: "w_conf",
        name: "yes",
        data: {},
        origin: "renderer",
        routed_to: "waiter",
      },
      { type: "widget_close", widget_id: "w_conf" },
      {
        type: "widget",
        widget_id: "w_gen",
        renderer: "pkg.board",
        version: 1,
        surface: "chat",
        interactive: true,
        title: "Board",
        spec: { cells: 9 },
        state: { turn: "x" },
        fallback: "Board",
        routing: "agent",
      },
      {
        type: "widget_update",
        widget_id: "w_gen",
        patch: { state: { score: 1 }, title: "Board v2" },
      },
      { type: "widget_state", widget_id: "w_gen", state: { turn: "o", score: 1 } },
      { type: "widget_close", widget_id: "w_gen" },
    ]
    const { messages } = _replayEvents([], events, null, "main")
    const [oldCard, confirm, generic] = messages
    expect(oldCard).toMatchObject({ role: "ui_event", uiEventType: "card", superseded: true })
    expect(confirm).toMatchObject({
      role: "ui_event",
      uiEventType: "confirm",
      eventId: "w_conf",
      replied: true,
      repliedActionId: "yes",
      superseded: false,
    })
    expect(generic).toMatchObject({
      role: "widget",
      eventId: "w_gen",
      title: "Board v2",
      closed: true,
    })
    expect(generic.state).toEqual({ turn: "o", score: 1 })
  })

  it("one message when a legacy event is recorded in both the old and the widget shape", () => {
    const events = [
      { type: "confirm", ui_event_id: "w_dup", interactive: true, payload: { prompt: "Go?" } },
      {
        type: "widget",
        widget_id: "w_dup",
        renderer: "kt.confirm",
        interactive: true,
        spec: { prompt: "Go?" },
      },
      { type: "widget_message", widget_id: "w_dup", name: "yes", data: {} },
    ]
    const { messages } = _replayEvents([], events, null, "main")
    expect(messages).toHaveLength(1)
    expect(messages[0]).toMatchObject({
      role: "ui_event",
      replied: true,
      repliedActionId: "yes",
      viaWidget: true,
    })
  })

  it("ignores rows for widgets it never saw opened", () => {
    const { messages } = _replayEvents(
      [],
      [{ type: "widget_state", widget_id: "w_x", state: { a: 1 } }],
      null,
      "main",
    )
    expect(messages).toHaveLength(0)
  })
})
