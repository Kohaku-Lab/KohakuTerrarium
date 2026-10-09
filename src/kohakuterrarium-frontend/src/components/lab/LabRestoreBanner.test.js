import { flushPromises, mount } from "@vue/test-utils"
import { beforeEach, describe, expect, it, vi } from "vitest"

const api = vi.hoisted(() => ({
  getRestoreState: vi.fn(),
  retryRestore: vi.fn(),
  dismissRestore: vi.fn(),
}))
const message = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }))
vi.mock("@/utils/api", () => ({ sessionAPI: api }))
vi.mock("@/utils/i18n", () => ({
  useI18n: () => ({ t: (k, p) => (p ? `${k}:${JSON.stringify(p)}` : k) }),
}))
vi.mock("element-plus", () => ({ ElMessage: message }))

import LabRestoreBanner from "./LabRestoreBanner.vue"

const failedRow = {
  path: "/s/beta.kohakutr",
  session_id: "beta",
  failed: "pwd missing",
  restored_this_boot: false,
}
const okRow = {
  path: "/s/alpha.kohakutr",
  session_id: "alpha",
  failed: null,
  restored_this_boot: true,
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.useRealTimers()
})

describe("LabRestoreBanner", () => {
  it("shows nothing when every session came back", async () => {
    api.getRestoreState.mockResolvedValue({ running: false, rows: [okRow] })
    const w = mount(LabRestoreBanner)
    await flushPromises()
    expect(w.find("[data-test='lab-restore']").exists()).toBe(false)
    expect(w.emitted("restored")).toHaveLength(1)
  })

  it("polls while restoring, then lists failures", async () => {
    vi.useFakeTimers()
    api.getRestoreState
      .mockResolvedValueOnce({
        running: true,
        rows: [
          { ...okRow, restored_this_boot: false },
          { ...failedRow, failed: null },
        ],
      })
      .mockResolvedValueOnce({ running: false, rows: [okRow, failedRow] })
    const w = mount(LabRestoreBanner)
    await flushPromises()
    expect(w.text()).toContain('lab.restore.restoring:{"n":2}')
    expect(w.emitted("restored")).toBeUndefined()
    await vi.advanceTimersByTimeAsync(2000)
    await flushPromises()
    expect(w.text()).toContain('lab.restore.failed:{"n":1}')
    expect(w.find("[data-test='lab-restore-beta']").text()).toContain("pwd missing")
    expect(w.emitted("restored")).toHaveLength(1)
  })

  it("retries and dismisses a failed session, then re-reads", async () => {
    api.getRestoreState
      .mockResolvedValueOnce({ running: false, rows: [failedRow] })
      .mockResolvedValueOnce({ running: false, rows: [failedRow] })
      .mockResolvedValue({ running: false, rows: [] })
    api.retryRestore.mockResolvedValue({ status: "failed", error: "still missing" })
    const w = mount(LabRestoreBanner)
    await flushPromises()
    await w.find("[data-test='lab-restore-retry']").trigger("click")
    await flushPromises()
    expect(api.retryRestore).toHaveBeenCalledWith("/s/beta.kohakutr")
    expect(message.error).toHaveBeenCalledWith("still missing")
    await w.find("[data-test='lab-restore-dismiss']").trigger("click")
    await flushPromises()
    expect(api.dismissRestore).toHaveBeenCalledWith("/s/beta.kohakutr")
    expect(w.find("[data-test='lab-restore']").exists()).toBe(false)
  })
})
