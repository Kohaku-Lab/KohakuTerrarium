import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest"

const statsAPI = vi.hoisted(() => ({ diskUsage: vi.fn(), metrics: vi.fn() }))
const sessionApi = vi.hoisted(() => ({
  list: vi.fn(),
  stopActive: vi.fn(async () => ({})),
  getRestoreState: vi.fn(async () => ({ running: false, rows: [], outcomes: [] })),
  getExchanges: vi.fn(),
}))
const messages = vi.hoisted(() => ({ confirm: vi.fn(), error: vi.fn() }))
const observed = vi.hoisted(() => ({ size: { width: 1000, height: 700 } }))
vi.mock("@/utils/api", () => ({
  runtimeGraphAPI: { snapshot: vi.fn(() => new Promise(() => {})) },
  statsAPI,
  sessionAPI: sessionApi,
}))
vi.mock("element-plus", () => ({
  ElMessage: { error: messages.error, success: vi.fn() },
  ElMessageBox: { confirm: messages.confirm },
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
  default: {
    name: "NewSessionDialog",
    props: ["mode", "initialConfig"],
    template: "<div data-test='new-dialog' :data-mode='mode' :data-config='initialConfig' />",
  },
}))

import LabPage from "./LabPage.vue"
import { TILE } from "@/components/lab/model/labSessions"
import { _resetDensityForTests, useDensity } from "@/composables/useDensity"
import { useGraphLiveStore } from "@/stores/graph/live"
import { useTabsStore } from "@/stores/tabs"

beforeAll(() => {
  globalThis.ResizeObserver = class {
    constructor(cb) {
      this.cb = cb
    }
    observe() {
      this.cb([{ contentRect: observed.size }])
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
const graph = (id, name, extra = {}) => ({
  graph_id: id,
  name,
  session_name: `${id}_saved`,
  channels: [{ name: "tasks" }],
  creatures: [
    creature(`${id}-boss`, { is_privileged: true }),
    creature(`${id}-w`, { send_channels: ["tasks"], is_processing: true }),
  ],
  ...extra,
})
const SNAPSHOT = { graphs: [graph("g1", "alpha")] }
const SAVED = [
  {
    name: "saved_ab12",
    terrarium_name: "Research notes",
    config_path: "@kt-biome/terrariums/deep_research",
    config_type: "terrarium",
    agents: ["a", "b"],
    stop_reason: "crash",
  },
  { name: "saved_cd34", config_path: "@kt-biome/creatures/general", agents: ["a"] },
]

let wrapper
beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  localStorage.clear()
  observed.size = { width: 1000, height: 700 }
  statsAPI.diskUsage.mockResolvedValue({ count: 7, total_bytes: 2 * 1024 * 1024 })
  statsAPI.metrics.mockResolvedValue({})
  sessionApi.list.mockResolvedValue({ sessions: SAVED, total: 21 })
  sessionApi.getExchanges.mockImplementation(async (name) => ({
    title: "",
    summary: `About ${name}`,
    exchanges: [{ turn: 2, user: "Why is CI red?", reply: "A flaky timeout." }],
  }))
  _resetDensityForTests()
})
afterEach(() => {
  wrapper?.unmount()
  useDensity().setOverride("auto")
})

async function mountLab(snapshot = SNAPSHOT) {
  const live = useGraphLiveStore()
  wrapper = mount(LabPage, { attachTo: document.body })
  live.snapshot = snapshot
  live.loading = false
  await flushPromises()
  return { live, w: wrapper }
}

describe("LabPage on a desktop", () => {
  it("shows the counts, each running session as a tile with its graph, and the recent sessions with the numbers", async () => {
    const { w } = await mountLab({
      graphs: [
        graph("g1", "alpha", {
          creatures: [...SNAPSHOT.graphs[0].creatures, creature("far", { home_node: "gpu" })],
        }),
      ],
    })
    expect(w.find("[data-test='lab-count-running']").text()).toContain("1")
    expect(w.find("[data-test='lab-count-creatures']").text()).toContain("3")
    expect(w.find("[data-test='lab-count-busy']").text()).toContain("1")
    expect(w.find("[data-test='lab-count-machines']").text()).toContain("2")
    const tile = w.find("[data-test='lab-tile-g1']")
    expect(tile.find("[data-test='lab-tile-name']").text()).toBe("alpha")
    expect(tile.find("[data-test='lab-tile-text']").text()).toBe("About g1_saved")
    expect(tile.find("[data-test='lab-mini-graph']").exists()).toBe(true)
    expect(tile.find("[data-test='lab-tile-quote']").exists()).toBe(false)
    expect(w.find("[data-test='lab-recent-saved_ab12']").text()).toContain("Research notes")
    expect(w.find("[data-test='lab-recent-status-crashed']").exists()).toBe(true)
    expect(w.find("[data-test='lab-recent']").text()).toContain("21")
    expect(w.find("[data-test='lab-stat-saved']").text()).toContain("7")
    expect(w.find("[data-test='lab-empty']").exists()).toBe(false)
    expect(w.findAll("[data-test='lab-new']")).toHaveLength(1)
  })

  it("drops the graph, then the second summary line, only as tiles stop fitting the region", async () => {
    const fleet = (n) => ({ graphs: Array.from({ length: n }, (_, i) => graph(`g${i}`, `s${i}`)) })
    observed.size = {
      width: 3 * TILE.minWidth + 2 * TILE.gap,
      height: 2 * TILE.height.full + TILE.gap,
    }
    const { w, live } = await mountLab(fleet(6))
    const count = (test) => w.findAll(`[data-test='${test}']`).length
    const textClass = () => w.find("[data-test='lab-tile-text']").classes()
    expect([count("lab-mini-graph"), count("lab-tile-text"), count("lab-tile-expand")]).toEqual([
      6, 6, 6,
    ])
    expect(textClass()).toContain("line-clamp-2")
    live.snapshot = fleet(7)
    await flushPromises()
    expect([count("lab-mini-graph"), count("lab-tile-text"), count("lab-tile-expand")]).toEqual([
      0, 7, 7,
    ])
    live.snapshot = fleet(22)
    await flushPromises()
    expect([count("lab-mini-graph"), count("lab-tile-text"), count("lab-tile-expand")]).toEqual([
      0, 22, 0,
    ])
    expect(textClass()).toContain("truncate")
  })

  it("looks inside a session in place, switches with the chip strip, and comes back with ← or Esc", async () => {
    const { w } = await mountLab({ graphs: [graph("g1", "alpha"), graph("g2", "beta")] })
    await w.find("[data-test='lab-tile-g1']").trigger("click")
    expect(
      w.find("[data-test='lab-focus'] [data-test='graph-surface']").attributes("data-session"),
    ).toBe("g1")
    await w.find("[data-test='lab-focus-chip-g2']").trigger("click")
    expect(w.find("[data-test='graph-surface']").attributes("data-session")).toBe("g2")
    await w.find("[data-test='lab-back']").trigger("click")
    expect(w.find("[data-test='lab-focus']").exists()).toBe(false)
    await w.find("[data-test='lab-tile-g1']").trigger("keydown", { key: "Enter" })
    const input = document.createElement("input")
    document.body.appendChild(input)
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }))
    await flushPromises()
    expect(w.find("[data-test='lab-focus']").exists()).toBe(true)
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }))
    await flushPromises()
    expect(w.find("[data-test='lab-focus']").exists()).toBe(false)
    input.remove()
  })

  it("returns from a session that ends while looked at, to the empty bench with quick starts", async () => {
    const { w, live } = await mountLab()
    await w.find("[data-test='lab-tile-g1']").trigger("click")
    live.snapshot = { graphs: [] }
    await flushPromises()
    expect(w.find("[data-test='lab-focus']").exists()).toBe(false)
    expect(w.find("[data-test='lab-empty']").exists()).toBe(true)
    expect(w.find("[data-test='lab-counts']").text()).toBe("lab.nothingRunning")
    await w.find("[data-test='lab-quick-deep_research']").trigger("click")
    const dialog = w.find("[data-test='new-dialog']")
    expect(dialog.attributes("data-mode")).toBe("terrarium")
    expect(dialog.attributes("data-config")).toBe("@kt-biome/terrariums/deep_research")
  })

  it("opens chat and inspector from the focus bar, and a blank New session from the header", async () => {
    const tabs = useTabsStore()
    const openSurface = vi.spyOn(tabs, "openSurface").mockResolvedValue()
    const { w } = await mountLab()
    await w.find("[data-test='lab-tile-open']").trigger("click")
    expect(openSurface).toHaveBeenCalledWith("g1", "chat", { config_name: "alpha" })
    expect(w.find("[data-test='lab-focus']").exists()).toBe(false)
    openSurface.mockClear()
    await w.find("[data-test='lab-tile-g1']").trigger("click")
    await w.find("[data-test='lab-focus-open']").trigger("click")
    expect(openSurface).toHaveBeenCalledWith("g1", "chat", { config_name: "alpha" })
    await w.find("[data-test='lab-focus-inspector']").trigger("click")
    expect(openSurface).toHaveBeenCalledWith("g1", "inspector", { config_name: "alpha" })
    await w.find("[data-test='lab-new']").trigger("click")
    expect(w.find("[data-test='new-dialog']").attributes("data-mode")).toBe("creature")
    expect(w.find("[data-test='new-dialog']").attributes("data-config")).toBe("")
  })

  it("toggles the graph of every session over the bench and remembers it", async () => {
    const tabs = useTabsStore()
    const openTab = vi.spyOn(tabs, "openTab")
    const { w } = await mountLab()
    await w.find("[data-test='lab-graph-all']").trigger("click")
    expect(
      w.find("[data-test='lab-graph'] [data-test='graph-surface']").attributes("data-session"),
    ).toBeUndefined()
    expect(localStorage.getItem("kt.lab.view")).toBe("graph")
    expect(openTab).not.toHaveBeenCalled()
    await w.find("[data-test='lab-graph-all']").trigger("click")
    expect(w.find("[data-test='lab-graph']").exists()).toBe(false)
    expect(localStorage.getItem("kt.lab.view")).toBe("bench")
  })

  it("offers a session menu from ⋯ and right-click: look inside, open, inspector, and stop after confirming", async () => {
    const tabs = useTabsStore()
    const openSurface = vi.spyOn(tabs, "openSurface").mockResolvedValue()
    const { w } = await mountLab()
    const menuItem = (label) =>
      [...document.querySelectorAll("[role='menuitem']")].find((b) => b.textContent.includes(label))
    await w.find("[data-test='lab-tile-more']").trigger("click")
    expect(document.querySelector("[role='menu']").textContent).toContain("alpha")
    expect(w.find("[data-test='lab-focus']").exists()).toBe(false)
    menuItem("lab.menu.inspector").click()
    await flushPromises()
    expect(openSurface).toHaveBeenCalledWith("g1", "inspector", { config_name: "alpha" })
    expect(document.querySelector("[role='menu']")).toBeNull()
    await w.find("[data-test='lab-tile-g1']").trigger("contextmenu", { clientX: 40, clientY: 50 })
    menuItem("lab.menu.inside").click()
    await flushPromises()
    expect(w.find("[data-test='graph-surface']").attributes("data-session")).toBe("g1")
    await w.find("[data-test='lab-back']").trigger("click")
    messages.confirm.mockRejectedValueOnce(new Error("cancel"))
    await w.find("[data-test='lab-tile-g1']").trigger("contextmenu", { clientX: 40, clientY: 50 })
    menuItem("graph.action.stopSession").click()
    await flushPromises()
    expect(sessionApi.stopActive).not.toHaveBeenCalled()
    messages.confirm.mockResolvedValueOnce()
    await w.find("[data-test='lab-tile-g1']").trigger("contextmenu", { clientX: 40, clientY: 50 })
    menuItem("graph.action.stopSession").click()
    await flushPromises()
    expect(sessionApi.stopActive).toHaveBeenCalledWith("g1")
  })

  it("moves message dots while a channel carries messages", async () => {
    const { w, live } = await mountLab()
    expect(w.find("[data-test='lab-tile-g1'] circle").exists()).toBe(false)
    live.handleEvent({
      type: "channel_message",
      graph_id: "g1",
      channel: "tasks",
      sender: "w",
      content_preview: "done",
    })
    await flushPromises()
    expect(w.find("[data-test='lab-tile-g1'] circle").exists()).toBe(true)
  })

  it("shows each tile's summary, never a prompt in its place, read again only after work", async () => {
    const working = graph("g1", "alpha")
    const resting = graph("g1", "alpha", {
      creatures: working.creatures.map((c) => ({ ...c, is_processing: false })),
    })
    const { w, live } = await mountLab({
      graphs: [working, graph("g2", "beta", { session_name: "" })],
    })
    const reads = () => sessionApi.getExchanges.mock.calls.filter(([, limit]) => limit === 1)
    const tile = w.find("[data-test='lab-tile-g1']")
    const summary = () => tile.find("[data-test='lab-tile-summary']").text()
    expect(summary()).toBe("About g1_saved")
    expect(tile.find("[data-test='lab-tile-name']").text()).toBe("alpha")
    expect(w.find("[data-test='lab-tile-g2'] [data-test='lab-tile-summary']").text()).toBe(
      "lab.session.noSummary",
    )
    expect(reads()).toEqual([["g1_saved", 1]])
    live.snapshot = { graphs: [{ ...working }] }
    await flushPromises()
    expect(reads()).toHaveLength(1)
    sessionApi.getExchanges.mockResolvedValueOnce({
      title: "Nightly",
      summary: "",
      exchanges: [{ turn: 3, user: "Rerun it", reply: "Green now." }],
    })
    live.snapshot = { graphs: [resting] }
    await flushPromises()
    expect(reads()).toHaveLength(2)
    expect(summary()).toBe("lab.session.noSummary")
    expect(tile.text()).not.toContain("Rerun it")
    expect(tile.find("[data-test='lab-tile-name']").text()).toBe("Nightly")
    sessionApi.getExchanges.mockResolvedValueOnce({ summary: "Green CI", exchanges: [] })
    live.snapshot = { graphs: [working] }
    await flushPromises()
    live.snapshot = { graphs: [resting] }
    await flushPromises()
    expect(summary()).toBe("Green CI")
    sessionApi.getExchanges.mockRejectedValueOnce(new Error("gone"))
    live.snapshot = { graphs: [working] }
    await flushPromises()
    live.snapshot = { graphs: [resting] }
    await flushPromises()
    expect(reads()).toHaveLength(4)
    expect(summary()).toBe("Green CI")
  })

  it("quotes a tile's latest messages only behind its chevron, without looking inside", async () => {
    const { w } = await mountLab({
      graphs: [graph("g1", "alpha"), graph("g2", "beta", { session_name: "" })],
    })
    const tile = w.find("[data-test='lab-tile-g1']")
    expect(tile.text()).not.toContain("Why is CI red?")
    expect(w.find("[data-test='lab-tile-g2'] [data-test='lab-tile-expand']").exists()).toBe(false)
    await tile.find("[data-test='lab-tile-expand']").trigger("click")
    await flushPromises()
    expect(sessionApi.getExchanges).toHaveBeenCalledWith("g1_saved", 2)
    const quote = tile.find("[data-test='lab-tile-quote']")
    expect(quote.text()).toContain("Why is CI red?")
    expect(quote.text()).toContain("A flaky timeout.")
    expect(tile.find("[data-test='lab-tile-expand']").attributes("aria-expanded")).toBe("true")
    expect(w.find("[data-test='lab-focus']").exists()).toBe(false)
    await quote.trigger("click")
    expect(w.find("[data-test='lab-focus']").exists()).toBe(false)
    await tile.find("[data-test='lab-tile-expand']").trigger("click")
    expect(tile.find("[data-test='lab-tile-quote']").exists()).toBe(false)
  })

  it("leads recent rows with their summary and the title the user gave", async () => {
    sessionApi.list.mockResolvedValue({
      total: 1,
      sessions: [{ name: "s_1", title: "Nightly", summary: "Triaging the CI timeout" }],
    })
    const { w } = await mountLab()
    const row = w.find("[data-test='lab-recent-s_1']")
    expect(row.find("[data-test='lab-recent-headline']").text()).toBe("Triaging the CI timeout")
    expect(row.find("[data-test='lab-recent-title']").text()).toBe("Nightly")
  })

  it("re-reads the recent list when the running set changes", async () => {
    const { live } = await mountLab()
    expect(sessionApi.list).toHaveBeenCalledTimes(1)
    live.snapshot = { graphs: [graph("g1", "alpha"), graph("g2", "beta")] }
    await flushPromises()
    expect(sessionApi.list).toHaveBeenCalledTimes(2)
  })
})

describe("LabPage on a phone", () => {
  beforeEach(() => useDensity().setOverride("compact"))

  it("lists running and recent sessions as rows with one New session footer", async () => {
    const { w } = await mountLab()
    expect(w.find("[data-test='lab']").exists()).toBe(false)
    const row = w.find("[data-test='lab-phone-session-g1']")
    expect(row.find("[data-test='lab-phone-headline']").text()).toBe("About g1_saved")
    expect(row.text()).toContain("alpha")
    expect(w.find("[data-test='lab-phone-recent-saved_ab12']").text()).toContain("Research notes")
    await w.find("[data-test='lab-new']").trigger("click")
    expect(w.find("[data-test='new-dialog']").exists()).toBe(true)
    w.unmount()
    sessionApi.getExchanges.mockRejectedValue(new Error("gone"))
    const quiet = await mountLab({ graphs: [graph("g3", "gamma")] })
    const bare = quiet.w.find("[data-test='lab-phone-session-g3']")
    expect(bare.find("[data-test='lab-phone-headline']").text()).toBe("gamma")
    expect(bare.text()).toContain("lab.session.noSummary")
  })

  it("opens a running session's chat straight from its row", async () => {
    const tabs = useTabsStore()
    const openSurface = vi.spyOn(tabs, "openSurface").mockResolvedValue()
    const { w } = await mountLab()
    await w.find("[data-test='lab-phone-chat-g1']").trigger("click")
    expect(openSurface).toHaveBeenCalledWith("g1", "chat", { config_name: "alpha" })
    expect(w.find("[data-test='lab-phone-page']").exists()).toBe(false)
  })

  it("opens a session page with its creatures, its live graph and its chat, and goes back", async () => {
    const tabs = useTabsStore()
    const openSurface = vi.spyOn(tabs, "openSurface").mockResolvedValue()
    const { w } = await mountLab()
    await w.find("[data-test='lab-phone-session-g1']").trigger("click")
    const page = w.find("[data-test='lab-phone-page']")
    expect(page.find("[data-test='lab-phone-summary']").text()).toBe("About g1_saved")
    expect(page.text()).not.toContain("Why is CI red?")
    await page.find("[data-test='lab-phone-quote-toggle']").trigger("click")
    await flushPromises()
    expect(page.find("[data-test='lab-phone-quote']").text()).toContain("Why is CI red?")
    expect(page.find("[data-test='lab-mini-graph']").exists()).toBe(true)
    expect(page.find("[data-test='lab-phone-creature-g1-boss']").text()).toContain("g1-boss")
    await page.find("[data-test='lab-phone-graph']").trigger("click")
    expect(w.find("[data-test='graph-surface']").attributes("data-session")).toBe("g1")
    await w.find("[data-test='phone-page-back']").trigger("click")
    expect(w.find("[data-test='graph-surface']").exists()).toBe(false)
    await w.find("[data-test='lab-phone-open']").trigger("click")
    expect(openSurface).toHaveBeenCalledWith("g1", "chat", { config_name: "alpha" })
    await w.find("[data-test='phone-page-back']").trigger("click")
    expect(w.find("[data-test='lab-phone-page']").exists()).toBe(false)
  })

  it("resumes a recent session from its row button and views it from the row", async () => {
    const tabs = useTabsStore()
    const createSession = vi.spyOn(tabs, "createSession").mockResolvedValue("graph_9")
    const openTab = vi.spyOn(tabs, "openTab")
    const { w } = await mountLab()
    const row = w.find("[data-test='lab-phone-recent-saved_ab12']")
    await row.find("[data-test='lab-phone-resume']").trigger("click")
    await flushPromises()
    expect(createSession).toHaveBeenCalledWith(
      expect.objectContaining({ kind: "resume", sessionName: "saved_ab12", attachMode: "chat" }),
    )
    await row.find("button").trigger("click")
    expect(openTab).toHaveBeenCalledWith(expect.objectContaining({ id: "session:saved_ab12" }))
  })
})
