import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"

import api from "@/utils/api"
import { terrariumAPI, sessionAPI } from "@/utils/api"

function pagePayload(overrides = {}) {
  return {
    events: [],
    messages: [],
    history_page: {
      version: 1,
      stream: "events",
      history_id: "hist-1",
      before: null,
      after: newerCursor,
      has_older: false,
      has_newer: false,
      reset_required: false,
      ...(overrides.history_page || {}),
    },
    is_processing: false,
    live_job_ids: [],
    ...overrides,
  }
}

let newerCursor = "cursor-newest"
let apiGet

beforeEach(() => {
  newerCursor = "cursor-newest"
  apiGet = vi.spyOn(api, "get").mockResolvedValue({ data: pagePayload() })
})

afterEach(() => {
  apiGet.mockRestore()
})

describe("terrariumAPI.getHistoryPage", () => {
  it("preserves legacy getHistory by leaving it on its own URL and params", async () => {
    apiGet.mockResolvedValue({ data: { messages: [], events: [] } })
    await terrariumAPI.getHistory("graph-1", "root")
    expect(apiGet).toHaveBeenCalledWith("/sessions/graph-1/creatures/root/history", {})
  })

  it("opts in with paged=true and a default clamped limit", async () => {
    await terrariumAPI.getHistoryPage("graph-1", "root")
    const [, config] = apiGet.mock.calls[0]
    expect(apiGet.mock.calls[0][0]).toBe("/sessions/graph-1/creatures/root/history")
    expect(config.params).toMatchObject({ paged: true, limit: 400 })
  })

  it("clamps a limit above 400 and refuses a non-positive limit", async () => {
    await terrariumAPI.getHistoryPage("g", "root", { limit: 1200 })
    expect(apiGet.mock.calls[0][1].params.limit).toBe(400)

    apiGet.mockClear()
    await expect(terrariumAPI.getHistoryPage("g", "root", { limit: 0 })).rejects.toThrow()
    expect(apiGet).not.toHaveBeenCalled()
  })

  it("sends exactly one opaque cursor and never both before and after", async () => {
    await terrariumAPI.getHistoryPage("g", "root", { before: "old" })
    let params = apiGet.mock.calls[0][1].params
    expect(params.before).toBe("old")
    expect(Object.hasOwn(params, "after")).toBe(false)

    apiGet.mockClear()
    await terrariumAPI.getHistoryPage("g", "root", { after: "new" })
    params = apiGet.mock.calls[0][1].params
    expect(params.after).toBe("new")
    expect(Object.hasOwn(params, "before")).toBe(false)

    apiGet.mockClear()
    apiGet.mockResolvedValue({ data: pagePayload() })
    await expect(
      terrariumAPI.getHistoryPage("g", "root", { before: "old", after: "new" }),
    ).rejects.toThrow()
    expect(apiGet).not.toHaveBeenCalled()
  })

  it("carries an optional history_id and returns the raw paged payload", async () => {
    const payload = pagePayload({ history_id: "hist-9", is_processing: true, live_job_ids: ["j1"] })
    apiGet.mockResolvedValue({ data: payload })
    const out = await terrariumAPI.getHistoryPage("g", "root", { history_id: "hist-9" })
    expect(apiGet.mock.calls[0][1].params.history_id).toBe("hist-9")
    expect(out).toEqual(payload)
    expect(out.history_page.has_older).toBe(false)
  })

  it("keeps existing calls without paged=true on the full legacy URL", async () => {
    apiGet.mockResolvedValue({ data: { messages: [], events: [] } })
    await terrariumAPI.getHistory("g", "root", 42)
    const [, config] = apiGet.mock.calls[0]
    expect(config.params).toEqual({ since_event_id: 42 })
    expect(config.params.paged).toBeUndefined()
  })
})

describe("sessionAPI.getHistoryPage", () => {
  it("adds a paged adapter alongside the legacy session getHistory", async () => {
    apiGet.mockResolvedValue({ data: pagePayload() })
    const out = await sessionAPI.getHistoryPage("session-a", "root", { limit: 120 })
    expect(apiGet.mock.calls[0][0]).toBe("/sessions/session-a/history/root")
    expect(apiGet.mock.calls[0][1].params).toMatchObject({ paged: true, limit: 120 })
    expect(out.history_page.stream).toBe("events")
  })

  it("preserves explicit snapshot stream on continuations", async () => {
    await sessionAPI.getHistoryPage("saved", "root", {
      stream: "snapshot",
      before: "s1",
      history_id: "h1",
    })
    expect(apiGet.mock.calls[0][1].params).toMatchObject({
      stream: "snapshot",
      before: "s1",
      history_id: "h1",
    })
  })

  it("does not alter the raw session getHistory contract", async () => {
    apiGet.mockResolvedValue({ data: { meta: {}, targets: [] } })
    await sessionAPI.getHistory("session-a", "root")
    expect(apiGet.mock.calls[0][0]).toBe("/sessions/session-a/history/root")
    expect(apiGet.mock.calls[0][1]).toBeUndefined()
  })
})
