import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import ChatPanel from "./ChatPanel.vue"
import { useChatStore } from "@/stores/chat"
import { sessionAPI, terrariumAPI } from "@/utils/api"

let wrapper, chat, frames, idle
const height = (element) =>
  30 + (Number(element.getAttribute("data-message-id")?.split(":").pop()) % 3 || 0) * 17
function geometry(element) {
  const vp = element.closest?.(".chat-messages-viewport")
  if (!vp || element === vp) return { top: 0, bottom: 120, height: 120 }
  let top = 30 - vp.scrollTop
  for (const row of vp.querySelectorAll("[data-message-id]")) {
    if (row === element) return { top, bottom: top + height(row), height: height(row) }
    top += height(row)
  }
  return { top: 0, bottom: 0, height: 0 }
}
function page(start, count, older = true, overrides = {}) {
  const events = Array.from({ length: count }, (_, i) => ({
    type: "user_message",
    event_id: start + i,
    content: `message ${start + i}`,
    _history_key: `e:${start + i}`,
  }))
  return {
    events,
    messages: [],
    live_job_ids: [],
    is_processing: false,
    history_page: {
      version: 1,
      stream: "events",
      history_id: "history",
      before: `b${start}`,
      after: `a${start + count - 1}`,
      has_older: older,
      has_newer: false,
      reset_required: false,
    },
    ...overrides,
  }
}
const ids = () =>
  wrapper.findAll("[data-message-id]").map((row) => row.attributes("data-message-id"))
const button = () => wrapper.get("button.self-center")
function visibleAnchor() {
  const vp = wrapper.get(".chat-messages-viewport").element
  const row = [...vp.querySelectorAll("[data-message-id]")].find(
    (el) => el.getBoundingClientRect().bottom > 0,
  )
  return { key: row.getAttribute("data-message-id"), top: row.getBoundingClientRect().top }
}
async function scroll(top) {
  const vp = wrapper.get(".chat-messages-viewport").element
  vp.scrollTop = Math.max(0, top)
  vp.dispatchEvent(new Event("scroll"))
  for (const [id, callback] of [...frames]) {
    frames.delete(id)
    callback()
  }
  await flushPromises()
}
beforeEach(() => {
  setActivePinia(createPinia())
  frames = new Map()
  idle = new Map()
  let sequence = 0
  vi.stubGlobal("requestAnimationFrame", (cb) => {
    const id = ++sequence
    frames.set(id, cb)
    return id
  })
  vi.stubGlobal("cancelAnimationFrame", (id) => frames.delete(id))
  vi.stubGlobal("requestIdleCallback", (cb) => {
    const id = ++sequence
    idle.set(id, cb)
    return id
  })
  vi.stubGlobal("cancelIdleCallback", (id) => idle.delete(id))
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function () {
    return geometry(this)
  })
  chat = useChatStore("graph")
  chat._instanceId = "graph"
  chat._instanceGraphId = "graph"
  chat.activeTab = "root"
  chat.tabs = ["root", "other"]
  chat.messagesByTab.other = [{ id: "other", role: "user", content: "other tab" }]
  chat.commandInventoryByTab.root = { commands: [], skills: [] }
  chat._commandInventoryFetchedAtByTab.root = Date.now()
})
afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  chat._cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})
async function mountPage(kind = "live") {
  if (kind === "live") await chat._loadHistory("root")
  else await chat.initHistoryPage("root", { kind: "saved", sessionName: "saved" })
  await mountPanel(kind)
}
async function mountPanel(kind = "live") {
  wrapper = mount(ChatPanel, {
    attachTo: document.body,
    props: {
      instance: { id: "graph", graph_id: "graph", creatures: [{ name: "root", status: "idle" }] },
      readOnly: kind === "saved",
    },
    global: {
      stubs: {
        ChatMessage: {
          props: ["message"],
          template:
            '<div>{{ message.content || message.parts?.map(p => p.content).join("") }}</div>',
        },
        ModelSwitcher: true,
        SiteChip: true,
        StatusDot: true,
      },
    },
  })
  await flushPromises()
  const vp = wrapper.get(".chat-messages-viewport").element
  Object.defineProperty(vp, "clientHeight", { configurable: true, value: 120 })
  Object.defineProperty(vp, "scrollHeight", {
    configurable: true,
    get: () =>
      30 + [...vp.querySelectorAll("[data-message-id]")].reduce((sum, row) => sum + height(row), 0),
  })
  await scroll(vp.scrollHeight - 120)
}

async function runIdle() {
  for (const [id, callback] of [...idle]) {
    idle.delete(id)
    callback()
  }
  await flushPromises()
}

describe("bounded initial tail fill, source currency, and scroll intent", () => {
  it("does not turn initial fill into multi-page head catchup after a live mutation", async () => {
    const api = vi
      .spyOn(terrariumAPI, "getHistoryPage")
      .mockResolvedValueOnce(page(150, 50))
      .mockResolvedValue(page(200, 1))
    await mountPage()
    chat._appendStreamChunk("root", "live")
    await runIdle()
    expect(api).toHaveBeenCalledTimes(1)
  })

  it("stops scheduling once successive small pages reach the tail message budget", async () => {
    const api = vi
      .spyOn(terrariumAPI, "getHistoryPage")
      .mockResolvedValueOnce(page(150, 50))
      .mockResolvedValueOnce(page(100, 50))
      .mockResolvedValueOnce(page(50, 50))
      .mockResolvedValueOnce(page(0, 50))
    await mountPage()
    await runIdle()
    await runIdle()
    await runIdle()
    await runIdle()
    expect(ids()).toHaveLength(200)
    expect(api).toHaveBeenCalledTimes(4)
    const vp = wrapper.get(".chat-messages-viewport").element
    expect(vp.scrollTop).toBeGreaterThanOrEqual(vp.scrollHeight - vp.clientHeight)
    await scroll(vp.scrollHeight - vp.clientHeight)
    await runIdle()
    expect(ids()).toHaveLength(200)
    expect(api).toHaveBeenCalledTimes(4)
  })

  it("does not start a queued fill after its scheduling deadline", async () => {
    let now = 100
    vi.spyOn(Date, "now").mockImplementation(() => now)
    const api = vi.spyOn(terrariumAPI, "getHistoryPage").mockResolvedValueOnce(page(100, 50))
    await mountPage()
    now += 1501
    await runIdle()
    expect(api).toHaveBeenCalledTimes(1)
    expect(ids()).toHaveLength(50)
  })

  it("ignores compensation-generated scroll but accepts the next upward inertia movement", async () => {
    const text = (id) => ({
      type: "text",
      content: `text${id}`,
      event_id: id,
      _history_key: `e:${id}`,
    })
    const api = vi
      .spyOn(terrariumAPI, "getHistoryPage")
      .mockResolvedValueOnce(page(401, 50, true, { events: [text(400), ...page(401, 50).events] }))
      .mockResolvedValueOnce(page(300, 1, true, { events: [text(300)] }))
      .mockResolvedValueOnce(page(200, 1, false, { events: [text(200)] }))
    await mountPage()
    const vp = wrapper.get(".chat-messages-viewport").element
    vp.scrollTop = 20
    await button().trigger("click")
    await flushPromises()
    const keys = ids()
    await scroll(vp.scrollTop)
    expect(ids()).toEqual(keys)
    expect(api).toHaveBeenCalledTimes(2)
    await scroll(10)
    expect(api).toHaveBeenCalledTimes(3)
    expect(wrapper.text()).toContain("text200text300text400")
  })

  it("claims fill once across two panels and never rearms on manual reset", async () => {
    let release
    const api = vi
      .spyOn(terrariumAPI, "getHistoryPage")
      .mockResolvedValueOnce(page(400, 50))
      .mockImplementationOnce(
        () =>
          new Promise((r) => {
            release = r
          }),
      )
    await mountPage()
    const first = wrapper
    await mountPanel()
    await runIdle()
    expect(api).toHaveBeenCalledTimes(2)
    release(page(350, 50, false))
    await flushPromises()
    await runIdle()
    expect(ids()).toHaveLength(100)
    expect(first.findAll("[data-message-id]")).toHaveLength(100)
    api.mockResolvedValueOnce(page(400, 50))
    await chat.initHistoryPage("root")
    await flushPromises()
    await runIdle()
    expect(api).toHaveBeenCalledTimes(3)
    first.unmount()
  })

  it("keeps pre-first-page reading intent cancelled when history becomes ready", async () => {
    let release
    const api = vi.spyOn(terrariumAPI, "getHistoryPage").mockImplementationOnce(
      () =>
        new Promise((r) => {
          release = r
        }),
    )
    await mountPanel()
    const load = chat._loadHistory("root")
    await flushPromises()
    wrapper
      .get(".chat-messages-viewport")
      .element.dispatchEvent(new WheelEvent("wheel", { deltaY: -10 }))
    release(page(400, 50))
    await load
    await flushPromises()
    await runIdle()
    expect(api).toHaveBeenCalledTimes(1)
    expect(ids()).toHaveLength(50)
  })

  it("starts only after a mounted empty panel receives its first page", async () => {
    let release
    const api = vi
      .spyOn(terrariumAPI, "getHistoryPage")
      .mockImplementationOnce(
        () =>
          new Promise((r) => {
            release = r
          }),
      )
      .mockResolvedValueOnce(page(0, 200, false))
    await mountPanel()
    const load = chat._loadHistory("root")
    await flushPromises()
    release(page(200, 1))
    await load
    await flushPromises()
    expect(ids()).toHaveLength(1)
    expect(api).toHaveBeenCalledTimes(1)
    await runIdle()
    expect(ids()).toHaveLength(200)
    expect(api).toHaveBeenCalledTimes(2)
  })

  it.each(["live", "saved"])(
    "shows %s first page before delayed fill and fills the same tail budget as return-to-bottom",
    async (kind) => {
      let release
      const api = vi
        .spyOn(kind === "live" ? terrariumAPI : sessionAPI, "getHistoryPage")
        .mockResolvedValueOnce(page(400, 50))
        .mockImplementationOnce(
          () =>
            new Promise((r) => {
              release = r
            }),
        )
      await mountPage(kind)
      expect(ids()).toHaveLength(50)
      expect(api).toHaveBeenCalledTimes(1)
      await runIdle()
      expect(api).toHaveBeenCalledTimes(2)
      expect(ids()).toHaveLength(50)
      release(page(0, 400, false))
      await flushPromises()
      await runIdle()
      expect(ids()).toHaveLength(200)
      const filled = ids()
      const vp = wrapper.get(".chat-messages-viewport").element
      await scroll(vp.scrollHeight - 120)
      expect(ids()).toEqual(filled)
      await runIdle()
      expect(api).toHaveBeenCalledTimes(2)
    },
  )

  it("stops at three extra pages even when many events merge into one row", async () => {
    const textPage = (n) =>
      page(n, 400, true, {
        events: Array.from({ length: 400 }, (_, i) => ({
          type: "text",
          content: "x",
          event_id: n + i,
          _history_key: `e:${n + i}`,
        })),
      })
    const api = vi
      .spyOn(terrariumAPI, "getHistoryPage")
      .mockResolvedValueOnce(textPage(1200))
      .mockResolvedValueOnce(textPage(800))
      .mockResolvedValueOnce(textPage(400))
      .mockResolvedValueOnce(textPage(0))
    await mountPage()
    await runIdle()
    await runIdle()
    await runIdle()
    await runIdle()
    expect(api).toHaveBeenCalledTimes(4)
    expect(chat.messagesByTab.root).toHaveLength(1)
    expect(chat.messagesByTab.root[0]._historyKeys).toHaveLength(1600)
    await scroll(0)
    await runIdle()
    expect(api).toHaveBeenCalledTimes(4)
  })

  it.each(["up", "tab", "unmount", "deadline"])(
    "keeps a late initial-fill response cache-only after %s",
    async (reason) => {
      let release,
        now = 100
      vi.spyOn(Date, "now").mockImplementation(() => now)
      const api = vi
        .spyOn(terrariumAPI, "getHistoryPage")
        .mockResolvedValueOnce(page(400, 50))
        .mockImplementationOnce(
          () =>
            new Promise((r) => {
              release = r
            }),
        )
      await mountPage()
      await runIdle()
      expect(api).toHaveBeenCalledTimes(2)
      if (reason === "up")
        wrapper
          .get(".chat-messages-viewport")
          .element.dispatchEvent(new WheelEvent("wheel", { deltaY: -10 }))
      if (reason === "tab") {
        chat.activeTab = "other"
        await flushPromises()
      }
      if (reason === "unmount") {
        wrapper.unmount()
        wrapper = null
      }
      if (reason === "deadline") now += 1501
      release(page(0, 400))
      await flushPromises()
      await runIdle()
      expect(chat.messagesByTab.root).toHaveLength(50)
      expect(chat.historyPageByTab.root.hasCachedOlder).toBe(true)
      expect(api).toHaveBeenCalledTimes(2)
    },
  )

  it("does not fetch when the render-unit budget is full despite few rows", async () => {
    const messages = Array.from({ length: 3 }, (_, i) => ({
      role: "assistant",
      content: "row",
      _history_key: `s:${i}`,
      tool_calls: Array.from({ length: 500 }, (_, j) => ({
        id: `${i}:${j}`,
        function: { name: "read", arguments: "{}" },
      })),
    }))
    const payload = page(0, 0, true, { messages })
    payload.history_page.stream = "snapshot"
    const api = vi.spyOn(terrariumAPI, "getHistoryPage").mockResolvedValueOnce(payload)
    await mountPage()
    await runIdle()
    expect(ids()).toHaveLength(2)
    expect(api).toHaveBeenCalledTimes(1)
  })
})

describe("connected paged history and reset", () => {
  it("offers bounded recovery after an older response invalidates the source", async () => {
    const reset = page(0, 0)
    reset.history_page.reset_required = true
    const api = vi
      .spyOn(terrariumAPI, "getHistoryPage")
      .mockResolvedValueOnce(page(400, 50))
      .mockResolvedValueOnce(reset)
      .mockResolvedValueOnce(page(600, 50))
    await mountPage()
    await button().trigger("click")
    await flushPromises()
    expect(chat.messagesByTab.root).toHaveLength(50)
    await wrapper.get("[data-history-reset]").trigger("click")
    await flushPromises()
    expect(api.mock.calls[2][2]).toEqual({ limit: 400 })
    expect(wrapper.text()).toContain("message 600")
    expect(wrapper.find("[data-history-reset]").exists()).toBe(false)
    expect(button().exists()).toBe(true)
  })

  it("loads the full saved channel record from its visible preview control", async () => {
    const record = {
      content: "preview",
      sender: "worker",
      _history_key: "c:1",
      _history_truncated: true,
      _history_detail: "opaque",
    }
    const payload = page(0, 0, false)
    payload.history_page.stream = "channel"
    payload.messages = [record]
    vi.spyOn(sessionAPI, "getHistoryPage").mockResolvedValue(payload)
    const detail = vi.spyOn(sessionAPI, "getHistoryDetail").mockResolvedValue({
      record: { ...record, content: "complete channel message", _history_truncated: false },
      history_page: payload.history_page,
    })
    await mountPage("saved")
    expect(wrapper.text()).toContain("preview")
    await wrapper.get('[data-history-detail="c:1"]').trigger("click")
    await flushPromises()
    expect(detail).toHaveBeenCalledWith("saved", "root", {
      stream: "channel",
      history_id: "history",
      ref: "opaque",
    })
    expect(wrapper.text()).toContain("complete channel message")
    expect(wrapper.find('[data-history-detail="c:1"]').exists()).toBe(false)
  })
  it.each(["live", "saved"])(
    "bounds %s initial load, preserves current anchor after delayed manual fetch, and uses local rows first",
    async (kind) => {
      let release
      const api = vi
        .spyOn(kind === "live" ? terrariumAPI : sessionAPI, "getHistoryPage")
        .mockResolvedValueOnce(page(400, 50))
        .mockImplementationOnce(
          () =>
            new Promise((resolve) => {
              release = resolve
            }),
        )
      await mountPage(kind)
      expect(ids()).toHaveLength(50)
      expect(api.mock.calls[0][2]).toMatchObject({ limit: 400 })
      await button().trigger("click")
      await flushPromises()
      expect(api.mock.calls[1][2]).toMatchObject({ before: "b400", stream: "events" })
      expect(ids()).toHaveLength(50)
      const vp = wrapper.get(".chat-messages-viewport").element
      vp.scrollTop = 55
      const anchor = visibleAnchor()
      release(page(0, 400, false))
      await flushPromises()
      expect(ids()).toHaveLength(250)
      expect(
        wrapper.get(`[data-message-id="${anchor.key}"]`).element.getBoundingClientRect().top,
      ).toBe(anchor.top)
      await button().trigger("click")
      await flushPromises()
      expect(api).toHaveBeenCalledTimes(2)
      expect(ids()).toHaveLength(450)
      await scroll(vp.scrollHeight - 120)
      expect(ids()).toHaveLength(200)
      expect(chat.messagesByTab.root).toHaveLength(450)
    },
  )

  it("keeps a completed prefetch cache-only and traverses multiple pages via upward scroll", async () => {
    const api = vi
      .spyOn(terrariumAPI, "getHistoryPage")
      .mockResolvedValueOnce(page(800, 200))
      .mockResolvedValueOnce(page(400, 400))
      .mockResolvedValueOnce(page(0, 400, true))
      .mockResolvedValueOnce(page(-400, 400, false))
    await mountPage()
    const vp = wrapper.get(".chat-messages-viewport").element
    const top = vp.scrollTop
    await chat.prefetchOlderHistory("root")
    expect(ids()).toHaveLength(200)
    expect(chat.messagesByTab.root).toHaveLength(200)
    expect(vp.scrollTop).toBe(top)
    await scroll(0)
    expect(ids()).toHaveLength(300)
    expect(api).toHaveBeenCalledTimes(2)
    await scroll(0)
    await scroll(0)
    await scroll(0)
    expect(ids()).toHaveLength(600)
    expect(api).toHaveBeenCalledTimes(2)
    const beforeIdle = vp.scrollTop
    for (const [id, callback] of [...idle]) {
      idle.delete(id)
      callback()
    }
    await flushPromises()
    expect(api).toHaveBeenCalledTimes(3)
    expect(ids()).toHaveLength(600)
    expect(chat.messagesByTab.root).toHaveLength(600)
    expect(vp.scrollTop).toBe(beforeIdle)
    await scroll(0)
    expect(ids()).toHaveLength(700)
    expect(api).toHaveBeenCalledTimes(3)
    await scroll(vp.scrollHeight - 120)
    expect(ids()).toHaveLength(200)
    expect(idle.size).toBe(0)
    // A viewport pinned at the top receives no further scroll delta, so the
    // gesture itself must still be able to request the next batch.
    await scroll(0)
    expect(ids()).toHaveLength(300)
    vp.scrollTop = 0
    vp.dispatchEvent(new WheelEvent("wheel", { deltaY: -120 }))
    await flushPromises()
    expect(ids()).toHaveLength(400)
    vp.scrollTop = vp.scrollHeight - 120
    vp.dispatchEvent(new WheelEvent("wheel", { deltaY: -120 }))
    await flushPromises()
    expect(ids()).toHaveLength(400)
    // Render every loaded row, so the next upward step needs a fetch.
    for (let step = 0; step < 6 && ids().length < chat.messagesByTab.root.length; step += 1) {
      vp.scrollTop = 0
      vp.dispatchEvent(new WheelEvent("wheel", { deltaY: -120 }))
      await flushPromises()
    }
    expect(ids()).toHaveLength(chat.messagesByTab.root.length)
    const calls = api.mock.calls.length
    const loaded = chat.messagesByTab.root.length
    // A live turn refuses the fetch instead of spending a discarded request.
    chat.processingByTab.root = true
    await flushPromises()
    expect(wrapper.find("[data-history-generating]").exists()).toBe(true)
    vp.scrollTop = 0
    vp.dispatchEvent(new WheelEvent("wheel", { deltaY: -120 }))
    await flushPromises()
    expect(api).toHaveBeenCalledTimes(calls)
    expect(chat.messagesByTab.root).toHaveLength(loaded)
    chat.processingByTab.root = false
    await flushPromises()
    expect(wrapper.find("[data-history-generating]").exists()).toBe(false)
    vp.scrollTop = 0
    vp.dispatchEvent(new WheelEvent("wheel", { deltaY: -120 }))
    await flushPromises()
    expect(api).toHaveBeenCalledTimes(calls + 1)
    expect(chat.messagesByTab.root.length).toBeGreaterThan(loaded)
  })

  it.each(["tail", "switch", "unmount"])(
    "does not materialize when %s invalidates a pending viewport continuation",
    async (action) => {
      let release
      vi.spyOn(terrariumAPI, "getHistoryPage")
        .mockResolvedValueOnce(page(400, 50))
        .mockImplementationOnce(
          () =>
            new Promise((resolve) => {
              release = resolve
            }),
        )
      await mountPage()
      await button().trigger("click")
      await flushPromises()
      if (action === "tail") {
        const vp = wrapper.get(".chat-messages-viewport").element
        await scroll(vp.scrollHeight - 120)
      } else if (action === "switch") {
        chat.activeTab = "other"
        await flushPromises()
      } else {
        wrapper.unmount()
        wrapper = null
      }
      release(page(0, 400, false))
      await flushPromises()
      expect(chat.messagesByTab.root).toHaveLength(50)
      expect(chat.historyPageByTab.root.hasCachedOlder).toBe(true)
      if (action === "switch") expect(ids()).toEqual(["other"])
      expect(idle.size).toBe(0)
    },
  )

  it("resolves the current row containing the old text key after a boundary merge", async () => {
    const text = (id, content) => ({ type: "text", event_id: id, content, _history_key: `e:${id}` })
    vi.spyOn(terrariumAPI, "getHistoryPage")
      .mockResolvedValueOnce(page(400, 1, true, { events: [text(400, "later")] }))
      .mockResolvedValueOnce(page(0, 1, false, { events: [text(0, "earlier ")] }))
    await mountPage()
    const vp = wrapper.get(".chat-messages-viewport").element
    vp.scrollTop = 10
    const anchor = visibleAnchor()
    await button().trigger("click")
    await flushPromises()
    expect(wrapper.text()).toContain("earlier later")
    expect(chat.messagesByTab.root[0]._historyKeys).toEqual(["e:0", "e:400"])
    expect(ids()).toEqual(["h_e:0"])
    expect(wrapper.get('[data-message-id="h_e:0"]').element.getBoundingClientRect().top).toBe(
      anchor.top,
    )
  })
})
