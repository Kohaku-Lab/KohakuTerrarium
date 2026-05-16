import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { useChatStore } from "./chat.js"
import { useClusterStore } from "./cluster.js"
import { useNotificationsStore } from "./notifications.js"

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

describe("chat store — Phase B UI event dispatch", () => {
  it("appends a ui_event message when a confirm event arrives", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [] }
    chat.tabs = ["main"]

    chat._onMessage({
      type: "confirm",
      source: "main",
      event_id: "ev_1",
      interactive: true,
      surface: "modal",
      payload: {
        prompt: "Allow bash?",
        options: [
          { id: "allow", label: "Allow", style: "primary" },
          { id: "deny", label: "Deny", style: "danger" },
        ],
      },
    })

    expect(chat.messagesByTab.main.length).toBe(1)
    const msg = chat.messagesByTab.main[0]
    expect(msg.role).toBe("ui_event")
    expect(msg.uiEventType).toBe("confirm")
    expect(msg.eventId).toBe("ev_1")
    expect(msg.interactive).toBe(true)
    expect(msg.replied).toBe(false)
    expect(msg.payload.options.length).toBe(2)
  })

  it("mutates an existing progress message when update_target matches", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [] }
    chat.tabs = ["main"]

    chat._onMessage({
      type: "progress",
      source: "main",
      event_id: "bar_1",
      payload: { label: "indexing", value: 0, max: 100 },
    })
    chat._onMessage({
      type: "progress",
      source: "main",
      update_target: "bar_1",
      payload: { value: 50 },
    })

    const list = chat.messagesByTab.main
    expect(list.length).toBe(1)
    expect(list[0].payload.value).toBe(50)
    expect(list[0].payload.max).toBe(100)
    expect(list[0].payload.label).toBe("indexing")
  })

  it("wire_inbound carries crossNode=true when activity has cross_node flag", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [] }
    chat.tabs = ["main"]

    chat._onMessage({
      type: "activity",
      source: "main",
      activity_type: "wire_inbound",
      from: "alice",
      to: "main",
      content_preview: "hello",
      with_content: true,
      turn_index: 1,
      cross_node: true,
    })

    const list = chat.messagesByTab.main
    expect(list.length).toBe(1)
    expect(list[0].role).toBe("wire_inbound")
    expect(list[0].crossNode).toBe(true)
    expect(list[0].from).toBe("alice")
  })

  it("wire_inbound carries crossNode=false when flag is absent", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [] }
    chat.tabs = ["main"]

    chat._onMessage({
      type: "activity",
      source: "main",
      activity_type: "wire_inbound",
      from: "alice",
      to: "main",
      content_preview: "hello",
      with_content: true,
      turn_index: 1,
    })

    expect(chat.messagesByTab.main[0].crossNode).toBe(false)
  })

  it("_notifyWorkerDisconnect fires toast + markSiteOffline for worker session", () => {
    const chat = useChatStore()
    chat.sessionInfo = { ...chat.sessionInfo, homeNode: "worker-1" }
    const cluster = useClusterStore()
    cluster.$patch({
      mode: "lab-host",
      sites: [{ nodeId: "worker-1", isHost: false, status: "online", creatures: 1 }],
    })
    const notifs = useNotificationsStore()
    chat._notifyWorkerDisconnect()
    expect(cluster.getSite("worker-1").status).toBe("unreachable")
    expect(notifs.history.length).toBeGreaterThan(0)
    expect(notifs.history[0].level).toBe("warn")
  })

  it("_notifyWorkerDisconnect is a no-op for host sessions", () => {
    const chat = useChatStore()
    chat.sessionInfo = { ...chat.sessionInfo, homeNode: "_host" }
    const notifs = useNotificationsStore()
    const before = notifs.history.length
    chat._notifyWorkerDisconnect()
    expect(notifs.history.length).toBe(before)
  })

  it("_notifyWorkerDisconnect is a no-op when homeNode is missing", () => {
    // Audit-loop catch: an older code path may set sessionInfo
    // without ``homeNode``.  Treat missing as "_host" — never spam a
    // toast for a worker we can't identify.
    const chat = useChatStore()
    chat.sessionInfo = { sessionId: "s1" } // no homeNode key
    const cluster = useClusterStore()
    cluster.$patch({
      mode: "lab-host",
      sites: [{ nodeId: "worker-1", isHost: false, status: "online", creatures: 1 }],
    })
    const notifs = useNotificationsStore()
    const before = notifs.history.length
    chat._notifyWorkerDisconnect()
    expect(notifs.history.length).toBe(before)
  })

  it("_notifyWorkerDisconnect is a no-op in standalone mode", () => {
    const chat = useChatStore()
    chat.sessionInfo = { ...chat.sessionInfo, homeNode: "worker-1" }
    const cluster = useClusterStore()
    cluster.$patch({ mode: "standalone", sites: [] })
    const notifs = useNotificationsStore()
    const before = notifs.history.length
    chat._notifyWorkerDisconnect()
    expect(notifs.history.length).toBe(before)
  })

  it("wire_inbound reads cross_node from nested metadata as fallback", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [] }
    chat.tabs = ["main"]

    chat._onMessage({
      type: "activity",
      source: "main",
      activity_type: "wire_inbound",
      from: "alice",
      to: "main",
      content_preview: "x",
      metadata: { cross_node: true },
    })

    expect(chat.messagesByTab.main[0].crossNode).toBe(true)
  })

  it("marks an event superseded on ui_supersede", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [] }
    chat.tabs = ["main"]

    chat._onMessage({
      type: "confirm",
      source: "main",
      event_id: "ev_2",
      interactive: true,
      payload: { prompt: "x", options: [{ id: "ok", label: "OK" }] },
    })
    chat._onMessage({
      type: "ui_supersede",
      source: "main",
      event_id: "ev_2",
    })

    const msg = chat.messagesByTab.main[0]
    expect(msg.superseded).toBe(true)
    expect(msg.replied).toBe(false)
  })

  it("submitUIReply marks the message replied and queues a ui_reply frame", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [] }
    chat.tabs = ["main"]

    chat._onMessage({
      type: "ask_text",
      source: "main",
      event_id: "ev_3",
      interactive: true,
      payload: { prompt: "Name?" },
    })

    const sent = []
    chat._ws = {
      readyState: 1, // WebSocket.OPEN
      send: vi.fn((data) => sent.push(JSON.parse(data))),
    }

    chat.submitUIReply("main", "ev_3", "submit", { text: "alice" })

    const msg = chat.messagesByTab.main[0]
    expect(msg.replied).toBe(true)
    expect(msg.repliedActionId).toBe("submit")
    expect(msg.repliedValues).toEqual({ text: "alice" })
    expect(sent.length).toBe(1)
    expect(sent[0].type).toBe("ui_reply")
    expect(sent[0].event_id).toBe("ev_3")
    expect(sent[0].action_id).toBe("submit")
    expect(sent[0].values).toEqual({ text: "alice" })
  })

  it("ui_reply_ack with status=superseded flips the message superseded", () => {
    const chat = useChatStore()
    chat.messagesByTab = { main: [] }
    chat.tabs = ["main"]

    chat._onMessage({
      type: "confirm",
      source: "main",
      event_id: "ev_4",
      interactive: true,
      payload: { prompt: "x", options: [{ id: "ok", label: "OK" }] },
    })
    chat._onMessage({
      type: "ui_reply_ack",
      source: "main",
      event_id: "ev_4",
      status: "superseded",
    })

    const msg = chat.messagesByTab.main[0]
    expect(msg.superseded).toBe(true)
  })
})
