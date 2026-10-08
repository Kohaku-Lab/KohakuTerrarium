import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest"

const statsAPI = vi.hoisted(() => ({ diskUsage: vi.fn(), metrics: vi.fn() }))
vi.mock("@/utils/api", () => ({
  runtimeGraphAPI: { snapshot: vi.fn(() => new Promise(() => {})) },
  statsAPI,
  sessionAPI: { list: vi.fn(async () => ({ sessions: [] })) },
}))
vi.mock("@/utils/i18n", () => ({
  useI18n: () => ({ t: (k, p) => (p ? `${k}:${JSON.stringify(p)}` : k) }),
}))
vi.mock("@/components/graph/GraphSurface.vue", () => ({
  default: {
    name: "GraphSurface",
    props: ["storeKey", "sessionId", "lockSession"],
    template: "<div data-test='graph-surface' :data-session='sessionId' />",
  },
}))
vi.mock("@/components/shell/newSession/NewSessionDialog.vue", () => ({
  default: { name: "NewSessionDialog", template: "<div data-test='new-dialog' />" },
}))

import LabPage from "./LabPage.vue"
import { useGraphLiveStore } from "@/stores/graph/live"
import { useTabsStore } from "@/stores/tabs"

beforeAll(() => {
  globalThis.ResizeObserver = class {
    constructor(cb) {
      this.cb = cb
    }
    observe() {
      this.cb([{ contentRect: { width: 1000, height: 700 } }])
    }
    disconnect() {}
  }
  globalThis.WebSocket = undefined
})

const creature = (id, extra = {}) => ({
  creature_id: id,
  name: id,
  running: true,
  listen_channels: [],
  send_channels: [],
  ...extra,
})
const SNAPSHOT = {
  graphs: [
    {
      graph_id: "g1",
      name: "alpha",
      channels: [{ name: "tasks" }],
      creatures: [
        creature("boss", { is_privileged: true }),
        creature("w", { send_channels: ["tasks"], is_processing: true }),
      ],
    },
  ],
}

let wrapper
beforeEach(() => {
  setActivePinia(createPinia())
  statsAPI.diskUsage.mockResolvedValue({ count: 7, total_bytes: 2 * 1024 * 1024 })
  statsAPI.metrics.mockResolvedValue({})
})
afterEach(() => wrapper?.unmount())

async function mountLab(snapshot = SNAPSHOT) {
  const live = useGraphLiveStore()
  wrapper = mount(LabPage, { attachTo: document.body })
  live.snapshot = snapshot
  await flushPromises()
  return { live, w: wrapper }
}

describe("LabPage", () => {
  it("shows each running session as a tank and the bench's numbers", async () => {
    const { w } = await mountLab()
    expect(w.find("[data-test='lab-tank-g1']").text()).toContain("alpha")
    expect(w.find("[data-test='lab-stat-running']").text()).toContain("1")
    expect(w.find("[data-test='lab-stat-creatures']").text()).toContain("2")
    expect(w.find("[data-test='lab-stat-busy']").text()).toContain("1")
    expect(w.find("[data-test='lab-stat-saved']").text()).toContain("7")
    expect(w.find("[data-test='lab-stat-machines']").exists()).toBe(false)
    expect(w.find("[data-test='lab-empty']").exists()).toBe(false)
  })

  it("looks inside a tank in place, and comes back with ← or Esc", async () => {
    const { w } = await mountLab()
    await w.find("[data-test='lab-tank-g1']").trigger("click")
    expect(w.find("[data-test='graph-surface']").attributes("data-session")).toBe("g1")
    expect(w.find("[data-test='lab-history']").exists()).toBe(false)
    await w.find("[data-test='lab-back']").trigger("click")
    expect(w.find("[data-test='graph-surface']").exists()).toBe(false)
    await w.find("[data-test='lab-tank-g1']").trigger("click")
    const input = document.createElement("input")
    document.body.appendChild(input)
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }))
    await flushPromises()
    expect(w.find("[data-test='graph-surface']").exists()).toBe(true)
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }))
    await flushPromises()
    expect(w.find("[data-test='graph-surface']").exists()).toBe(false)
    input.remove()
  })

  it("returns to the bench when the session it looks at ends", async () => {
    const { w, live } = await mountLab()
    await w.find("[data-test='lab-tank-g1']").trigger("click")
    live.snapshot = { graphs: [] }
    live.loading = false
    await flushPromises()
    expect(w.find("[data-test='graph-surface']").exists()).toBe(false)
    expect(w.find("[data-test='lab-empty']").exists()).toBe(true)
  })

  it("opens a session's chat tab, a new-session dialog, and the full graph", async () => {
    const tabs = useTabsStore()
    const openSurface = vi.spyOn(tabs, "openSurface").mockResolvedValue()
    const openTab = vi.spyOn(tabs, "openTab")
    const { w } = await mountLab()
    await w.find("[data-test='lab-tank-g1'] [data-test='lab-tank-open']").trigger("click")
    expect(openSurface).toHaveBeenCalledWith("g1", "chat", { config_name: "alpha" })
    await w.find("[data-test='lab-new']").trigger("click")
    expect(w.find("[data-test='new-dialog']").exists()).toBe(true)
    await w.find("[data-test='lab-open-graph']").trigger("click")
    expect(openTab).toHaveBeenCalledWith({ kind: "graph", id: "graph" })
  })

  it("marks a tank active while its channel carries messages and shows the latest", async () => {
    const { w, live } = await mountLab()
    live.handleEvent({
      type: "channel_message",
      graph_id: "g1",
      channel: "tasks",
      sender: "w",
      content_preview: "done",
    })
    await flushPromises()
    expect(w.find("[data-test='lab-tank-g1']").classes()).toContain("kt-lab-tank--active")
    expect(w.find("[data-test='lab-tank-g1']").text()).toContain("w done")
  })
})
