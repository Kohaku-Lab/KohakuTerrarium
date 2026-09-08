import { expect, it, vi } from "vitest"
import { createHistoryPageController } from "./historyPageController"

const page = (start, count, hasNewer = false) => ({
  events: Array.from({ length: count }, (_, i) => ({
    _history_key: `e:${start + i}`,
    event_id: start + i,
  })),
  history_page: {
    version: 1,
    stream: "events",
    history_id: "h",
    before: `b${start}`,
    after: `a${start + count - 1}`,
    has_older: true,
    has_newer: hasNewer,
    reset_required: false,
  },
  live_job_ids: [],
  is_processing: false,
})
it("drains bounded head pages atomically and shares a prefetch refresh with the authoritative resync", async () => {
  let mutation = 0,
    release
  const fetchPage = vi
    .fn()
    .mockResolvedValueOnce(page(100, 1))
    .mockResolvedValueOnce(page(101, 400, true))
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          release = resolve
        }),
    )
    .mockResolvedValueOnce(page(901, 100))
    .mockResolvedValueOnce(page(0, 100))
  const applyReplay = vi.fn()
  const controller = createHistoryPageController({
    fetchPage,
    applyReplay,
    getSourceKey: () => "source",
    getInstanceGeneration: () => 0,
    getMutationGeneration: () => mutation,
  })
  await controller.initialize()
  mutation++
  const prefetch = controller.prefetchOlder()
  const resync = controller.refreshHead()
  await vi.waitFor(() => expect(release).toBeTypeOf("function"))
  expect(applyReplay).toHaveBeenCalledTimes(1)
  release(page(501, 400, true))
  expect((await resync).applied).toBe(true)
  await prefetch
  expect(applyReplay).toHaveBeenCalledTimes(2)
  expect(applyReplay.mock.calls[1][0]).toHaveLength(901)
  expect(applyReplay.mock.calls[1][0].at(-1)._history_key).toBe("e:1000")
  expect(fetchPage.mock.calls.map(([params]) => params.after).filter(Boolean)).toEqual([
    "a100",
    "a500",
    "a900",
  ])
  expect(controller.materializeOlder().applied).toBe(true)
  expect(applyReplay.mock.calls[2][0]).toHaveLength(1001)
  let next = 1001
  fetchPage.mockImplementation(async () => page(next++, 1, true))
  await controller.refreshHead()
  expect(fetchPage).toHaveBeenCalledTimes(37)
  expect(controller.getState().hasNewer).toBe(true)
  expect(applyReplay).toHaveBeenCalledTimes(3)
  fetchPage.mockResolvedValueOnce(page(1033, 1))
  expect((await controller.refreshHead()).applied).toBe(true)
  expect(applyReplay.mock.calls[3][0]).toHaveLength(1034)
})
