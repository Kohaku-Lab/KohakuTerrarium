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

  it("keeps one phone sheet; a widget, a side view or the add dialog replaces it", () => {
    const ctx = make()
    ctx.openSheet("model", { agent: "lead" })
    expect(ctx.sheet.value).toEqual({ kind: "model", payload: { agent: "lead" } })
    ctx.openSheet("menu")
    expect(ctx.sheet.value).toEqual({ kind: "menu", payload: {} })
    ctx.openWidget("agents")
    expect(ctx.sheet.value).toBe(null)
    ctx.openSheet("menu")
    ctx.openSide("terminal")
    expect(ctx.sheet.value).toBe(null)
    ctx.openSheet("menu")
    ctx.openAdd("creature")
    expect(ctx.sheet.value).toBe(null)
    ctx.openSheet("conversations")
    ctx.closeSheet()
    expect(ctx.sheet.value).toBe(null)
  })

  it("on a phone shows a side view over the current tab", () => {
    const ctx = createSessionV2({
      instance: computed(() => ({ id: "p" })),
      instanceId: ref("p"),
      chat: {},
      phone: ref(true),
    })
    ctx.setTab("status")
    ctx.openSide("drive", { driveId: "d1" })
    expect(ctx.tab.value).toBe("status")
    expect(ctx.side.value).toEqual({ kind: "drive", payload: { driveId: "d1" } })
  })

  it("has no Workspace on a phone: picking it is ignored, a remembered one shows Chat until wide again", () => {
    const phone = ref(false)
    const ctx = createSessionV2({
      instance: computed(() => ({ id: "w" })),
      instanceId: ref("w"),
      chat: {},
      phone,
    })
    ctx.setTab("workspace")
    phone.value = true
    expect(ctx.tab.value).toBe("chat")
    ctx.setTab("status")
    ctx.setTab("workspace")
    expect(ctx.tab.value).toBe("status")
    ctx.setTab("workspace")
    phone.value = false
    expect(ctx.tab.value).toBe("status")
    ctx.setTab("workspace")
    phone.value = true
    expect(ctx.tab.value).toBe("chat")
    phone.value = false
    expect(ctx.tab.value).toBe("workspace")
  })

  it("remembers the tab per session", () => {
    make("a").setTab("debug")
    expect(make("a").tab.value).toBe("debug")
    expect(make("b").tab.value).toBe("chat")
  })
})
