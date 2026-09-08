import { describe, expect, it, vi } from "vitest"

import { mergeRawRecords, physicalKey } from "./historyProjection"
import { createHistoryPageSource } from "./historyPageSource"

// Raw backend record shape: stable physical ``_history_key``, never a
// frontend ``_messageId`` (the backend does not invent one).
function record(key, extra = {}) {
  return { _history_key: `events:root:${key}`, content: key, ...extra }
}

function makePage({
  before = null,
  after = null,
  hasOlder = false,
  hasNewer = false,
  reset = false,
  records = [],
  historyId = "h1",
  stream = "events",
} = {}) {
  return {
    events: records,
    messages: [],
    history_page: {
      version: 1,
      stream,
      history_id: historyId,
      before,
      after,
      has_older: hasOlder,
      has_newer: hasNewer,
      reset_required: reset,
    },
    is_processing: false,
    live_job_ids: [],
  }
}

function makeHarness(overrides = {}) {
  const pages = []
  const calls = []
  const fetchPage =
    overrides.fetchPage ||
    (async (options) => {
      calls.push(options)
      const next = pages.shift()
      return typeof next === "function" ? next(options) : next
    })
  const source = createHistoryPageSource({
    fetchPage,
    getSourceKey: overrides.getSourceKey || (() => "g:root"),
    getInstanceGeneration: overrides.getInstanceGeneration || (() => 1),
    getMutationGeneration: overrides.getMutationGeneration || (() => 0),
    pageSize: overrides.pageSize || 400,
    onResetRequired: overrides.onResetRequired,
  })
  return { pages, calls, source }
}

describe("projection: physical record identity", () => {
  it("prefers _history_key, then message_id, then id", () => {
    expect(physicalKey({ _history_key: "events:root:5", id: "x" })).toBe("events:root:5")
    expect(physicalKey({ message_id: "m1", id: "x" })).toBe("m1")
    expect(physicalKey({ id: "h_5" })).toBe("h_5")
  })

  it("returns null when no stable key is present", () => {
    expect(physicalKey({ content: "hi" })).toBeNull()
  })
})

describe("projection: mergeRawRecords", () => {
  it("concatenates contiguous ranges in order", () => {
    const merged = mergeRawRecords([record("c0"), record("c1")], [record("c2")])
    expect(merged.map((r) => r.content)).toEqual(["c0", "c1", "c2"])
  })

  it("drops a boundary repeat that shares a physical key", () => {
    const merged = mergeRawRecords([record("c0"), record("c1")], [record("c1"), record("c2")])
    expect(merged.map((r) => r.content)).toEqual(["c0", "c1", "c2"])
  })

  it("keeps distinct keys even when content is identical", () => {
    const merged = mergeRawRecords(
      [record("c0"), { ...record("c1"), _history_key: "events:root:other" }],
      [record("c2")],
    )
    expect(merged.map((r) => r._history_key)).toEqual([
      "events:root:c0",
      "events:root:other",
      "events:root:c2",
    ])
  })
})

describe("history page source", () => {
  it("returns null window and empty records before initialize", () => {
    const { source } = makeHarness()
    expect(source.getWindow()).toBeNull()
    expect(source.getRecords()).toEqual([])
  })

  it("initialize returns raw records and the exclusive window", async () => {
    const { pages, source } = makeHarness()
    pages.push(() =>
      makePage({
        before: "c10",
        after: "c20",
        hasOlder: true,
        records: [record("c10"), record("c11")],
      }),
    )
    const out = await source.initialize()
    expect(out.discarded).toBe(false)
    expect(out.applied).toBe(true)
    expect(out.records.map((r) => r.content)).toEqual(["c10", "c11"])
    expect(source.getRecords().map((r) => r.content)).toEqual(["c10", "c11"])
    expect(source.getWindow()).toMatchObject({ before: "c10", after: "c20", hasOlder: true })
  })

  it("prefetch caches raw records by the requested boundary, never the returned after", async () => {
    const { pages, calls, source } = makeHarness()
    pages.push(() =>
      makePage({
        before: "c10",
        after: "c20",
        hasOlder: true,
        records: [record("c10"), record("c11")],
      }),
    )
    await source.initialize()
    // Older page: the backend's returned newest cursor (c9) is STRICTLY below
    // the requested before (c10), so c9 can never be the join token.
    pages.push(() => makePage({ before: "c0", after: "c9", records: [record("c0"), record("c9")] }))
    const out = await source.prefetchOlder()
    expect(out.cached).toBe(true)
    expect(out.boundary).toBe("c10")
    // The fetch is a bare pagination read: no projector options, no conversion.
    expect(calls[1]).toEqual({ limit: 400, before: "c10", history_id: "h1", stream: "events" })
    // Cache holds raw records; the materialized range is untouched (no replay).
    expect(source.getRecords().map((r) => r.content)).toEqual(["c10", "c11"])
    expect(source.getState().hasCachedOlder).toBe(true)
  })

  it("materialize prepends raw records and advances the oldest window", async () => {
    const { pages, source } = makeHarness()
    pages.push(() =>
      makePage({
        before: "c10",
        after: "c20",
        hasOlder: true,
        records: [record("c10"), record("c11")],
      }),
    )
    await source.initialize()
    pages.push(() => makePage({ before: "c0", after: "c9", records: [record("c0"), record("c9")] }))
    await source.prefetchOlder()
    const out = source.materializeCachedOlder()
    expect(out.merged).toBe(true)
    expect(out.records.map((r) => r.content)).toEqual(["c0", "c9", "c10", "c11"])
    expect(source.getRecords().map((r) => r.content)).toEqual(["c0", "c9", "c10", "c11"])
    expect(source.getWindow()).toMatchObject({ before: "c0", hasOlder: false })
    expect(source.getState().hasCachedOlder).toBe(false)
  })

  it("refresh keeps the oldest range and preserves a cached older page", async () => {
    const { pages, calls, source } = makeHarness()
    pages.push(() =>
      makePage({
        before: "c10",
        after: "c20",
        hasOlder: true,
        hasNewer: true,
        records: [record("c10"), record("c11")],
      }),
    )
    await source.initialize()
    pages.push(() => makePage({ before: "c0", after: "c9", records: [record("c0"), record("c9")] }))
    await source.prefetchOlder()
    pages.push(() =>
      makePage({
        before: "c21",
        after: "c30",
        hasNewer: false,
        records: [record("c21"), record("c22")],
      }),
    )
    const out = await source.refreshHead()
    expect(out.applied).toBe(true)
    expect(calls[2]).toEqual({ limit: 400, after: "c20", history_id: "h1", stream: "events" })
    expect(out.records.map((r) => r.content)).toEqual(["c10", "c11", "c21", "c22"])
    // Oldest range and the raw cached older page are preserved.
    expect(source.getWindow()).toMatchObject({ before: "c10", hasOlder: true, after: "c30" })
    expect(source.getState().hasCachedOlder).toBe(true)
    const mat = source.materializeCachedOlder()
    expect(mat.records.map((r) => r.content)).toEqual(["c0", "c9", "c10", "c11", "c21", "c22"])
  })

  it("an empty after page preserves the prior head cursor", async () => {
    const { pages, source } = makeHarness()
    pages.push(() =>
      makePage({ before: "c10", after: "c20", hasNewer: true, records: [record("c10")] }),
    )
    await source.initialize()
    pages.push(() => makePage({ hasNewer: false, records: [] }))
    const out = await source.refreshHead()
    expect(out.applied).toBe(true)
    expect(source.getWindow()).toMatchObject({ after: "c20", hasNewer: false })
    expect(source.getRecords().map((r) => r.content)).toEqual(["c10"])
  })

  it("prefetchOlder is a no-op when there is no older range", async () => {
    const { pages, source } = makeHarness()
    pages.push(() => makePage({ before: "c10", after: "c20", records: [record("c10")] }))
    await source.initialize()
    const out = await source.prefetchOlder()
    expect(out.cached).toBe(false)
    expect(source.getState().hasCachedOlder).toBe(false)
  })

  it("repeated prefetch for the same boundary dedupes without a new request", async () => {
    const { pages, calls, source } = makeHarness()
    pages.push(() =>
      makePage({ before: "c10", after: "c20", hasOlder: true, records: [record("c10")] }),
    )
    await source.initialize()
    pages.push(() => makePage({ before: "c0", after: "c9", records: [record("c0")] }))
    expect((await source.prefetchOlder()).cached).toBe(true)
    const second = await source.prefetchOlder()
    expect(second.cached).toBe(true)
    expect(second.boundary).toBe("c10")
    expect(calls).toHaveLength(2) // initialize + one prefetch, no duplicate fetch
  })

  it("ensureOlder materializes a cached older page before fetching", async () => {
    const { pages, calls, source } = makeHarness()
    pages.push(() =>
      makePage({
        before: "c10",
        after: "c20",
        hasOlder: true,
        records: [record("c10"), record("c11")],
      }),
    )
    await source.initialize()
    pages.push(() => makePage({ before: "c0", after: "c9", records: [record("c0")] }))
    await source.prefetchOlder()
    const before = calls.length
    const out = await source.ensureOlder()
    expect(out.action).toBe("materialized")
    expect(out.records.map((r) => r.content)).toEqual(["c0", "c10", "c11"])
    expect(calls).toHaveLength(before)
    expect(source.getWindow()).toMatchObject({ before: "c0" })
  })

  it("prefetch and refresh complete out of order without corrupting the range", async () => {
    const resolvers = []
    const { source } = makeHarness({
      fetchPage: () => new Promise((resolve) => resolvers.push((payload) => resolve(payload))),
    })
    const initPromise = source.initialize()
    resolvers[0](
      makePage({
        before: "c10",
        after: "c20",
        hasOlder: true,
        hasNewer: true,
        records: [record("c10"), record("c11")],
      }),
    )
    await initPromise
    const prefetchPromise = source.prefetchOlder()
    const refreshPromise = source.refreshHead()
    // Newest side resolves first (refresh), then the older prefetch arrives.
    resolvers[2](
      makePage({ before: "c21", after: "c30", hasNewer: false, records: [record("c21")] }),
    )
    await refreshPromise
    resolvers[1](
      makePage({
        before: "c0",
        after: "c9",
        hasOlder: false,
        records: [record("c0"), record("c9")],
      }),
    )
    await prefetchPromise
    expect(source.getRecords().map((r) => r.content)).toEqual(["c10", "c11", "c21"])
    expect(source.getState().hasCachedOlder).toBe(true)
    const mat = source.materializeCachedOlder()
    expect(mat.records.map((r) => r.content)).toEqual(["c0", "c9", "c10", "c11", "c21"])
  })

  it("a stale in-flight request cannot clear a newer request's pending state", async () => {
    const resolvers = []
    const { source } = makeHarness({
      fetchPage: () => new Promise((resolve) => resolvers.push((payload) => resolve(payload))),
    })
    const first = source.initialize()
    const second = source.initialize()
    // Resolve the superseded first request; it must not apply nor clear pending.
    resolvers[0](makePage({ before: "c10", after: "c20", records: [record("c10")] }))
    await first
    expect(source.getState().pending).toBe(true)
    expect(source.getRecords()).toEqual([])
    resolvers[1](makePage({ before: "c10", after: "c20", records: [record("c10")] }))
    await second
    expect(source.getState().pending).toBe(false)
    expect(source.getRecords().map((r) => r.content)).toEqual(["c10"])
  })

  it("reset invalidates an outstanding request and clears cached state", async () => {
    const resolvers = []
    const { source } = makeHarness({
      fetchPage: () => new Promise((resolve) => resolvers.push((payload) => resolve(payload))),
    })
    const initPromise = source.initialize()
    resolvers[0](
      makePage({ before: "c10", after: "c20", hasOlder: true, records: [record("c10")] }),
    )
    await initPromise
    const prefetchPromise = source.prefetchOlder()
    source.reset()
    expect(source.getWindow()).toBeNull()
    expect(source.getRecords()).toEqual([])
    resolvers[1](makePage({ before: "c0", after: "c9", records: [record("c0")] }))
    const out = await prefetchPromise
    expect(out.discarded).toBe(true)
    expect(source.getRecords()).toEqual([])
  })

  it("surfaces a reset_required response without merging it", async () => {
    const { pages, source, onResetRequired } = makeHarness({ onResetRequired: vi.fn() })
    pages.push(() =>
      makePage({ before: "c10", after: "c20", hasOlder: true, records: [record("c10")] }),
    )
    await source.initialize()
    pages.push(() => makePage({ reset: true }))
    const out = await source.prefetchOlder()
    expect(out.discarded).toBe(true)
    expect(out.resetRequired).toBe(true)
    expect(source.getRecords()).toEqual([])
    expect(source.getWindow()).toBeNull()
  })

  it("rejects a continuation whose history identity or stream changes", async () => {
    const { pages, source } = makeHarness()
    pages.push(() =>
      makePage({ before: "c10", after: "c20", hasOlder: true, records: [record("c10")] }),
    )
    await source.initialize()
    // Same route but a different history identity: do not merge into the cache.
    pages.push(() =>
      makePage({ before: "c21", after: "c30", historyId: "different", records: [record("c21")] }),
    )
    const out = await source.refreshHead()
    expect(out.discarded).toBe(true)
    expect(out.resetRequired).toBe(true)
    expect(source.getRecords()).toEqual([])
  })
})
