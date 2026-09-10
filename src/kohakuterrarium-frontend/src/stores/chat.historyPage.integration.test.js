import { createPinia, setActivePinia } from "pinia"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { useChatStore } from "./chat"
import { indexOfSemanticKey, semanticKey } from "@/components/chat/chatRenderWindow"
import { sessionAPI, terrariumAPI } from "@/utils/api"

function event(id, type = "user_message", extra = {}) {
  return {
    type,
    event_id: id,
    turn_index: id,
    branch_id: 0,
    content: String(id),
    _history_key: `e:${id}`,
    ...extra,
  }
}
function page(
  events,
  { before = "b20", after = "a30", older = true, stream = "events", ...extra } = {},
) {
  return {
    events: stream === "events" ? events : [],
    messages: stream === "events" ? [] : events,
    history_page: {
      version: 1,
      stream,
      history_id: "h1",
      before,
      after,
      has_older: older,
      has_newer: false,
      reset_required: false,
    },
    live_job_ids: [],
    is_processing: false,
    ...extra,
  }
}
const content = (chat) =>
  (chat.messagesByTab.root || []).map(
    (m) => m.content || m.parts?.map((p) => p.content || p.result).join(""),
  )
let chat
beforeEach(() => {
  setActivePinia(createPinia())
  chat = useChatStore()
  chat._instanceId = "instance"
  chat._instanceGraphId = "graph"
  chat.activeTab = "root"
})
afterEach(() => {
  chat._cleanup()
  vi.restoreAllMocks()
})

describe("paged history consumers", () => {
  it("loads the real live entry point with a bounded request and refreshes after the loaded head", async () => {
    const full = vi.spyOn(terrariumAPI, "getHistory").mockResolvedValue({ events: [] })
    const api = vi
      .spyOn(terrariumAPI, "getHistoryPage")
      .mockResolvedValueOnce(page([event(20), event(21)]))
      .mockResolvedValueOnce(page([event(10)], { before: "b10", after: "a10", older: false }))
      .mockResolvedValueOnce(page([event(22)], { before: "b22", after: "a22", older: true }))
    expect(await chat._loadHistory("root")).toBe(true)
    expect(full).not.toHaveBeenCalled()
    expect(api).toHaveBeenCalledWith("graph", "root", expect.objectContaining({ limit: 400 }))
    expect(content(chat)).toEqual(["20", "21"])
    expect(chat.tokenUsage.root.partial).toBe(true)
    chat._restoreTokenUsage("root", [
      event(1, "token_usage", { total_tokens: 900 }),
      event(2, "subagent_result", { job_id: "old-agent", total_tokens: 300 }),
    ])
    expect(chat.historyPageByTab.root.hasOlder).toBe(true)
    chat.processingByTab.root = true
    await chat.prefetchOlderHistory("root")
    expect(content(chat)).toEqual(["20", "21"])
    expect(chat.materializeOlderHistory("root").applied).toBe(true)
    expect(content(chat)).toEqual(["10", "20", "21"])
    expect(chat.processingByTab.root).toBe(true)
    expect(chat.tokenUsage.root.total).toBe(900)
    // The oldest page is loaded, so the totals are no longer partial.
    expect(chat.tokenUsage.root.partial).toBe(false)
    chat._appendStreamChunk("root", "live")
    await chat._resyncHistory("root")
    expect(full).not.toHaveBeenCalled()
    expect(api.mock.calls[2][2]).toMatchObject({ after: "a30", history_id: "h1" })
    expect(content(chat)).toEqual(["10", "20", "21", "22"])
    expect(chat.historyPageByTab.root.hasOlder).toBe(false)
    full.mockResolvedValueOnce({ events: [event(40)] })
    chat.tokenUsage.root.partial = true
    await chat._resyncHistory("root", { full: true })
    expect(chat.tokenUsage.root.partial).toBe(false)
    expect(full).toHaveBeenCalledTimes(1)
    full.mockResolvedValueOnce({ messages: [{ role: "user", content: "snapshot only" }] })
    chat.tokenUsage.root.partial = true
    await chat._resyncHistory("root", { full: true })
    expect(chat.tokenUsage.root.partial).toBe(false)
    full.mockResolvedValueOnce({ events: [] })
    chat.tokenUsage.root.partial = true
    await chat._resyncHistory("root", { full: true, initialLoad: true })
    expect(chat.tokenUsage.root.partial).toBe(false)
    api.mockResolvedValueOnce(page([event(50)]))
    await chat._resyncHistory("root")
    expect(full).toHaveBeenCalledTimes(3)
    expect(content(chat)).toEqual(["50"])
    expect(chat.historyPageByTab.root.hasOlder).toBe(true)
    // A reconnect resync keeps the materialized older range instead of
    // resetting to the newest page.
    vi.mocked(terrariumAPI.getHistoryPage).mockResolvedValueOnce(page([event(60)]))
    await chat.prefetchOlderHistory("root")
    expect(chat.materializeOlderHistory("root").applied).toBe(true)
    const merged = chat.messagesByTab.root.length
    expect(merged).toBeGreaterThan(1)
    vi.mocked(terrariumAPI.getHistoryPage).mockResolvedValueOnce(page([], { after: "a60" }))
    await chat._resyncHistory("root", { initialLoad: true })
    expect(chat.messagesByTab.root.length).toBe(merged)
  })

  it("keeps a delayed older response cache-only and rejects materialization after history mutation", async () => {
    let release
    vi.spyOn(terrariumAPI, "getHistoryPage")
      .mockResolvedValueOnce(page([event(20)]))
      .mockImplementationOnce(
        () =>
          new Promise((r) => {
            release = r
          }),
      )
    await chat.initHistoryPage("root")
    const pending = chat.prefetchOlderHistory("root")
    release(page([event(10)], { before: "b10", older: false }))
    await pending
    expect(content(chat)).toEqual(["20"])
    chat._appendStreamChunk("root", "live")
    expect(chat.materializeOlderHistory("root").applied).toBe(false)
    vi.mocked(terrariumAPI.getHistoryPage).mockResolvedValueOnce(page([event(30)]))
    const before = content(chat)
    await chat.prefetchOlderHistory("root")
    expect(content(chat)).toEqual(before)
    expect(chat.materializeOlderHistory("root").applied).toBe(true)
    expect(content(chat)).toEqual(["10", "20", "30"])
    const pin = vi.fn()
    chat.materializeOlderHistory("root", pin)
    expect(pin).not.toHaveBeenCalled()
    vi.mocked(terrariumAPI.getHistoryPage).mockResolvedValueOnce({
      ...page([]),
      history_page: { ...page([]).history_page, reset_required: true },
    })
    await chat.initHistoryPage("root")
    expect(chat.historyPageByTab.root.resetRequired).toBe(true)
    vi.mocked(terrariumAPI.getHistoryPage).mockResolvedValueOnce(page([event(60)]))
    await chat.initHistoryPage("root")
    expect(chat.historyPageByTab.root.resetRequired).toBe(false)
    expect(content(chat)).toEqual(["60"])
    vi.mocked(terrariumAPI.getHistoryPage).mockResolvedValueOnce(
      page([
        event(70, "ask_text", {
          ui_event_id: "prompt",
          interactive: true,
          surface: "chat",
          payload: { prompt: "Reply" },
        }),
      ]),
    )
    await chat.initHistoryPage("root")
    expect(chat.attentionByTab.root.pending).toEqual(new Set(["prompt"]))
    expect(chat._appliedMaxEventIdByTab.root).toBe(70)
  })

  it("reuses the saved source for older reads, maps channel messages, and never starts saved jobs", async () => {
    const api = vi
      .spyOn(sessionAPI, "getHistoryPage")
      .mockResolvedValueOnce(
        page([{ _history_key: "c20", sender: "a", content: "new" }], { stream: "channel" }),
      )
      .mockResolvedValueOnce(
        page([{ _history_key: "c10", sender: "b", content: "old" }], {
          stream: "channel",
          older: false,
        }),
      )
    const live = vi.spyOn(terrariumAPI, "getHistoryPage")
    await chat.initHistoryPage("root", { kind: "saved", sessionName: "saved-one" })
    await chat.prefetchOlderHistory("root")
    chat.materializeOlderHistory("root")
    expect(content(chat)).toEqual(["old", "new"])
    expect(chat.messagesByTab.root.map((m) => m.id)).toEqual(["ch_c10", "ch_c20"])
    expect(api.mock.calls[1].slice(0, 2)).toEqual(["saved-one", "root"])
    expect(live).not.toHaveBeenCalled()
    expect(chat.runningJobs).toEqual({})
  })

  it("uses explicit snapshot fallback only for an empty event source", async () => {
    const api = vi
      .spyOn(terrariumAPI, "getHistoryPage")
      .mockResolvedValueOnce(page([], { before: null, after: null, older: false }))
      .mockResolvedValueOnce(
        page([{ role: "user", content: "snapshot", _history_key: "s1" }], {
          stream: "snapshot",
          older: false,
        }),
      )
    await chat.initHistoryPage("root")
    expect(api.mock.calls[1][2]).toMatchObject({ stream: "snapshot", limit: 400 })
    expect(content(chat)).toEqual(["snapshot"])
  })

  it("preserves duplicate provenance and remaps an assistant boundary after older text merges", async () => {
    vi.spyOn(terrariumAPI, "getHistoryPage")
      .mockResolvedValueOnce(page([event(20, "text", { content: "later" })]))
      .mockResolvedValueOnce(page([event(10, "text", { content: "earlier " })], { older: false }))
    await chat.initHistoryPage("root")
    await chat.prefetchOlderHistory("root")
    chat.materializeOlderHistory("root")
    expect(content(chat)).toEqual(["earlier later"])
    expect(chat.messagesByTab.root[0]._historyKeys).toEqual(["e:10", "e:20"])
    vi.mocked(terrariumAPI.getHistoryPage).mockResolvedValueOnce(
      page([event(30, "user_input"), event(31, "user_message", { turn_index: 30 })]),
    )
    await chat.initHistoryPage("root")
    expect(chat.messagesByTab.root[0]._historyKeys).toEqual(["e:30", "e:31"])
    vi.mocked(terrariumAPI.getHistoryPage)
      .mockResolvedValueOnce(
        page([
          event(40, "processing_start"),
          event(41, "subagent_tool", {
            activity: "tool_start",
            tool_name: "bash",
            subagent: "worker",
            job_id: "sa",
            detail: "cmd",
          }),
          event(42, "background_result", { job_id: "sa", label: "worker" }),
          event(43, "processing_error", { error: "boom" }),
        ]),
      )
      .mockResolvedValueOnce(
        page([event(39, "user_input", { content: "older" })], { older: false }),
      )
    await chat.initHistoryPage("root")
    expect(chat.messagesByTab.root.map((m) => m.role)).toEqual(["assistant", "bg_result", "error"])
    expect(chat.messagesByTab.root.every((m) => m._historyKeys?.length)).toBe(true)
    const anchors = chat.messagesByTab.root.map((m) => semanticKey(m))
    expect(anchors).toEqual(["e:40", "e:42", "e:43"])
    await chat.prefetchOlderHistory("root")
    expect(chat.materializeOlderHistory("root").applied).toBe(true)
    expect(content(chat)[0]).toBe("older")
    expect(anchors.map((key) => indexOfSemanticKey(chat.messagesByTab.root, key))).not.toContain(-1)
    vi.mocked(terrariumAPI.getHistoryPage).mockResolvedValueOnce(
      page([
        event(80, "user_message"),
        event(81, "text", {
          content: "preview",
          _history_truncated: true,
          _history_detail: "opaque",
        }),
      ]),
    )
    vi.spyOn(terrariumAPI, "getHistoryDetail").mockResolvedValueOnce({
      record: { _history_key: "e:81", _history_truncated: false, content: "full" },
      history_page: { version: 1, stream: "events", history_id: "h1" },
    })
    await chat.initHistoryPage("root")
    chat.processingByTab.root = true
    expect((await chat.loadHistoryRecord("root", "e:81")).applied).toBe(true)
    expect(chat.processingByTab.root).toBe(true)
    vi.mocked(terrariumAPI.getHistoryPage).mockResolvedValueOnce({
      events: [event(90, "user_message")],
      is_processing: true,
    })
    chat.processingByTab.root = false
    await chat.initHistoryPage("root")
    expect(chat.processingByTab.root).toBe(true)
  })

  it("treats live job ids as authoritative and retains result-first tools across page boundaries", async () => {
    const call = event(20, "tool_call", { name: "read", call_id: "job", args: {} })
    const api = vi
      .spyOn(terrariumAPI, "getHistoryPage")
      .mockResolvedValueOnce(page([call], { live_job_ids: ["job"], is_processing: true }))
      .mockResolvedValueOnce(page([], { live_job_ids: [] }))
    await chat.initHistoryPage("root")
    expect(chat.messagesByTab.root[0].parts[0].status).toBe("running")
    expect(chat.runningJobs.job).toBeDefined()
    await chat.refreshHistoryHead("root")
    expect(chat.messagesByTab.root[0].parts[0].status).toBe("interrupted")
    expect(chat.runningJobs.job).toBeUndefined()
    expect(chat.processingByTab.root).toBe(false)
    chat.eventsByTab.root.push(
      event(25, "tool_call", { name: "read", call_id: "new-job", args: {} }),
    )
    chat._rebuildMessages("root")
    expect(
      chat.messagesByTab.root.flatMap((m) => m.parts || []).find((p) => p.jobId === "new-job")
        .status,
    ).toBe("running")
    expect(
      chat.messagesByTab.root.flatMap((m) => m.parts || []).find((p) => p.jobId === "job").status,
    ).toBe("interrupted")
    api.mockResolvedValueOnce(
      page([event(21, "tool_result", { name: "read", call_id: "job", output: "done" })]),
    )
    api.mockResolvedValueOnce(page([call], { older: false }))
    await chat.initHistoryPage("root")
    expect(chat.messagesByTab.root[0].parts[0].result).toBe("done")
    await chat.prefetchOlderHistory("root")
    chat.materializeOlderHistory("root")
    expect(
      chat.messagesByTab.root.flatMap((m) => m.parts || []).filter((p) => p.type === "tool"),
    ).toHaveLength(1)
    expect(chat.messagesByTab.root[0]._historyKeys).toEqual(["e:20", "e:21"])
    api.mockResolvedValueOnce(page([call], { live_job_ids: ["job"], is_processing: true }))
    await chat.initHistoryPage("root")
    let cursor = 100
    api.mockImplementation(async () => {
      const result = page([event(cursor++)], { after: `a${cursor}` })
      result.history_page.has_newer = true
      return result
    })
    const attention = chat.attentionByTab.root
    expect((await chat.refreshHistoryHead("root")).catchingUp).toBe(true)
    expect(chat.messagesByTab.root[0].parts[0].status).toBe("running")
    expect(chat.runningJobs.job).toBeDefined()
    expect(chat.processingByTab.root).toBe(true)
    expect(chat.attentionByTab.root).toBe(attention)
    expect(chat.historyPageByTab.root.hasNewer).toBe(true)
  })
})
