/**
 * Multi-chat-panel (Option E) — store-level tests for the new
 * groups / groupTree / focusedGroupId state and the actions that
 * mutate them. Pure store tests; no component mounting required.
 *
 * The chat store keeps ``tabs`` / ``activeTab`` as real state for
 * back-compat (so ``SessionHistoryViewer`` and the ~20 existing
 * ``chat.tabs = [...]`` test setups don't need to be rewritten),
 * but every group-mutating action calls ``_syncLegacyFromGroups``
 * before returning so the legacy fields stay in lock-step. These
 * tests pin BOTH semantics.
 */
import { createPinia, setActivePinia } from "pinia"
import { beforeEach, describe, expect, it, vi } from "vitest"

import { useChatStore } from "./chat.js"

// Project convention (see ``stores/hosts.test.js``): jsdom in this
// repo doesn't expose a real localStorage; stub with an in-memory
// Map so persistence assertions can read writes back deterministically.
let storage
beforeEach(() => {
  storage = new Map()
  vi.stubGlobal("localStorage", {
    getItem: (key) => (storage.has(key) ? storage.get(key) : null),
    setItem: (key, value) => storage.set(key, String(value)),
    removeItem: (key) => storage.delete(key),
    clear: () => storage.clear(),
  })
  setActivePinia(createPinia())
})

describe("multi-chat-panel — initial state", () => {
  it("store's groupTree is null pre-mount (ChatPanelContainer seeds it on setup)", () => {
    // At the bare-store level the multi-group state is empty. The
    // container component (``ChatPanelContainer``) is what calls
    // ``enableGroups`` / ``_loadGroupState`` synchronously at setup
    // so that the first render of the chat slot ALWAYS has a
    // populated group tree — drag-to-split is the primary chat
    // interaction (Option E §3) and must be available from the
    // very first frame, not behind any opt-in step.
    const chat = useChatStore()
    expect(chat.groupTree).toBeNull()
    expect(chat.groupsActive).toBe(false)
    expect(chat.groups).toEqual({})
    expect(chat.focusedGroupId).toBeNull()
  })

  it("groupsActive flips true after enableGroups", () => {
    const chat = useChatStore()
    chat.tabs = ["alice", "bob"]
    chat.activeTab = "alice"
    const gid = chat.enableGroups()
    expect(chat.groupsActive).toBe(true)
    expect(chat.groupTree).toEqual({ type: "leaf", groupId: gid })
    expect(chat.groups[gid].tabs).toEqual(["alice", "bob"])
    expect(chat.groups[gid].activeTab).toBe("alice")
    expect(chat.focusedGroupId).toBe(gid)
  })

  it("enableGroups is idempotent", () => {
    const chat = useChatStore()
    chat.tabs = ["alice"]
    const a = chat.enableGroups()
    const b = chat.enableGroups()
    expect(a).toBe(b)
    expect(Object.keys(chat.groups)).toEqual([a])
  })
})

describe("multi-chat-panel — splitGroup", () => {
  it("splitting horizontal/after creates a new group on the right", () => {
    const chat = useChatStore()
    chat.tabs = ["alice", "bob"]
    chat.activeTab = "alice"
    const g1 = chat.enableGroups()

    const g2 = chat.splitGroup(g1, "horizontal", "after", "bob")
    expect(g2).not.toBeNull()
    expect(chat.groupTree.type).toBe("split")
    expect(chat.groupTree.direction).toBe("horizontal")
    expect(chat.groupTree.children[0]).toEqual({ type: "leaf", groupId: g1 })
    expect(chat.groupTree.children[1]).toEqual({ type: "leaf", groupId: g2 })
    expect(chat.groups[g1].tabs).toEqual(["alice"])
    expect(chat.groups[g2].tabs).toEqual(["bob"])
    expect(chat.groups[g2].activeTab).toBe("bob")
    expect(chat.focusedGroupId).toBe(g2)
  })

  it("splitting vertical/before creates a new group on top", () => {
    const chat = useChatStore()
    chat.tabs = ["alice", "bob"]
    const g1 = chat.enableGroups()
    const g2 = chat.splitGroup(g1, "vertical", "before", "bob")
    expect(chat.groupTree.direction).toBe("vertical")
    expect(chat.groupTree.children[0]).toEqual({ type: "leaf", groupId: g2 })
    expect(chat.groupTree.children[1]).toEqual({ type: "leaf", groupId: g1 })
  })

  it("split without movedTab creates an empty new group", () => {
    const chat = useChatStore()
    chat.tabs = ["alice"]
    const g1 = chat.enableGroups()
    const g2 = chat.splitGroup(g1, "horizontal", "after", null)
    expect(chat.groups[g2].tabs).toEqual([])
    expect(chat.groups[g2].activeTab).toBeNull()
  })

  it("split is rejected when it would empty the source group", () => {
    const chat = useChatStore()
    chat.tabs = ["alice"]
    const g1 = chat.enableGroups()
    // alice is the ONLY tab in g1 — moving it out would empty the
    // source group and race the tree-collapse. Disallow.
    const g2 = chat.splitGroup(g1, "horizontal", "after", "alice")
    expect(g2).toBeNull()
    expect(chat.groupTree).toEqual({ type: "leaf", groupId: g1 })
    expect(chat.groups[g1].tabs).toEqual(["alice"])
  })

  it("rejects unknown direction / edge values", () => {
    const chat = useChatStore()
    chat.tabs = ["alice", "bob"]
    const g1 = chat.enableGroups()
    expect(chat.splitGroup(g1, "diagonal", "after")).toBeNull()
    expect(chat.splitGroup(g1, "horizontal", "middle")).toBeNull()
  })

  it("rejects splits on an unknown groupId", () => {
    const chat = useChatStore()
    chat.tabs = ["alice"]
    chat.enableGroups()
    expect(chat.splitGroup("g_nope", "horizontal", "after")).toBeNull()
  })

  it("cross-group split removes the moved tab from the source", () => {
    // Regression for the ``a|b|c drag c onto a's side → a|c|b|c``
    // duplication bug: when a drag-to-split crosses group boundaries
    // the source group must surrender its tab. Without ``srcGroupId``
    // (the 5th splitGroup arg) the moved tab gets added to the new
    // sibling but never removed from the origin.
    const chat = useChatStore()
    chat.tabs = ["a", "b", "c"]
    const gA = chat.enableGroups()
    // Create three side-by-side groups: a | b | c.
    const gB = chat.splitGroup(gA, "horizontal", "after", "b")
    const gC = chat.splitGroup(gB, "horizontal", "after", "c")
    // Drag "c" from gC and drop on the LEFT edge of gA — equivalent
    // to ``splitGroup(gA, "horizontal", "before", "c", gC)``.
    chat.splitGroup(gA, "horizontal", "before", "c", gC)
    // "c" must be in the new sibling group, NOT still in gC. gC
    // emptied → collapses.
    expect(chat.groups[gC]).toBeUndefined()
    // Union should still contain a/b/c exactly once each.
    expect(chat.tabs.sort()).toEqual(["a", "b", "c"])
    // No tab appears in more than one group.
    const seen = new Set()
    for (const g of Object.values(chat.groups)) {
      for (const t of g.tabs) {
        expect(seen.has(t)).toBe(false)
        seen.add(t)
      }
    }
  })
})

describe("multi-chat-panel — moveTab", () => {
  it("moves a tab between groups and focuses the destination", () => {
    const chat = useChatStore()
    chat.tabs = ["alice", "bob", "carol"]
    const g1 = chat.enableGroups()
    const g2 = chat.splitGroup(g1, "horizontal", "after", "bob")
    chat.moveTab(g1, "carol", g2, 0)
    expect(chat.groups[g1].tabs).toEqual(["alice"])
    expect(chat.groups[g2].tabs).toEqual(["carol", "bob"])
    expect(chat.focusedGroupId).toBe(g2)
    expect(chat.groups[g2].activeTab).toBe("carol")
  })

  it("collapses source group when last tab is moved out", () => {
    const chat = useChatStore()
    chat.tabs = ["alice", "bob"]
    const g1 = chat.enableGroups()
    const g2 = chat.splitGroup(g1, "horizontal", "after", "bob")
    chat.moveTab(g1, "alice", g2, 0)
    expect(chat.groups[g1]).toBeUndefined()
    // Tree should collapse to a single leaf (g2).
    expect(chat.groupTree).toEqual({ type: "leaf", groupId: g2 })
    expect(chat.groups[g2].tabs).toEqual(["alice", "bob"])
    expect(chat.focusedGroupId).toBe(g2)
  })

  it("same-group reorder", () => {
    const chat = useChatStore()
    chat.tabs = ["a", "b", "c"]
    const g1 = chat.enableGroups()
    chat.moveTab(g1, "c", g1, 0)
    expect(chat.groups[g1].tabs).toEqual(["c", "a", "b"])
    expect(chat.groups[g1].activeTab).toBe("c")
  })

  it("ignores no-op moves on unknown group / tab", () => {
    const chat = useChatStore()
    chat.tabs = ["a"]
    const g1 = chat.enableGroups()
    chat.moveTab("g_nope", "a", g1, 0)
    chat.moveTab(g1, "nonexistent", g1, 0)
    expect(chat.groups[g1].tabs).toEqual(["a"])
  })
})

describe("multi-chat-panel — removeGroup", () => {
  it("removes a leaf and collapses the split", () => {
    const chat = useChatStore()
    chat.tabs = ["a", "b"]
    const g1 = chat.enableGroups()
    const g2 = chat.splitGroup(g1, "horizontal", "after", "b")
    chat.removeGroup(g2)
    expect(chat.groups[g2]).toBeUndefined()
    expect(chat.groupTree).toEqual({ type: "leaf", groupId: g1 })
    expect(chat.focusedGroupId).toBe(g1)
  })

  it("removing the last group falls back to legacy mode", () => {
    const chat = useChatStore()
    chat.tabs = ["a"]
    const g1 = chat.enableGroups()
    chat.removeGroup(g1)
    expect(chat.groupTree).toBeNull()
    expect(chat.focusedGroupId).toBeNull()
    // tabs/activeTab kept as-is so the panel still shows something.
    expect(chat.tabs).toEqual(["a"])
  })
})

describe("multi-chat-panel — setFocusedGroup + setGroupActiveTab", () => {
  it("changing focused group syncs chat.activeTab to that group's tab", () => {
    const chat = useChatStore()
    chat.tabs = ["a", "b"]
    chat.activeTab = "a"
    const g1 = chat.enableGroups()
    const g2 = chat.splitGroup(g1, "horizontal", "after", "b")
    // After the split focused is g2 → chat.activeTab should be "b".
    expect(chat.activeTab).toBe("b")
    chat.setFocusedGroup(g1)
    expect(chat.activeTab).toBe("a")
  })

  it("setGroupActiveTab does not change focus or chat.activeTab when group isn't focused", () => {
    const chat = useChatStore()
    chat.tabs = ["a", "b", "c"]
    const g1 = chat.enableGroups()
    const g2 = chat.splitGroup(g1, "horizontal", "after", "c")
    // g1 has [a, b]; g2 has [c]. Focus is on g2.
    chat.setGroupActiveTab(g1, "b") // change g1 while g2 is focused
    expect(chat.groups[g1].activeTab).toBe("b")
    expect(chat.focusedGroupId).toBe(g2)
    expect(chat.activeTab).toBe("c") // unchanged
  })

  it("setGroupActiveTab is a no-op for unknown group or tab", () => {
    const chat = useChatStore()
    chat.tabs = ["a"]
    const g1 = chat.enableGroups()
    chat.setGroupActiveTab(g1, "nonexistent")
    expect(chat.groups[g1].activeTab).toBe("a")
    chat.setGroupActiveTab("g_nope", "a")
    // No throw, no mutation.
  })
})

describe("multi-chat-panel — setGroupSplitRatio", () => {
  it("updates the ratio at the given path", () => {
    const chat = useChatStore()
    chat.tabs = ["a", "b"]
    const g1 = chat.enableGroups()
    chat.splitGroup(g1, "horizontal", "after", "b")
    chat.setGroupSplitRatio([], 70)
    expect(chat.groupTree.ratio).toBe(70)
  })

  it("clamps ratio to [10, 90]", () => {
    const chat = useChatStore()
    chat.tabs = ["a", "b"]
    const g1 = chat.enableGroups()
    chat.splitGroup(g1, "horizontal", "after", "b")
    chat.setGroupSplitRatio([], 1)
    expect(chat.groupTree.ratio).toBe(10)
    chat.setGroupSplitRatio([], 99)
    expect(chat.groupTree.ratio).toBe(90)
  })

  it("is a no-op when groupTree is null", () => {
    const chat = useChatStore()
    chat.setGroupSplitRatio([], 60)
    expect(chat.groupTree).toBeNull()
  })
})

describe("multi-chat-panel — legacy sync invariant", () => {
  it("tabs derived from groups stays in tree-traversal order", () => {
    const chat = useChatStore()
    chat.tabs = ["a", "b", "c"]
    const g1 = chat.enableGroups()
    const g2 = chat.splitGroup(g1, "horizontal", "after", "c")
    // g1 has [a, b] left-side; g2 has [c] right-side. Union order
    // mirrors the tree.
    expect(chat.tabs).toEqual(["a", "b", "c"])
    chat.moveTab(g2, "c", g1, 0)
    // g1 now [c, a, b], g2 emptied → collapses → tree has only g1.
    expect(chat.tabs).toEqual(["c", "a", "b"])
  })

  it("ensureTab when groups active adds to focused group", () => {
    const chat = useChatStore()
    chat.tabs = ["a"]
    const g1 = chat.enableGroups()
    const g2 = chat.splitGroup(g1, "horizontal", "after", null)
    // Focus is g2 (empty).
    chat.ensureTab("z")
    expect(chat.groups[g2].tabs).toEqual(["z"])
    expect(chat.groups[g2].activeTab).toBe("z")
  })

  it("ensureTab is idempotent across groups (no double-add)", () => {
    const chat = useChatStore()
    chat.tabs = ["a", "b"]
    const g1 = chat.enableGroups()
    chat.splitGroup(g1, "horizontal", "after", "b")
    chat.ensureTab("b") // already in g2 → no-op
    expect(chat.tabs.filter((t) => t === "b").length).toBe(1)
  })

  it("pruneTab removes from every group", () => {
    const chat = useChatStore()
    chat.tabs = ["a", "b"]
    const g1 = chat.enableGroups()
    const g2 = chat.splitGroup(g1, "horizontal", "after", null)
    // Duplicate "a" into g2 manually for the test.
    chat.groups[g2].tabs.push("a")
    chat.groups[g2].activeTab = "a"
    chat.pruneTab("a")
    // ``a`` is gone from both groups. g1 still has "b" so it stays.
    // g2 emptied (had only "a") → collapses, tree promotes to leaf(g1).
    expect(chat.groups[g1].tabs).toEqual(["b"])
    expect(chat.groups[g2]).toBeUndefined()
    expect(chat.tabs).toEqual(["b"])
  })

  it("disableGroups carries focused group's tabs/activeTab into legacy", () => {
    const chat = useChatStore()
    chat.tabs = ["a", "b", "c"]
    const g1 = chat.enableGroups()
    const g2 = chat.splitGroup(g1, "horizontal", "after", "c")
    // Focus is g2 with [c].
    chat.disableGroups()
    expect(chat.groupTree).toBeNull()
    expect(chat.tabs).toEqual(["c"])
    expect(chat.activeTab).toBe("c")
  })
})

describe("multi-chat-panel — persistence", () => {
  it("_persistGroupState writes to localStorage, _loadGroupState restores", () => {
    const chat = useChatStore()
    chat._instanceId = "test-scope"
    chat.tabs = ["a", "b"]
    const g1 = chat.enableGroups()
    chat.splitGroup(g1, "horizontal", "after", "b")
    // Verify storage got written.
    const raw = localStorage.getItem("kt.chat.groupTree.test-scope")
    expect(raw).toBeTruthy()

    // Wipe in-memory state and reload from storage.
    const fresh = useChatStore("fresh-store-not-default")
    fresh._instanceId = "test-scope"
    fresh.tabs = ["a", "b"]
    fresh.activeTab = "a"
    const ok = fresh._loadGroupState()
    expect(ok).toBe(true)
    expect(fresh.groupTree?.type).toBe("split")
    expect(Object.keys(fresh.groups).length).toBe(2)
  })

  it("_loadGroupState returns false on missing or invalid payload", () => {
    const chat = useChatStore()
    chat._instanceId = "no-such-scope"
    expect(chat._loadGroupState()).toBe(false)
    // Write a bad version.
    localStorage.setItem(
      "kt.chat.groupTree.no-such-scope",
      JSON.stringify({ version: 999, groups: {}, groupTree: null }),
    )
    expect(chat._loadGroupState()).toBe(false)
  })

  it("_loadGroupState rejects payloads whose tree references missing groups", () => {
    const chat = useChatStore()
    chat._instanceId = "dangling-scope"
    localStorage.setItem(
      "kt.chat.groupTree.dangling-scope",
      JSON.stringify({
        version: 1,
        groups: { g_1: { tabs: ["a"], activeTab: "a", draftText: "" } },
        groupTree: { type: "leaf", groupId: "g_does_not_exist" },
        focusedGroupId: "g_does_not_exist",
        _groupCounter: 1,
      }),
    )
    expect(chat._loadGroupState()).toBe(false)
    expect(chat.groupTree).toBeNull()
  })

  it("disableGroups clears the storage key", () => {
    const chat = useChatStore()
    chat._instanceId = "clearable-scope"
    chat.tabs = ["a"]
    chat.enableGroups()
    expect(localStorage.getItem("kt.chat.groupTree.clearable-scope")).toBeTruthy()
    chat.disableGroups()
    expect(localStorage.getItem("kt.chat.groupTree.clearable-scope")).toBeNull()
  })
})

describe("multi-chat-panel — back-compat with _addTab / closeTab / setActiveTab", () => {
  it("_addTab in groups mode also appends to the focused group", () => {
    const chat = useChatStore()
    chat.tabs = ["a"]
    const g1 = chat.enableGroups()
    chat._addTab("new-creature")
    expect(chat.groups[g1].tabs).toContain("new-creature")
  })

  it("closeTab in groups mode removes from every group it appears in", () => {
    const chat = useChatStore()
    chat.tabs = ["a", "b"]
    const g1 = chat.enableGroups()
    chat.splitGroup(g1, "horizontal", "after", "b")
    chat.closeTab("a")
    // a was only in g1 → g1 emptied → collapses; tree has just g2.
    expect(chat.groups[g1]).toBeUndefined()
    expect(chat.tabs).toEqual(["b"])
  })

  it("setActiveTab in groups mode syncs the focused group's activeTab", () => {
    const chat = useChatStore()
    chat.tabs = ["a", "b"]
    const g1 = chat.enableGroups()
    // Single group has both tabs; activeTab is "a" via enableGroups.
    chat.setActiveTab("b")
    expect(chat.groups[g1].activeTab).toBe("b")
  })
})

describe("multi-chat-panel — initForInstance resets group state", () => {
  it("clears groups + groupTree + _groupCounter on instance switch", () => {
    const chat = useChatStore()
    chat.tabs = ["a"]
    const g1 = chat.enableGroups()
    expect(chat.groupTree).toEqual({ type: "leaf", groupId: g1 })
    chat.resetForRouteSwitch()
    expect(chat.groupTree).toBeNull()
    expect(chat.groups).toEqual({})
    expect(chat.focusedGroupId).toBeNull()
    expect(chat._groupCounter).toBe(0)
  })
})
