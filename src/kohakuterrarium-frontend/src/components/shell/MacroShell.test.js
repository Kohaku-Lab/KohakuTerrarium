import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { createRouter, createMemoryHistory } from "vue-router"

vi.mock("@/utils/api", () => ({
  attachAPI: { getCreaturePolicies: vi.fn(), getSessionPolicies: vi.fn() },
  configAPI: { listCreatures: vi.fn(), listTerrariums: vi.fn(), getServerInfo: vi.fn() },
  settingsAPI: {
    getBackends: vi.fn().mockResolvedValue([]),
    listMCP: vi.fn().mockResolvedValue([]),
  },
  statsAPI: {
    diskUsage: vi.fn().mockResolvedValue({ count: 0, total_bytes: 0 }),
    metrics: vi.fn().mockResolvedValue({ rates: { llm: [], error: [] }, histograms: {} }),
    sessionStats: vi.fn().mockResolvedValue({ by_recency: { "1d": 0 } }),
  },
  terrariumAPI: { list: vi.fn().mockResolvedValue([]) },
  agentAPI: { list: vi.fn().mockResolvedValue([]) },
}))

import MacroShell from "./MacroShell.vue"
import RailItem from "./RailItem.vue"
import { useTabsStore } from "@/stores/tabs"
import { useInstancesStore } from "@/stores/instances"
import { tabKinds, inspectorInnerTabs, railGroups } from "@/stores/tabKindRegistry"

let storage

beforeEach(() => {
  storage = new Map()
  vi.stubGlobal("localStorage", {
    getItem: (k) => (storage.has(k) ? storage.get(k) : null),
    setItem: (k, v) => storage.set(k, String(v)),
    removeItem: (k) => storage.delete(k),
    clear: () => storage.clear(),
    get length() {
      return storage.size
    },
    key: (i) => Array.from(storage.keys())[i] ?? null,
  })
  setActivePinia(createPinia())
  tabKinds.clear()
  inspectorInnerTabs.clear()
  railGroups.clear()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [{ path: "/", component: { template: "<div />" } }],
  })
}

describe("MacroShell — render", () => {
  it("mounts and shows the rail + tab strip + content", async () => {
    const router = makeRouter()
    const wrapper = mount(MacroShell, {
      global: { plugins: [router] },
    })
    await router.isReady()
    // Rail brand
    expect(wrapper.text()).toContain("Kohaku")
    expect(wrapper.text()).toContain("Terrarium")
    // Quick group entries
    expect(wrapper.text()).toContain("Catalog")
    expect(wrapper.text()).toContain("Studio")
    expect(wrapper.text()).toContain("Settings")
    // Pinned group placeholder
    expect(wrapper.text()).toContain("No pinned tabs")
  })

  it("opens a default Dashboard tab on mount when none in URL", async () => {
    const router = makeRouter()
    const wrapper = mount(MacroShell, {
      global: { plugins: [router] },
    })
    await router.isReady()
    await wrapper.vm.$nextTick()
    const tabs = useTabsStore()
    // After onMounted, dashboard tab should exist.
    expect(tabs.tabs.some((t) => t.kind === "dashboard")).toBe(true)
  })

  it("renders one RailItem per running instance", async () => {
    const router = makeRouter()
    const instances = useInstancesStore()
    instances.list = [
      {
        id: "agent-1",
        config_name: "alice",
        type: "creature",
        status: "running",
      },
      {
        id: "graph-1",
        config_name: "swe-graph",
        type: "terrarium",
        status: "running",
      },
    ]
    const wrapper = mount(MacroShell, {
      global: { plugins: [router] },
    })
    await router.isReady()
    const items = wrapper.findAllComponents(RailItem)
    expect(items).toHaveLength(2)
    expect(wrapper.text()).toContain("alice")
    expect(wrapper.text()).toContain("swe-graph")
  })
})

describe("MacroShell — density branch", () => {
  it("renders CompactShell (not RailPane) at compact density", async () => {
    // Narrow viewport BEFORE the composable initializes.
    window.innerWidth = 600
    const { _resetDensityForTests } = await import("@/composables/useDensity")
    _resetDensityForTests()

    const router = makeRouter()
    const wrapper = mount(MacroShell, {
      global: { plugins: [router] },
    })
    await router.isReady()
    // CompactShell renders a hamburger + density-override button; the
    // regular shell renders the BrandMark with full "Kohaku Terrarium"
    // text and rail group entries like "No pinned tabs".
    expect(wrapper.text()).not.toContain("No pinned tabs")

    // Restore for downstream tests.
    window.innerWidth = 1024
    _resetDensityForTests()
  })

  it("renders the regular shell at desktop viewport", async () => {
    window.innerWidth = 1024
    const { _resetDensityForTests } = await import("@/composables/useDensity")
    _resetDensityForTests()

    const router = makeRouter()
    const wrapper = mount(MacroShell, {
      global: { plugins: [router] },
    })
    await router.isReady()
    expect(wrapper.text()).toContain("No pinned tabs")
  })
})

describe("MacroShell — surface indicators", () => {
  it("rail [C] click opens an attach tab; second click closes", async () => {
    const router = makeRouter()
    const instances = useInstancesStore()
    instances.list = [
      {
        id: "agent-1",
        config_name: "alice",
        type: "creature",
        status: "running",
      },
    ]
    const wrapper = mount(MacroShell, {
      global: { plugins: [router] },
    })
    await router.isReady()
    const tabs = useTabsStore()
    const railItem = wrapper.findComponent(RailItem)
    // Find the [C] button (first surface indicator)
    const buttons = railItem.findAll("button")
    const cBtn = buttons.find((b) => b.text() === "C")
    expect(cBtn).toBeDefined()
    await cBtn.trigger("click")
    expect(tabs.surfaceTabsForTarget("agent-1").chat).toBeDefined()
    await cBtn.trigger("click")
    expect(tabs.surfaceTabsForTarget("agent-1").chat).toBeUndefined()
  })

  it("rail [I] click opens an inspector tab", async () => {
    const router = makeRouter()
    const instances = useInstancesStore()
    instances.list = [
      {
        id: "agent-1",
        config_name: "alice",
        type: "creature",
        status: "running",
      },
    ]
    const wrapper = mount(MacroShell, {
      global: { plugins: [router] },
    })
    await router.isReady()
    const tabs = useTabsStore()
    const railItem = wrapper.findComponent(RailItem)
    const iBtn = railItem.findAll("button").find((b) => b.text() === "I")
    expect(iBtn).toBeDefined()
    await iBtn.trigger("click")
    expect(tabs.surfaceTabsForTarget("agent-1").inspector).toBeDefined()
  })
})
