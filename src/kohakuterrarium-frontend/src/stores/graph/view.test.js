import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("@/utils/api", () => ({ runtimeGraphAPI: { snapshot: vi.fn() } }))
vi.mock("@/utils/uiPrefs", () => ({
  getHybridPrefSync: vi.fn(() => null),
  setHybridPref: vi.fn(),
  readLocalJsonPref: vi.fn(() => ({})),
  writeLocalJsonPref: vi.fn(),
}))

import { DEFAULT_LAYERS } from "@/utils/graph/data/projection"

import { useGraphLiveStore } from "./live"
import { quantizeAspect, useGraphViewStore } from "./view"

function creature(id, extra = {}) {
  return {
    creature_id: id,
    name: id,
    running: true,
    listen_channels: [],
    send_channels: [],
    ...extra,
  }
}

const snapshot = {
  graphs: [
    {
      graph_id: "g",
      creatures: [
        creature("a", { home_node: "_host" }),
        creature("b", { home_node: "w1" }),
        creature("c", { home_node: "w1" }),
      ],
      output_edges: [
        { edge_id: "e1", from: "a", to_creature_id: "b" },
        { edge_id: "e2", from: "c", to_creature_id: "a" },
      ],
    },
    { graph_id: "h", creatures: [creature("solo")], output_edges: [] },
  ],
}

let key = 0

beforeEach(() => {
  setActivePinia(createPinia())
  useGraphLiveStore().snapshot = snapshot
  key += 1
})

describe("graph view store", () => {
  it("resolves a collapsed edge with one member back to the real edge, so actions use creature ids", () => {
    const view = useGraphViewStore(`t${key}`)
    view.setSession("g")
    view.toggleCollapse("grp:host:w1")
    const bundled = view.projection.edges.find((e) => e.kind === "wire" && e.source === "a")
    expect(bundled.target).toBe("grp:host:w1")
    view.select("edge", bundled.id)
    expect(view.selected.kind).toBe("edge")
    expect(view.selected.item).toMatchObject({ source: "a", target: "b", edgeId: "e1" })
  })

  it("keeps a multi-member bundle as the bundle", () => {
    const live = useGraphLiveStore()
    live.snapshot = {
      graphs: [
        {
          graph_id: "g",
          creatures: [
            creature("a", { home_node: "_host", send_channels: ["x"] }),
            creature("b", { home_node: "w1", listen_channels: ["x"] }),
            creature("c", { home_node: "w1", listen_channels: ["x"] }),
          ],
        },
      ],
    }
    const view = useGraphViewStore(`t${key}`)
    view.setSession("g")
    view.toggleCollapse("grp:host:w1")
    const bundle = view.projection.edges.find((e) => e.source === "grp:host:w1")
    expect(bundle.count).toBe(2)
    view.select("edge", bundle.id)
    expect(view.selected.item).toBe(bundle)
  })

  it("drops a selection or scope that no longer exists", () => {
    const view = useGraphViewStore(`t${key}`)
    view.setSession("gone")
    expect(view.effectiveSessionId).toBeNull()
    view.select("creature", "ghost")
    expect(view.selected).toBeNull()
  })

  it("locks the scope for an embedded panel and keeps it while sample data is off", () => {
    const view = useGraphViewStore(`t${key}`)
    view.setSession("h", { lock: true })
    expect(view.lockedSession).toBe(true)
    expect(view.projection.nodes.map((n) => n.id)).toEqual(["solo"])
  })

  it("opens a large grouped scope with every group collapsed, and a small one expanded", async () => {
    const view = useGraphViewStore(`t${key}`)
    view.sample = "large"
    await Promise.resolve()
    expect(view.collapsed.size).toBeGreaterThan(1)
    expect(view.projection.nodes.every((n) => n.kind !== "creature")).toBe(true)
    view.sample = "small"
    await Promise.resolve()
    expect(view.collapsed.size).toBe(0)
  })

  it("switches to generated sample data without touching the live snapshot", () => {
    const view = useGraphViewStore(`t${key}`)
    view.sample = "small"
    expect(view.isSample).toBe(true)
    expect(view.model.creatures.length).toBeGreaterThan(3)
    expect(useGraphLiveStore().model.creatures.map((c) => c.id)).toEqual(["a", "b", "c", "solo"])
  })

  it("shows privileged listens, sends and direct links in Network, each toggled on its own", () => {
    useGraphLiveStore().snapshot = {
      graphs: [
        {
          graph_id: "g",
          creatures: [
            creature("boss", { is_privileged: true, listen_channels: ["x"], send_channels: ["y"] }),
            creature("w", { send_channels: ["x"], listen_channels: ["y"] }),
          ],
        },
      ],
    }
    const view = useGraphViewStore(`t${key}`)
    view.setSession("g")
    const ids = () => view.projection.edges.map((e) => `${e.id}/${e.mode || ""}`).sort()
    expect(ids()).toEqual([
      "chan:boss:ch:g:x/listen",
      "chan:boss:ch:g:y/send",
      "chan:w:ch:g:x/send",
      "chan:w:ch:g:y/listen",
      "direct:boss:w/",
    ])
    view.toggleLayer("privilegedListen")
    expect(ids()).not.toContain("chan:boss:ch:g:x/listen")
    expect(ids()).toContain("chan:boss:ch:g:y/send")
    view.toggleLayer("privilegedSend")
    view.toggleLayer("direct")
    expect(ids()).toEqual(["chan:w:ch:g:x/send", "chan:w:ch:g:y/listen"])
    view.view = "tiers"
    expect(ids()).toContain("chan:boss:ch:g:x/listen")
    expect(ids().some((id) => id.startsWith("direct:"))).toBe(false)
  })

  it("drops retired layer keys from saved prefs", async () => {
    const { getHybridPrefSync } = await import("@/utils/uiPrefs")
    getHybridPrefSync.mockReturnValueOnce({ layers: { control: true, wires: false, direct: "x" } })
    const layers = useGraphViewStore(`t${key}-layers`).layers
    expect(layers).toEqual({ ...DEFAULT_LAYERS, wires: false })
  })

  it("lets live drag positions win over stored ones and stores only on remember", () => {
    const view = useGraphViewStore(`t${key}`)
    view.setSession("g")
    view.rememberPosition("a", { x: 1, y: 2 })
    view.setDragPositions({ a: { x: 50, y: 60 }, b: { x: 7, y: 8 } })
    expect(view.positionOverrides.a).toEqual({ x: 50, y: 60 })
    expect(view.isMoved("b")).toBe(true)
    view.setDragPositions(null)
    expect(view.positionOverrides.a).toMatchObject({ x: 1, y: 2 })
    expect(view.isMoved("b")).toBe(false)
    view.rememberPositions({ a: { x: 9, y: 9 }, b: { x: 3, y: 4 } })
    expect(view.positionOverrides.b).toMatchObject({ x: 3, y: 4 })
    expect(view.positionOverrides.a).toMatchObject({ x: 9, y: 9 })
  })

  it("starts with the minimap hidden, and shows it only when a saved pref turned it on", async () => {
    expect(useGraphViewStore(`t${key}`).minimap).toBe(false)
    const { getHybridPrefSync } = await import("@/utils/uiPrefs")
    getHybridPrefSync.mockReturnValueOnce({ minimap: true })
    const on = useGraphViewStore(`t${key}-on`)
    expect(on.minimap).toBe(true)
    on.setMinimap(false)
    expect(on.minimap).toBe(false)
  })

  it("draws Flow's privileged links as chips unless a saved pref says otherwise", async () => {
    expect(useGraphViewStore(`t${key}`).privilegedLinks).toBe("chips")
    const { getHybridPrefSync } = await import("@/utils/uiPrefs")
    getHybridPrefSync.mockReturnValueOnce({ privilegedLinks: "pair" })
    expect(useGraphViewStore(`t${key}-saved`).privilegedLinks).toBe("pair")
    getHybridPrefSync.mockReturnValueOnce({ privilegedLinks: "bogus" })
    expect(useGraphViewStore(`t${key}-bad`).privilegedLinks).toBe("chips")
  })

  it("records the canvas aspect in steps, so a small resize changes nothing", () => {
    const view = useGraphViewStore(`t${key}`)
    view.setViewportSize(1600, 1000)
    const first = view.viewportAspect
    view.setViewportSize(1620, 1000)
    expect(view.viewportAspect).toBe(first)
    view.setViewportSize(800, 1000)
    expect(view.viewportAspect).toBeLessThan(first)
    view.setViewportSize(0, 0)
    expect(view.viewportAspect).toBeLessThan(first)
  })
})

describe("aspect quantization", () => {
  it("snaps to quarter-octave steps and clamps to [1/4, 4]", () => {
    expect(quantizeAspect(1)).toBe(1)
    expect(quantizeAspect(2)).toBe(2)
    expect(quantizeAspect(1.05)).toBe(1)
    expect(quantizeAspect(100)).toBe(4)
    expect(quantizeAspect(0.001)).toBe(0.25)
  })
})
