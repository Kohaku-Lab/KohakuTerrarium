import { flushPromises, mount } from "@vue/test-utils"
import { beforeEach, describe, expect, it, vi } from "vitest"

const api = vi.hoisted(() => ({
  getExchanges: vi.fn(),
  setTitle: vi.fn(),
  setSummaryText: vi.fn(),
  refreshSummary: vi.fn(),
}))
const box = vi.hoisted(() => ({ prompt: vi.fn(), success: vi.fn(), error: vi.fn() }))
vi.mock("@/utils/api", () => ({ sessionAPI: api }))
vi.mock("@/utils/i18n", () => ({
  useI18n: () => ({ t: (k, p) => (p ? `${k}:${JSON.stringify(p)}` : k) }),
}))
vi.mock("element-plus", () => ({
  ElMessage: { success: box.success, error: box.error },
  ElMessageBox: { prompt: box.prompt },
}))

import SessionIdentityCard from "./SessionIdentityCard.vue"

const stubs = {
  ElButton: {
    emits: ["click"],
    template: "<button type='button' @click=\"$emit('click')\"><slot /></button>",
  },
}
const mountCard = () =>
  mount(SessionIdentityCard, { props: { sessionName: "pair_ab12" }, global: { stubs } })

beforeEach(() => {
  vi.clearAllMocks()
  api.getExchanges.mockResolvedValue({
    title: "Nightly triage",
    summary: "Triaging CI",
    summary_source: "user",
    exchanges: [{ turn: 2, user: "Retry it", reply: "Done." }],
  })
})

describe("SessionIdentityCard", () => {
  it("shows the name, summary with its source, and the latest exchange", async () => {
    const w = mountCard()
    await flushPromises()
    expect(w.find("[data-test='viewer-title']").text()).toBe("Nightly triage")
    expect(w.find("[data-test='viewer-summary']").text()).toContain("Triaging CI")
    expect(w.find("[data-test='viewer-summary']").text()).toContain("lab.history.summary.user")
    expect(w.find("[data-test='history-exchange-2']").text()).toContain("Done.")
    expect(api.getExchanges).toHaveBeenCalledWith("pair_ab12", 1)
  })

  it("falls back to the storage name and says when there is no summary", async () => {
    api.getExchanges.mockResolvedValue({ title: "", summary: "", exchanges: [] })
    const w = mountCard()
    await flushPromises()
    expect(w.find("[data-test='viewer-title']").text()).toBe("pair_ab12")
    expect(w.find("[data-test='viewer-summary']").text()).toBe("sessionViewer.overview.noSummary")
  })

  it("renames, edits and regenerates, re-reading after each save", async () => {
    const w = mountCard()
    await flushPromises()
    box.prompt.mockResolvedValueOnce({ value: "Triage" })
    await w.find("[data-test='viewer-rename']").trigger("click")
    await flushPromises()
    expect(api.setTitle).toHaveBeenCalledWith("pair_ab12", "Triage")
    box.prompt.mockResolvedValueOnce({ value: "" })
    await w.find("[data-test='viewer-edit-summary']").trigger("click")
    await flushPromises()
    expect(api.setSummaryText).toHaveBeenCalledWith("pair_ab12", "")
    await w.find("[data-test='viewer-regenerate']").trigger("click")
    await flushPromises()
    expect(api.refreshSummary).toHaveBeenCalledWith("pair_ab12")
    expect(api.getExchanges.mock.calls.filter((c) => c[1] === 1).length).toBeGreaterThanOrEqual(7)
  })
})
