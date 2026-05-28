/**
 * ``useChatTabDrag`` — drag state machine + edge detection for the
 * VSCode-style tab drag-to-split (Option E). The composable is
 * module-scoped (shared drag state across panels) so tests reset
 * via the exported ``_resetForTests`` between cases.
 */
import { beforeEach, describe, expect, it, vi } from "vitest"

import { edgeOf, useChatTabDrag, _resetForTests } from "./useChatTabDrag.js"

beforeEach(() => {
  _resetForTests()
})

describe("edgeOf — 25% threshold detection", () => {
  const rect = { left: 0, top: 0, width: 100, height: 100 }

  it("returns center for the middle 50% region", () => {
    expect(edgeOf(rect, 50, 50)).toBe("center")
    expect(edgeOf(rect, 30, 30)).toBe("center")
    expect(edgeOf(rect, 70, 70)).toBe("center")
  })

  it("returns 'left' when x < 25%", () => {
    expect(edgeOf(rect, 20, 50)).toBe("left")
    expect(edgeOf(rect, 0, 50)).toBe("left")
  })

  it("returns 'right' when x > 75%", () => {
    expect(edgeOf(rect, 80, 50)).toBe("right")
  })

  it("returns 'top' when y < 25% and x in central column", () => {
    expect(edgeOf(rect, 50, 10)).toBe("top")
  })

  it("returns 'bottom' when y > 75% and x in central column", () => {
    expect(edgeOf(rect, 50, 90)).toBe("bottom")
  })

  it("horizontal edge takes priority over vertical when both apply", () => {
    // Cursor in top-left corner: left & top both trigger. The
    // implementation prioritizes horizontal (left/right) → users
    // dragging side-to-side get a horizontal split.
    expect(edgeOf(rect, 10, 10)).toBe("left")
    expect(edgeOf(rect, 90, 10)).toBe("right")
  })

  it("returns null for cursor outside the rect", () => {
    expect(edgeOf(rect, -5, 50)).toBeNull()
    expect(edgeOf(rect, 50, -5)).toBeNull()
    expect(edgeOf(rect, 200, 50)).toBeNull()
  })

  it("returns null for a zero-size rect", () => {
    expect(edgeOf({ left: 0, top: 0, width: 0, height: 100 }, 0, 50)).toBeNull()
  })
})

describe("useChatTabDrag — drag lifecycle", () => {
  function makeMockChat() {
    return {
      groups: {
        g1: { tabs: ["a", "b"], activeTab: "a", draftText: "" },
        g2: { tabs: ["c"], activeTab: "c", draftText: "" },
      },
      moveTab: vi.fn(),
      splitGroup: vi.fn(),
    }
  }

  function mockDragEvent({ kind, payload, clientX, clientY }) {
    const types = []
    if (kind === "tab") types.push("application/x-kt-tab")
    const _data = new Map()
    return {
      clientX,
      clientY,
      preventDefault: vi.fn(),
      currentTarget: {
        getBoundingClientRect: () => ({ left: 0, top: 0, width: 200, height: 200 }),
      },
      dataTransfer: {
        types,
        effectAllowed: null,
        dropEffect: null,
        setData(t, v) {
          _data.set(t, v)
        },
        getData(t) {
          return _data.get(t) ?? ""
        },
        _seed(t, v) {
          _data.set(t, v)
          if (!types.includes(t)) types.push(t)
        },
      },
    }
  }

  it("onTabDragStart sets dataTransfer with the tab payload", () => {
    const chat = makeMockChat()
    const drag = useChatTabDrag(chat)
    const ev = mockDragEvent({ kind: "none", clientX: 0, clientY: 0 })
    drag.onTabDragStart(ev, "g1", "b")
    expect(ev.dataTransfer.effectAllowed).toBe("move")
    expect(JSON.parse(ev.dataTransfer.getData("application/x-kt-tab"))).toEqual({
      srcGroupId: "g1",
      tab: "b",
    })
  })

  it("onBubbleDrop in center → moveTab, NOT splitGroup", () => {
    const chat = makeMockChat()
    const drag = useChatTabDrag(chat)
    const start = mockDragEvent({ kind: "none", clientX: 0, clientY: 0 })
    drag.onTabDragStart(start, "g1", "b")
    const drop = mockDragEvent({ kind: "tab", clientX: 100, clientY: 100 })
    drop.dataTransfer._seed("application/x-kt-tab", JSON.stringify({ srcGroupId: "g1", tab: "b" }))
    drag.onBubbleDrop(drop, "g2")
    expect(chat.moveTab).toHaveBeenCalledWith("g1", "b", "g2", 1)
    expect(chat.splitGroup).not.toHaveBeenCalled()
  })

  it("onBubbleDrop on right edge → splitGroup horizontal/after", () => {
    const chat = makeMockChat()
    const drag = useChatTabDrag(chat)
    const start = mockDragEvent({ kind: "none", clientX: 0, clientY: 0 })
    drag.onTabDragStart(start, "g1", "b")
    const drop = mockDragEvent({ kind: "tab", clientX: 180, clientY: 100 })
    drop.dataTransfer._seed("application/x-kt-tab", JSON.stringify({ srcGroupId: "g1", tab: "b" }))
    drag.onBubbleDrop(drop, "g2")
    // ``srcGroupId`` is now passed as the 5th argument so
    // ``splitGroup`` can remove the moved tab from the cross-group
    // source — without it, ``a|b|c drag c onto a's side`` produced
    // ``a|c|b|c`` (duplicate) instead of ``c|a|b``.
    expect(chat.splitGroup).toHaveBeenCalledWith("g2", "horizontal", "after", "b", "g1")
    expect(chat.moveTab).not.toHaveBeenCalled()
  })

  it("onBubbleDrop on top edge → splitGroup vertical/before", () => {
    const chat = makeMockChat()
    const drag = useChatTabDrag(chat)
    const start = mockDragEvent({ kind: "none", clientX: 0, clientY: 0 })
    drag.onTabDragStart(start, "g1", "b")
    const drop = mockDragEvent({ kind: "tab", clientX: 100, clientY: 10 })
    drop.dataTransfer._seed("application/x-kt-tab", JSON.stringify({ srcGroupId: "g1", tab: "b" }))
    drag.onBubbleDrop(drop, "g2")
    expect(chat.splitGroup).toHaveBeenCalledWith("g2", "vertical", "before", "b", "g1")
  })

  it("center drop on the SAME group is a no-op", () => {
    const chat = makeMockChat()
    const drag = useChatTabDrag(chat)
    const start = mockDragEvent({ kind: "none", clientX: 0, clientY: 0 })
    drag.onTabDragStart(start, "g1", "b")
    const drop = mockDragEvent({ kind: "tab", clientX: 100, clientY: 100 })
    drop.dataTransfer._seed("application/x-kt-tab", JSON.stringify({ srcGroupId: "g1", tab: "b" }))
    drag.onBubbleDrop(drop, "g1")
    expect(chat.moveTab).not.toHaveBeenCalled()
    expect(chat.splitGroup).not.toHaveBeenCalled()
  })

  it("non-tab drag (file drop) is ignored", () => {
    const chat = makeMockChat()
    const drag = useChatTabDrag(chat)
    const ev = mockDragEvent({ kind: "none", clientX: 100, clientY: 100 })
    drag.onBubbleDragOver(ev, "g2")
    expect(ev.preventDefault).not.toHaveBeenCalled()
  })

  it("onTabDragEnd clears module-scoped drag state", () => {
    const chat = makeMockChat()
    const drag = useChatTabDrag(chat)
    const start = mockDragEvent({ kind: "none", clientX: 0, clientY: 0 })
    drag.onTabDragStart(start, "g1", "b")
    expect(drag.dragging.value).toEqual({ srcGroupId: "g1", tab: "b" })
    drag.onTabDragEnd()
    expect(drag.dragging.value).toBeNull()
  })

  it("onTabStripDrop dispatches moveTab with the requested dstIndex", () => {
    const chat = makeMockChat()
    const drag = useChatTabDrag(chat)
    const start = mockDragEvent({ kind: "none", clientX: 0, clientY: 0 })
    drag.onTabDragStart(start, "g1", "b")
    const drop = mockDragEvent({ kind: "tab", clientX: 50, clientY: 5 })
    drop.dataTransfer._seed("application/x-kt-tab", JSON.stringify({ srcGroupId: "g1", tab: "b" }))
    drag.onTabStripDrop(drop, "g2", 0)
    expect(chat.moveTab).toHaveBeenCalledWith("g1", "b", "g2", 0)
  })

  it("drop on a non-existent destination group is a no-op", () => {
    const chat = makeMockChat()
    const drag = useChatTabDrag(chat)
    const start = mockDragEvent({ kind: "none", clientX: 0, clientY: 0 })
    drag.onTabDragStart(start, "g1", "b")
    const drop = mockDragEvent({ kind: "tab", clientX: 100, clientY: 100 })
    drop.dataTransfer._seed("application/x-kt-tab", JSON.stringify({ srcGroupId: "g1", tab: "b" }))
    drag.onBubbleDrop(drop, "g_does_not_exist")
    expect(chat.moveTab).not.toHaveBeenCalled()
    expect(chat.splitGroup).not.toHaveBeenCalled()
  })
})
