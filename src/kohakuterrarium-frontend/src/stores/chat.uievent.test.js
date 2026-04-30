import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { useChatStore } from "./chat.js"

beforeEach(() => {
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
