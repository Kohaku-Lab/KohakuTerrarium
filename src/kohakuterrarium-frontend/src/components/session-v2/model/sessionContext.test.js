import { describe, expect, it, vi } from "vitest"
import { computed, ref } from "vue"

vi.mock("@/utils/uiPrefs", () => {
  const store = new Map()
  return {
    readLocalPref: (k) => store.get(k) ?? null,
    writeLocalPref: (k, v) => store.set(k, v),
  }
})

import { createSessionV2 } from "./sessionContext"

const make = (id = "s1") =>
  createSessionV2({ instance: computed(() => ({ id })), instanceId: ref(id), chat: {} })

describe("session v2 context", () => {
  it("keeps one widget open at a time and toggles it off on a second open", () => {
    const ctx = make()
    ctx.openWidget("agents")
    ctx.openWidget("drives")
    expect(ctx.widget.value).toBe("drives")
    ctx.openWidget("drives")
    expect(ctx.widget.value).toBe(null)
  })

  it("pins the open widget into the side view and opening a side view returns to Chat", () => {
    const ctx = make()
    ctx.setTab("status")
    ctx.openWidget("agents")
    ctx.pinWidget("agents")
    expect(ctx.widget.value).toBe(null)
    expect(ctx.side.value).toEqual({ kind: "widget", payload: { id: "agents" } })
    ctx.openWidget("search")
    ctx.openSide("canvas", { index: 2 })
    expect(ctx.tab.value).toBe("chat")
    expect(ctx.side.value.kind).toBe("canvas")
    expect(ctx.widget.value).toBe(null)
  })

  it("opens the add dialog on a kind and closes it", () => {
    const ctx = make()
    expect(ctx.addKind.value).toBe(null)
    ctx.openAdd("channel")
    expect(ctx.addKind.value).toBe("channel")
    ctx.openAdd()
    expect(ctx.addKind.value).toBe("creature")
    ctx.closeAdd()
    expect(ctx.addKind.value).toBe(null)
  })

  it("remembers the tab per session", () => {
    make("a").setTab("debug")
    expect(make("a").tab.value).toBe("debug")
    expect(make("b").tab.value).toBe("chat")
  })
})
