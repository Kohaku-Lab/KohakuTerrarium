import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("vue-router", () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ replace: vi.fn() }),
}))

import CostTab from "./CostTab.vue"
import { useSessionDetailStore } from "@/stores/sessionDetail"
import { useTurnRollupStore } from "@/stores/turnRollup"

beforeEach(() => {
  const storage = new Map()
  vi.stubGlobal("localStorage", {
    getItem: (k) => (storage.has(k) ? storage.get(k) : null),
    setItem: (k, v) => storage.set(k, String(v)),
    removeItem: (k) => storage.delete(k),
  })
  setActivePinia(createPinia())
})

describe("CostTab — aggregate header uses summary totals (UXI-03)", () => {
  it("header tokens come from summary.totals (graph-wide), not the turn-sum", async () => {
    const detail = useSessionDetailStore()
    detail.name = "s1"
    // Graph-wide total (includes channel-driven, turn-less usage).
    detail.summary = {
      totals: { tokens: { prompt: 5000, completion: 1200, cached: 300 }, cost_usd: null },
    }
    const rollup = useTurnRollupStore()
    // Per-turn rows DROP turn_index<=0 usage → summing them under-reports.
    rollup.load = vi.fn().mockResolvedValue()
    rollup.turns = [{ turn_index: 1, tokens_in: 1000, tokens_out: 200, tokens_cached: 100 }]

    const w = mount(CostTab)
    await flushPromises()

    const text = w.text()
    // Header In/Out come from summary (5.0k / 1.2k); the turn-sum would
    // have shown 1.0k / 200 (the per-turn breakdown table still does).
    expect(text).toContain("5.0k")
    expect(text).toContain("1.2k")
  })
})
