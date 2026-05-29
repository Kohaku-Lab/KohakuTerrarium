import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"

// Spy on scope ref-counting so we can assert open/close touch scope
// while moves between groups do NOT.
const acquireScope = vi.fn()
const releaseScope = vi.fn()
vi.mock("@/composables/useScope", () => ({
  acquireScope: (...a) => acquireScope(...a),
  releaseScope: (...a) => releaseScope(...a),
}))

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
  tabKinds.clear()
  inspectorInnerTabs.clear()
  railGroups.clear()
  for (const kind of ["dashboard", "attach", "inspector", "catalog", "settings", "code-editor"]) {
    registerTabKind({ kind, component: { template: "<div />" } })
  }
  acquireScope.mockClear()
  releaseScope.mockClear()
})

afterEach(() => {
  vi.unstubAllGlobals()
})

/** Convenience: open dashboard + N code-editor tabs in one group. */
function seed(tabs) {
  tabs.openTab({ kind: "dashboard", id: "dashboard" })
  tabs.openTab({ kind: "code-editor", id: "code-editor:a", slug: "a" })
  tabs.openTab({ kind: "code-editor", id: "code-editor:b", slug: "b" })
}

describe("tab groups — single group default", () => {
  it("starts as one group holding every tab; activeId = focused group active", () => {
    const tabs = useTabsStore()
    seed(tabs)
    expect(tabs.groupCount).toBe(1)
    const gid = tabs.focusedGroupId
    expect(tabs.tabGroups[gid].tabIds).toEqual(["dashboard", "code-editor:a", "code-editor:b"])
    expect(tabs.activeId).toBe("code-editor:b")
  })

  it("new tabs open into the focused group", () => {
    const tabs = useTabsStore()
    seed(tabs)
    const first = tabs.focusedGroupId
    tabs.splitTabGroup(first, "horizontal", "after", "code-editor:b", first)
    const second = tabs.focusedGroupId
    expect(second).not.toBe(first)
    tabs.openTab({ kind: "settings", id: "settings" })
    expect(tabs.tabGroups[second].tabIds).toContain("settings")
    expect(tabs.tabGroups[first].tabIds).not.toContain("settings")
  })
})

describe("tab groups — split", () => {
  it("splits, moving a tab to the new sibling group", () => {
    const tabs = useTabsStore()
    seed(tabs)
    const src = tabs.focusedGroupId
    const newId = tabs.splitTabGroup(src, "horizontal", "after", "code-editor:b", src)
    expect(newId).toBeTruthy()
    expect(tabs.groupCount).toBe(2)
    expect(tabs.tabGroups[src].tabIds).toEqual(["dashboard", "code-editor:a"])
    expect(tabs.tabGroups[newId].tabIds).toEqual(["code-editor:b"])
    expect(tabs.focusedGroupId).toBe(newId)
    // Flat view still contains every tab.
    expect(tabs.tabs.map((t) => t.id).sort()).toEqual(
      ["code-editor:a", "code-editor:b", "dashboard"].sort(),
    )
  })

  it("keyboard split (no moved tab) makes an empty focused group", () => {
    const tabs = useTabsStore()
    seed(tabs)
    const src = tabs.focusedGroupId
    const newId = tabs.splitTabGroup(src, "vertical", "after", null)
    expect(tabs.tabGroups[newId].tabIds).toEqual([])
    expect(tabs.focusedGroupId).toBe(newId)
    expect(tabs.groupCount).toBe(2)
  })

  it("refuses a self-split that would empty the only group", () => {
    const tabs = useTabsStore()
    tabs.openTab({ kind: "dashboard", id: "dashboard" })
    const gid = tabs.focusedGroupId
    // group has only dashboard; moving it out via self-split is refused
    expect(tabs.splitTabGroup(gid, "horizontal", "after", "dashboard", gid)).toBeNull()
    expect(tabs.groupCount).toBe(1)
  })
})

describe("tab groups — move + prune", () => {
  it("moves a tab across groups and prunes the emptied source", () => {
    const tabs = useTabsStore()
    seed(tabs)
    const src = tabs.focusedGroupId
    const dst = tabs.splitTabGroup(src, "horizontal", "after", "code-editor:b", src)
    // Move the last tab out of dst → dst empties → pruned, back to 1 group.
    tabs.moveTabToGroup(dst, "code-editor:b", src, -1)
    expect(tabs.groupCount).toBe(1)
    expect(tabs.tabGroups[src].tabIds).toEqual(["dashboard", "code-editor:a", "code-editor:b"])
  })

  it("move between groups does NOT acquire or release scope", () => {
    const tabs = useTabsStore()
    tabs.openTab({ kind: "dashboard", id: "dashboard" })
    tabs.openTab({ kind: "attach", id: "attach:x", target: "x" })
    const src = tabs.focusedGroupId
    const dst = tabs.splitTabGroup(src, "horizontal", "after", "attach:x", src)
    acquireScope.mockClear()
    releaseScope.mockClear()
    tabs.moveTabToGroup(dst, "attach:x", src, -1)
    expect(acquireScope).not.toHaveBeenCalled()
    expect(releaseScope).not.toHaveBeenCalled()
  })

  it("open acquires and close releases scope for a target", () => {
    const tabs = useTabsStore()
    tabs.openTab({ kind: "attach", id: "attach:y", target: "y" })
    expect(acquireScope).toHaveBeenCalledWith("y")
    tabs.closeTab("attach:y")
    expect(releaseScope).toHaveBeenCalledWith("y")
  })
})

describe("tab groups — remove group", () => {
  it("relocates protected survivors (dashboard) to a sibling, never strands", () => {
    const tabs = useTabsStore()
    seed(tabs)
    const src = tabs.focusedGroupId
    // Split dashboard into its own group, then remove that group.
    const dashGroup = tabs.splitTabGroup(src, "horizontal", "before", "dashboard", src)
    expect(tabs.tabGroups[dashGroup].tabIds).toEqual(["dashboard"])
    tabs.removeTabGroup(dashGroup)
    expect(tabs.groupCount).toBe(1)
    // dashboard relocated into the surviving group, still open.
    expect(tabs.isOpen("dashboard")).toBe(true)
    expect(tabs.tabsInGroup(tabs.focusedGroupId).map((t) => t.id)).toContain("dashboard")
  })

  it("refuses to remove the only group", () => {
    const tabs = useTabsStore()
    seed(tabs)
    const gid = tabs.focusedGroupId
    tabs.removeTabGroup(gid)
    expect(tabs.groupCount).toBe(1)
    expect(tabs.tabs.length).toBe(3)
  })
})

describe("tab groups — closeTab prunes empty group", () => {
  it("closing the last tab in a group prunes it and focuses the sibling", () => {
    const tabs = useTabsStore()
    seed(tabs)
    const src = tabs.focusedGroupId
    const dst = tabs.splitTabGroup(src, "horizontal", "after", "code-editor:b", src)
    tabs.closeTab("code-editor:b") // empties dst
    expect(tabs.groupCount).toBe(1)
    expect(tabs.focusedGroupId).toBe(src)
    expect(tabs.isOpen("code-editor:b")).toBe(false)
  })
})

describe("tab groups — v2 persistence", () => {
  it("round-trips the split layout through serialize/load", () => {
    const tabs = useTabsStore()
    seed(tabs)
    const src = tabs.focusedGroupId
    tabs.splitTabGroup(src, "vertical", "after", "code-editor:b", src)
    const snap = tabs.serializeToStorage()
    expect(snap.version).toBe(2)

    setActivePinia(createPinia())
    const restored = useTabsStore()
    restored.loadFromStorage(snap)
    expect(restored.groupCount).toBe(2)
    expect(restored.tabs.map((t) => t.id).sort()).toEqual(
      ["code-editor:a", "code-editor:b", "dashboard"].sort(),
    )
    // Tree preserved (two leaves).
    expect(restored.groupOrder().length).toBe(2)
  })

  it("self-heals a snapshot whose tree references a missing group", () => {
    const tabs = useTabsStore()
    const snap = {
      version: 2,
      tabs: [{ kind: "dashboard", id: "dashboard" }],
      tabGroups: { tg_1: { tabIds: ["dashboard"], activeId: "dashboard" } },
      tabTree: {
        type: "split",
        direction: "horizontal",
        ratio: 50,
        children: [
          { type: "leaf", id: "tg_1" },
          { type: "leaf", id: "tg_ghost" }, // group does not exist
        ],
      },
      focusedGroupId: "tg_ghost",
    }
    tabs.loadFromStorage(snap)
    expect(tabs.groupCount).toBe(1)
    expect(tabs.isOpen("dashboard")).toBe(true)
    expect(tabs.focusedGroupId).toBeTruthy()
    expect(tabs.tabGroups[tabs.focusedGroupId]).toBeDefined()
  })
})
