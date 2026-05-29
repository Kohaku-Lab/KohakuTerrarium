/**
 * ``useSplitDrag`` — macro tab-group drag state machine. Drives the
 * real ``tabs`` store so the asserts observe actual move/split effects.
 * Module-scoped drag state is reset via ``_resetForTests`` per case.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"

import { useSplitDrag, _resetForTests } from "./useSplitDrag.js"
import { useTabsStore } from "@/stores/tabs"

const MACRO_MIME = "application/x-kt-macrotab"
const CHAT_MIME = "application/x-kt-tab"

function makeEvent({
  types = [],
  data = {},
  rect = { left: 0, top: 0, width: 100, height: 100 },
  x = 50,
  y = 50,
} = {}) {
  const _data = { ...data }
  const _types = [...types]
  const dataTransfer = {
    effectAllowed: "",
    dropEffect: "",
    get types() {
      return _types
    },
    setData(k, v) {
      _data[k] = v
      if (!_types.includes(k)) _types.push(k)
    },
    getData(k) {
      return _data[k] ?? ""
    },
  }
  return {
    preventDefault() {},
    clientX: x,
    clientY: y,
    dataTransfer,
    currentTarget: { getBoundingClientRect: () => rect },
  }
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
  _resetForTests()
})
afterEach(() => vi.unstubAllGlobals())

/** Seed two groups: g1=[dashboard, a], g2=[b]. Returns ids. */
function seedTwoGroups() {
  const tabs = useTabsStore()
  tabs.openTab({ kind: "dashboard", id: "dashboard" })
  tabs.openTab({ kind: "code-editor", id: "code-editor:a", slug: "a" })
  tabs.openTab({ kind: "code-editor", id: "code-editor:b", slug: "b" })
  const g1 = tabs.focusedGroupId
  const g2 = tabs.splitTabGroup(g1, "horizontal", "after", "code-editor:b", g1)
  return { tabs, g1, g2 }
}

describe("useSplitDrag — drag start", () => {
  it("records the dragged tab + writes the typed payload", () => {
    seedTwoGroups()
    const drag = useSplitDrag()
    const ev = makeEvent()
    drag.onTabDragStart(ev, "tg_1", "code-editor:a")
    expect(drag.dragging.value).toEqual({ srcGroupId: "tg_1", tab: "code-editor:a" })
    expect(ev.dataTransfer.getData(MACRO_MIME)).toContain("code-editor:a")
    expect(ev.dataTransfer.getData("text/plain")).toBe("code-editor:a")
  })
})

describe("useSplitDrag — center drop = move", () => {
  it("moves a tab into the destination group", () => {
    const { tabs, g1, g2 } = seedTwoGroups()
    const drag = useSplitDrag()
    drag.onTabDragStart(makeEvent(), g2, "code-editor:b")
    // Drop in the center of g1's body.
    drag.onBubbleDrop(
      makeEvent({
        types: [MACRO_MIME],
        data: { [MACRO_MIME]: JSON.stringify({ srcGroupId: g2, tab: "code-editor:b" }) },
        x: 50,
        y: 50,
      }),
      g1,
    )
    // g2 emptied → pruned; b now in g1.
    expect(tabs.groupCount).toBe(1)
    expect(tabs.tabGroups[g1].tabIds).toContain("code-editor:b")
  })

  it("same-group center drop is a no-op", () => {
    const { tabs, g1 } = seedTwoGroups()
    const drag = useSplitDrag()
    const before = [...tabs.tabGroups[g1].tabIds]
    drag.onTabDragStart(makeEvent(), g1, "code-editor:a")
    drag.onBubbleDrop(
      makeEvent({
        types: [MACRO_MIME],
        data: { [MACRO_MIME]: JSON.stringify({ srcGroupId: g1, tab: "code-editor:a" }) },
        x: 50,
        y: 50,
      }),
      g1,
    )
    expect(tabs.tabGroups[g1].tabIds).toEqual(before)
  })
})

describe("useSplitDrag — edge drop = split", () => {
  it("splits the group, new sibling carries the moved tab", () => {
    const { tabs, g1, g2 } = seedTwoGroups()
    const drag = useSplitDrag()
    drag.onTabDragStart(makeEvent(), g1, "code-editor:a")
    // Drop on the RIGHT edge of g2 → horizontal split, new group after.
    drag.onBubbleDrop(
      makeEvent({
        types: [MACRO_MIME],
        data: { [MACRO_MIME]: JSON.stringify({ srcGroupId: g1, tab: "code-editor:a" }) },
        x: 95,
        y: 50,
      }),
      g2,
    )
    expect(tabs.groupCount).toBe(3)
    // a left g1, lives alone in a new group.
    expect(tabs.tabGroups[g1].tabIds).not.toContain("code-editor:a")
    const owner = Object.values(tabs.tabGroups).find((g) => g.tabIds.includes("code-editor:a"))
    expect(owner.tabIds).toEqual(["code-editor:a"])
  })
})

describe("useSplitDrag — scope isolation", () => {
  it("ignores a chat-tab drag (different MIME, no module state)", () => {
    const { tabs, g1 } = seedTwoGroups()
    const drag = useSplitDrag()
    const before = tabs.groupCount
    // A chat drag: only the chat MIME present, no macro module state.
    const ev = makeEvent({
      types: [CHAT_MIME],
      data: { [CHAT_MIME]: JSON.stringify({ srcGroupId: "g_1", tab: "root" }) },
      x: 95,
      y: 50,
    })
    expect(drag.isOurDrag(ev)).toBe(false)
    drag.onBubbleDrop(ev, g1)
    expect(tabs.groupCount).toBe(before)
  })
})
