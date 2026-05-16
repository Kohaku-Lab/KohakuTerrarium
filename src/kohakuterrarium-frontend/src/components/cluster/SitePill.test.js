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
import SitePill from "./SitePill.vue"

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

describe("SitePill", () => {
  it("renders nothing in standalone mode", async () => {
    nodesAPI.list.mockRejectedValueOnce(notFoundError())
    const cluster = useClusterStore()
    await cluster.hydrate()
    const w = mount(SitePill)
    expect(w.find("button").exists()).toBe(false)
  })

  it("renders 'host-only' label when lab-host has 1 site", async () => {
    nodesAPI.list.mockResolvedValueOnce({
      nodes: [{ node_id: "_host", is_host: true, status: "online", creatures: 0 }],
    })
    const cluster = useClusterStore()
    await cluster.hydrate()
    const w = mount(SitePill)
    const btn = w.find("button")
    expect(btn.exists()).toBe(true)
    expect(btn.text()).toMatch(/host-only/i)
  })

  it("renders site count when lab-host has multiple sites", async () => {
    nodesAPI.list.mockResolvedValueOnce({
      nodes: [
        { node_id: "_host", is_host: true, status: "online", creatures: 1 },
        { node_id: "worker-1", is_host: false, status: "online", creatures: 1 },
        { node_id: "worker-2", is_host: false, status: "online", creatures: 1 },
      ],
    })
    const cluster = useClusterStore()
    await cluster.hydrate()
    const w = mount(SitePill)
    expect(w.find("button").text()).toMatch(/3 sites/i)
  })

  it("emits click when pressed", async () => {
    nodesAPI.list.mockResolvedValueOnce({
      nodes: [
        { node_id: "_host", is_host: true, status: "online", creatures: 1 },
        { node_id: "worker-1", is_host: false, status: "online", creatures: 1 },
      ],
    })
    const cluster = useClusterStore()
    await cluster.hydrate()
    const w = mount(SitePill)
    await w.find("button").trigger("click")
    expect(w.emitted("click")).toBeTruthy()
  })
})
