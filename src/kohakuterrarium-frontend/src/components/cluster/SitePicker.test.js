import { mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("@/composables/useVisibilityInterval", () => ({
  createVisibilityInterval: vi.fn(() => ({ start: vi.fn(), stop: vi.fn() })),
}))

vi.mock("@/utils/api", () => ({
  nodesAPI: { list: vi.fn(), status: vi.fn(), deployCreature: vi.fn() },
}))

import { nodesAPI } from "@/utils/api"
import { useClusterStore } from "@/stores/cluster"
import SitePicker from "./SitePicker.vue"
import SiteChip from "./SiteChip.vue"

function notFoundError() {
  const e = new Error("404")
  e.response = { status: 404 }
  return e
}

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
  vi.clearAllMocks()
})

describe("SitePicker", () => {
  it("renders nothing in standalone mode", async () => {
    nodesAPI.list.mockRejectedValueOnce(notFoundError())
    const cluster = useClusterStore()
    await cluster.hydrate()
    const w = mount(SitePicker, { props: { modelValue: "_host" } })
    expect(w.find("select").exists()).toBe(false)
  })

  it("renders nothing in lab-host with only 1 site", async () => {
    nodesAPI.list.mockResolvedValueOnce({
      nodes: [{ node_id: "_host", is_host: true, status: "online", creatures: 0 }],
    })
    const cluster = useClusterStore()
    await cluster.hydrate()
    const w = mount(SitePicker, { props: { modelValue: "_host" } })
    expect(w.find("select").exists()).toBe(false)
  })

  it("renders dropdown with options when ≥2 sites", async () => {
    nodesAPI.list.mockResolvedValueOnce({
      nodes: [
        { node_id: "_host", is_host: true, status: "online", creatures: 0 },
        { node_id: "worker-1", is_host: false, status: "online", creatures: 0 },
      ],
    })
    const cluster = useClusterStore()
    await cluster.hydrate()
    const w = mount(SitePicker, { props: { modelValue: "_host" } })
    const opts = w.findAll("option")
    expect(opts).toHaveLength(2)
    expect(opts[1].element.value).toBe("worker-1")
  })

  it("emits update:modelValue on change", async () => {
    nodesAPI.list.mockResolvedValueOnce({
      nodes: [
        { node_id: "_host", is_host: true, status: "online", creatures: 0 },
        { node_id: "worker-1", is_host: false, status: "online", creatures: 0 },
      ],
    })
    const cluster = useClusterStore()
    await cluster.hydrate()
    const w = mount(SitePicker, { props: { modelValue: "_host" } })
    await w.find("select").setValue("worker-1")
    expect(w.emitted("update:modelValue")[0]).toEqual(["worker-1"])
  })
})

describe("SiteChip", () => {
  it("hidden in standalone mode by default", async () => {
    nodesAPI.list.mockRejectedValueOnce(notFoundError())
    const cluster = useClusterStore()
    await cluster.hydrate()
    const w = mount(SiteChip, { props: { nodeId: "_host" } })
    expect(w.find("span").exists()).toBe(false)
  })

  it("hidden in lab-host with one site by default", async () => {
    nodesAPI.list.mockResolvedValueOnce({
      nodes: [{ node_id: "_host", is_host: true, status: "online", creatures: 0 }],
    })
    const cluster = useClusterStore()
    await cluster.hydrate()
    const w = mount(SiteChip, { props: { nodeId: "_host" } })
    expect(w.find("span").exists()).toBe(false)
  })

  it("visible when cluster has ≥2 sites", async () => {
    nodesAPI.list.mockResolvedValueOnce({
      nodes: [
        { node_id: "_host", is_host: true, status: "online", creatures: 0 },
        { node_id: "worker-1", is_host: false, status: "online", creatures: 0 },
      ],
    })
    const cluster = useClusterStore()
    await cluster.hydrate()
    const w = mount(SiteChip, { props: { nodeId: "worker-1" } })
    expect(w.text()).toContain("worker-1")
  })

  it("alwaysShow renders even in standalone", async () => {
    nodesAPI.list.mockRejectedValueOnce(notFoundError())
    const cluster = useClusterStore()
    await cluster.hydrate()
    const w = mount(SiteChip, { props: { nodeId: "_host", alwaysShow: true } })
    expect(w.find("span").exists()).toBe(true)
    // Renders the host label.
    expect(w.text()).toMatch(/host/i)
  })
})
