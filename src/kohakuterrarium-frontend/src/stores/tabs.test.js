import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"

import { useTabsStore } from "./tabs.js"
import { registerTabKind, tabKinds, inspectorInnerTabs, railGroups } from "./tabKindRegistry.js"

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
  // Clear registries between tests
  tabKinds.clear()
  inspectorInnerTabs.clear()
  railGroups.clear()
  // Register the 8 built-in kinds with placeholder components so
  // ``loadFromStorage`` doesn't drop them when the snapshot is
  // round-tripped through the registry filter.
  for (const kind of [
    "dashboard",
    "attach",
    "inspector",
    "session-viewer",
    "studio-editor",
    "catalog",
    "settings",
    "code-editor",
  ]) {
    registerTabKind({ kind, component: { template: "<div />" } })
  }
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe("tabs store — open/activate/close", () => {
  it("opens a tab and activates it", () => {
    const tabs = useTabsStore()
    tabs.openTab({ kind: "dashboard", id: "dashboard" })
    expect(tabs.tabs).toHaveLength(1)
    expect(tabs.activeId).toBe("dashboard")
  })

  it("openTab is idempotent on id (just activates)", () => {
    const tabs = useTabsStore()
    tabs.openTab({ kind: "dashboard", id: "dashboard" })
    tabs.openTab({ kind: "attach", id: "attach:alice", target: "alice" })
    tabs.openTab({ kind: "dashboard", id: "dashboard" })
    expect(tabs.tabs).toHaveLength(2)
    expect(tabs.activeId).toBe("dashboard")
  })

  it("closeTab removes and activates neighbour", () => {
    const tabs = useTabsStore()
    tabs.openTab({ kind: "dashboard", id: "dashboard" })
    tabs.openTab({ kind: "attach", id: "attach:a", target: "a" })
    tabs.openTab({ kind: "inspector", id: "inspect:a", target: "a" })
    expect(tabs.activeId).toBe("inspect:a")
    tabs.closeTab("inspect:a")
    expect(tabs.tabs).toHaveLength(2)
    // Last neighbour is attach:a (idx-1)
    expect(tabs.activeId).toBe("attach:a")
  })

  it("closeTab on non-active does not change activeId", () => {
    const tabs = useTabsStore()
    tabs.openTab({ kind: "dashboard", id: "dashboard" })
    tabs.openTab({ kind: "attach", id: "attach:a", target: "a" })
    tabs.activateTab("dashboard")
    tabs.closeTab("attach:a")
    expect(tabs.activeId).toBe("dashboard")
  })

  it("closeTab on the dashboard is a no-op (protected baseline)", () => {
    const tabs = useTabsStore()
    tabs.openTab({ kind: "dashboard", id: "dashboard" })
    tabs.closeTab("dashboard")
    expect(tabs.tabs).toHaveLength(1)
    expect(tabs.activeId).toBe("dashboard")
  })

  it("closeTab on the only non-dashboard tab leaves dashboard active", () => {
    const tabs = useTabsStore()
    tabs.openTab({ kind: "dashboard", id: "dashboard" })
    tabs.openTab({ kind: "attach", id: "attach:a", target: "a" })
    tabs.closeTab("attach:a")
    expect(tabs.tabs.map((t) => t.id)).toEqual(["dashboard"])
    expect(tabs.activeId).toBe("dashboard")
  })

  it("recently-closed buffer caps at 10", () => {
    const tabs = useTabsStore()
    for (let i = 0; i < 15; i++) {
      tabs.openTab({ kind: "code-editor", id: `code-editor:f${i}`, slug: `f${i}` })
      tabs.closeTab(`code-editor:f${i}`)
    }
    expect(tabs.recentlyClosed).toHaveLength(10)
  })

  it("reopenLastClosed restores last closed", () => {
    const tabs = useTabsStore()
    tabs.openTab({ kind: "dashboard", id: "dashboard" })
    tabs.openTab({ kind: "attach", id: "attach:a", target: "a" })
    tabs.closeTab("attach:a")
    tabs.reopenLastClosed()
    expect(tabs.tabs.map((t) => t.id)).toEqual(["dashboard", "attach:a"])
    expect(tabs.activeId).toBe("attach:a")
  })

  it("closeOthers preserves active + pinned + dashboard", () => {
    const tabs = useTabsStore()
    tabs.openTab({ kind: "dashboard", id: "dashboard" })
    tabs.openTab({ kind: "attach", id: "attach:a", target: "a" })
    tabs.openTab({ kind: "settings", id: "settings" })
    tabs.pinTab("settings")
    tabs.closeOthers("attach:a")
    expect(tabs.tabs.map((t) => t.id).sort()).toEqual(["attach:a", "dashboard", "settings"])
    expect(tabs.activeId).toBe("attach:a")
  })

  it("closeAll preserves pinned + dashboard", () => {
    const tabs = useTabsStore()
    tabs.openTab({ kind: "dashboard", id: "dashboard" })
    tabs.openTab({ kind: "attach", id: "attach:a", target: "a" })
    tabs.openTab({ kind: "settings", id: "settings" })
    tabs.pinTab("settings")
    tabs.closeAll()
    expect(tabs.tabs.map((t) => t.id).sort()).toEqual(["dashboard", "settings"])
  })
})

describe("tabs store — surface helpers", () => {
  it("openSurface creates attach tab", async () => {
    const tabs = useTabsStore()
    await tabs.openSurface("alice", "chat", { config_name: "alice", type: "creature" })
    expect(tabs.tabs[0].id).toBe("attach:alice")
    expect(tabs.tabs[0].config_name).toBe("alice")
  })

  it("openSurface creates inspector tab", async () => {
    const tabs = useTabsStore()
    await tabs.openSurface("alice", "inspector")
    expect(tabs.tabs[0].id).toBe("inspect:alice")
  })

  it("surfaceTabsForTarget returns both surfaces", async () => {
    const tabs = useTabsStore()
    await tabs.openSurface("alice", "chat")
    await tabs.openSurface("alice", "inspector")
    const surfaces = tabs.surfaceTabsForTarget("alice")
    expect(surfaces.chat).toBeDefined()
    expect(surfaces.inspector).toBeDefined()
  })

  it("detach closes both surfaces", async () => {
    const tabs = useTabsStore()
    await tabs.openSurface("alice", "chat")
    await tabs.openSurface("alice", "inspector")
    await tabs.detach("alice")
    expect(tabs.tabs).toHaveLength(0)
  })
})

describe("tabs store — pinning + reorder", () => {
  it("pin/unpin tracks state and persists", () => {
    const tabs = useTabsStore()
    tabs.pinTab("dashboard")
    expect(tabs.pinnedIds.has("dashboard")).toBe(true)
    expect(localStorage.getItem("kt.tabs.pinned")).toContain("dashboard")
    tabs.unpinTab("dashboard")
    expect(tabs.pinnedIds.has("dashboard")).toBe(false)
  })

  it("reorderTabs respects given order", () => {
    const tabs = useTabsStore()
    tabs.openTab({ kind: "dashboard", id: "dashboard" })
    tabs.openTab({ kind: "catalog", id: "catalog" })
    tabs.openTab({ kind: "settings", id: "settings" })
    tabs.reorderTabs(["settings", "dashboard", "catalog"])
    expect(tabs.tabs.map((t) => t.id)).toEqual(["settings", "dashboard", "catalog"])
  })
})

describe("tabs store — localStorage load/serialize", () => {
  it("loadFromStorage hydrates tabs and active from a snapshot", () => {
    const tabs = useTabsStore()
    tabs.loadFromStorage({
      tabs: [
        { kind: "dashboard", id: "dashboard" },
        { kind: "attach", id: "attach:alice", target: "alice" },
        { kind: "inspector", id: "inspect:alice", target: "alice" },
      ],
      activeId: "attach:alice",
    })
    expect(tabs.tabs.map((t) => t.id)).toEqual(["dashboard", "attach:alice", "inspect:alice"])
    expect(tabs.activeId).toBe("attach:alice")
  })

  it("loadFromStorage drops tab specs whose id fails parseTabId", () => {
    const tabs = useTabsStore()
    tabs.loadFromStorage({
      tabs: [
        { kind: "dashboard", id: "dashboard" },
        { kind: "garbage", id: "totally-bogus-id" },
        { kind: "attach", id: "attach:bob", target: "bob" },
      ],
      activeId: "attach:bob",
    })
    expect(tabs.tabs.map((t) => t.id)).toEqual(["dashboard", "attach:bob"])
    expect(tabs.activeId).toBe("attach:bob")
  })

  it("loadFromStorage falls back to first tab when activeId is gone", () => {
    const tabs = useTabsStore()
    tabs.loadFromStorage({
      tabs: [{ kind: "dashboard", id: "dashboard" }],
      activeId: "attach:dropped",
    })
    expect(tabs.activeId).toBe("dashboard")
  })

  it("loadFromStorage accepts the legacy URL-query shape", () => {
    // Pre-migration users had ``{tabs: "dashboard,attach:alice", active: "1"}``
    // stored in the URL; flipping to localStorage shouldn't lose them.
    const tabs = useTabsStore()
    tabs.loadFromStorage({ tabs: "dashboard,attach:alice", active: "1" })
    expect(tabs.tabs.map((t) => t.id)).toEqual(["dashboard", "attach:alice"])
    expect(tabs.activeId).toBe("attach:alice")
  })

  it("serializeToStorage round-trips through loadFromStorage", () => {
    const tabs = useTabsStore()
    tabs.openTab({ kind: "dashboard", id: "dashboard" })
    tabs.openTab({ kind: "attach", id: "attach:a", target: "a", config_name: "alpha" })
    tabs.activateTab("attach:a")
    const snapshot = tabs.serializeToStorage()
    // Fresh store, replay
    setActivePinia(createPinia())
    const restored = useTabsStore()
    restored.loadFromStorage(snapshot)
    expect(restored.tabs.map((t) => t.id)).toEqual(["dashboard", "attach:a"])
    expect(restored.tabs[1].config_name).toBe("alpha")
    expect(restored.activeId).toBe("attach:a")
  })

  it("syncing fires kt:tabs:dirty event", () => {
    const tabs = useTabsStore()
    const handler = vi.fn()
    window.addEventListener("kt:tabs:dirty", handler)
    tabs.openTab({ kind: "dashboard", id: "dashboard" })
    expect(handler).toHaveBeenCalled()
    window.removeEventListener("kt:tabs:dirty", handler)
  })
})

describe("tabs store — close protections", () => {
  it("closeTab refuses to close the dashboard", () => {
    const tabs = useTabsStore()
    tabs.openTab({ kind: "dashboard", id: "dashboard" })
    tabs.openTab({ kind: "attach", id: "attach:a", target: "a" })
    tabs.closeTab("dashboard")
    expect(tabs.tabs.map((t) => t.id)).toEqual(["dashboard", "attach:a"])
  })

  it("closeAll preserves dashboard + pinned + restores dashboard if missing", () => {
    const tabs = useTabsStore()
    tabs.openTab({ kind: "dashboard", id: "dashboard" })
    tabs.openTab({ kind: "attach", id: "attach:a", target: "a" })
    tabs.openTab({ kind: "attach", id: "attach:b", target: "b" })
    tabs.pinTab("attach:b")
    tabs.closeAll()
    expect(tabs.tabs.map((t) => t.id)).toEqual(["dashboard", "attach:b"])
    expect(tabs.activeId).toBe("dashboard")
  })

  it("closeOthers keeps target + dashboard + pinned", () => {
    const tabs = useTabsStore()
    tabs.openTab({ kind: "dashboard", id: "dashboard" })
    tabs.openTab({ kind: "attach", id: "attach:a", target: "a" })
    tabs.openTab({ kind: "attach", id: "attach:b", target: "b" })
    tabs.openTab({ kind: "attach", id: "attach:c", target: "c" })
    tabs.pinTab("attach:c")
    tabs.closeOthers("attach:a")
    expect(tabs.tabs.map((t) => t.id).sort()).toEqual(["attach:a", "attach:c", "dashboard"].sort())
    expect(tabs.activeId).toBe("attach:a")
  })

  it("closeLeft drops tabs to the left, keeping dashboard + pinned", () => {
    const tabs = useTabsStore()
    tabs.openTab({ kind: "dashboard", id: "dashboard" })
    tabs.openTab({ kind: "attach", id: "attach:a", target: "a" })
    tabs.openTab({ kind: "attach", id: "attach:b", target: "b" })
    tabs.openTab({ kind: "attach", id: "attach:c", target: "c" })
    tabs.closeLeft("attach:c")
    expect(tabs.tabs.map((t) => t.id)).toEqual(["dashboard", "attach:c"])
    expect(tabs.activeId).toBe("attach:c")
  })

  it("closeRight drops tabs to the right, keeping dashboard + pinned", () => {
    const tabs = useTabsStore()
    tabs.openTab({ kind: "dashboard", id: "dashboard" })
    tabs.openTab({ kind: "attach", id: "attach:a", target: "a" })
    tabs.openTab({ kind: "attach", id: "attach:b", target: "b" })
    tabs.openTab({ kind: "attach", id: "attach:c", target: "c" })
    tabs.closeRight("attach:a")
    expect(tabs.tabs.map((t) => t.id)).toEqual(["dashboard", "attach:a"])
    expect(tabs.activeId).toBe("attach:a")
  })
})

describe("tabs store — layout migration", () => {
  it("migrates legacy preset keys idempotently", () => {
    localStorage.setItem("kt.layout.preset.agent_1", "chat-focus")
    localStorage.setItem("kt.layout.preset.agent_2", "workspace")
    const tabs = useTabsStore()
    tabs.migrateLayoutPresetKeys()
    expect(localStorage.getItem("kt.attach.agent_1.preset")).toBe("chat-focus")
    expect(localStorage.getItem("kt.attach.agent_2.preset")).toBe("workspace")
    expect(localStorage.getItem("kt.tabs.migrationV1")).toBe("true")
    // Second run is no-op
    localStorage.removeItem("kt.attach.agent_1.preset")
    tabs.migrateLayoutPresetKeys()
    expect(localStorage.getItem("kt.attach.agent_1.preset")).toBeNull()
  })
})
