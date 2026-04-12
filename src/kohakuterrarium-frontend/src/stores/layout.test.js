import { beforeEach, describe, expect, it } from "vitest"
import { createPinia, setActivePinia } from "pinia"

import { useLayoutStore } from "./layout.js"

function makeBuiltinPreset(id = "legacy-instance") {
  return {
    id,
    label: "Legacy Instance",
    zones: {
      "left-sidebar": { visible: true, size: 15 },
      main: { visible: true, size: 65 },
      "right-sidebar": { visible: true, size: 20 },
    },
    slots: [
      { zoneId: "main", panelId: "chat" },
      { zoneId: "right-sidebar", panelId: "status-dashboard" },
    ],
  }
}

function fakeComponent(name) {
  return { name, render: () => null }
}

beforeEach(() => {
  // Fresh pinia + fresh localStorage for every test.
  setActivePinia(createPinia())
  if (typeof localStorage !== "undefined") {
    localStorage.clear()
  }
})

describe("layout store — panel registry", () => {
  it("registers and retrieves a panel", () => {
    const store = useLayoutStore()
    const cmp = fakeComponent("Chat")
    store.registerPanel({
      id: "chat",
      label: "Chat",
      component: cmp,
      preferredZones: ["main", "right-sidebar"],
    })
    const panel = store.getPanel("chat")
    expect(panel).not.toBeNull()
    expect(panel.id).toBe("chat")
    expect(panel.label).toBe("Chat")
    expect(panel.component).toBe(cmp)
    expect(panel.supportsDetach).toBe(true)
  })

  it("unregisters a panel cleanly", () => {
    const store = useLayoutStore()
    store.registerPanel({ id: "chat", component: fakeComponent("Chat") })
    expect(store.getPanel("chat")).not.toBeNull()
    store.unregisterPanel("chat")
    expect(store.getPanel("chat")).toBeNull()
  })

  it("registerPanel is idempotent (replaces existing)", () => {
    const store = useLayoutStore()
    store.registerPanel({
      id: "chat",
      label: "v1",
      component: fakeComponent("A"),
    })
    store.registerPanel({
      id: "chat",
      label: "v2",
      component: fakeComponent("B"),
    })
    expect(store.getPanel("chat").label).toBe("v2")
  })
})

describe("layout store — preset switching", () => {
  it("switches to a registered builtin", () => {
    const store = useLayoutStore()
    store.registerBuiltinPreset(makeBuiltinPreset())
    store.switchPreset("legacy-instance")
    expect(store.activePresetId).toBe("legacy-instance")
    expect(store.activePreset?.id).toBe("legacy-instance")
  })

  it("ignores unknown preset ids", () => {
    const store = useLayoutStore()
    store.switchPreset("ghost")
    expect(store.activePresetId).toBeNull()
  })

  it("returns slots per zone from the active preset", () => {
    const store = useLayoutStore()
    store.registerBuiltinPreset(makeBuiltinPreset())
    store.switchPreset("legacy-instance")
    const mainSlots = store.slotsForZone("main")
    expect(mainSlots).toHaveLength(1)
    expect(mainSlots[0].panelId).toBe("chat")
    const rightSlots = store.slotsForZone("right-sidebar")
    expect(rightSlots[0].panelId).toBe("status-dashboard")
  })
})

describe("layout store — user presets", () => {
  it("saves current active preset as a new user preset and persists it", () => {
    const store = useLayoutStore()
    store.registerBuiltinPreset(makeBuiltinPreset())
    store.switchPreset("legacy-instance")
    const saved = store.saveAsNewPreset("my-chat", "My Chat", "Alt+1")
    expect(saved.id).toBe("my-chat")
    expect(saved.shortcut).toBe("Alt+1")
    expect(store.activePresetId).toBe("my-chat")
    // persistence
    const stored = JSON.parse(localStorage.getItem("kt.presets.user"))
    expect(stored["my-chat"]).toBeDefined()
    expect(stored["my-chat"].label).toBe("My Chat")
  })

  it("deletes a user preset and falls back to builtin", () => {
    const store = useLayoutStore()
    store.registerBuiltinPreset(makeBuiltinPreset())
    store.switchPreset("legacy-instance")
    store.saveAsNewPreset("my-chat", "My Chat")
    expect(store.activePresetId).toBe("my-chat")
    store.deleteUserPreset("my-chat")
    expect(store.allPresets["my-chat"]).toBeUndefined()
    expect(store.activePresetId).toBe("legacy-instance")
  })

  it("restores user presets from localStorage on fresh store", () => {
    const snapshot = {
      "my-chat": {
        id: "my-chat",
        label: "My Chat",
        zones: {},
        slots: [{ zoneId: "main", panelId: "chat" }],
      },
    }
    localStorage.setItem("kt.presets.user", JSON.stringify(snapshot))
    setActivePinia(createPinia())
    const store = useLayoutStore()
    expect(store.allPresets["my-chat"]).toBeDefined()
    expect(store.allPresets["my-chat"].label).toBe("My Chat")
  })
})

describe("layout store — per-instance overrides", () => {
  it("per-instance override patches the effective preset", () => {
    const store = useLayoutStore()
    store.registerBuiltinPreset(makeBuiltinPreset())
    store.switchPreset("legacy-instance")
    store.setInstanceOverride("inst-1", {
      zones: { main: { size: 80 } },
    })
    const eff = store.effectivePreset("inst-1")
    expect(eff.zones.main.size).toBe(80)
    // Original untouched
    const otherEff = store.effectivePreset("inst-2")
    expect(otherEff.zones.main.size).toBe(65)
  })

  it("clearInstanceOverride removes it from memory and localStorage", () => {
    const store = useLayoutStore()
    store.registerBuiltinPreset(makeBuiltinPreset())
    store.switchPreset("legacy-instance")
    store.setInstanceOverride("inst-1", { zones: { main: { size: 80 } } })
    expect(localStorage.getItem("kt.layout.instance.inst-1")).not.toBeNull()
    store.clearInstanceOverride("inst-1")
    expect(store.instanceOverrides["inst-1"]).toBeUndefined()
    expect(localStorage.getItem("kt.layout.instance.inst-1")).toBeNull()
  })

  it("loadInstanceOverrides pulls persisted patch back", () => {
    localStorage.setItem(
      "kt.layout.instance.inst-1",
      JSON.stringify({ zones: { main: { size: 50 } } }),
    )
    const store = useLayoutStore()
    store.registerBuiltinPreset(makeBuiltinPreset())
    store.switchPreset("legacy-instance")
    store.loadInstanceOverrides("inst-1")
    const eff = store.effectivePreset("inst-1")
    expect(eff.zones.main.size).toBe(50)
  })
})

describe("layout store — per-instance preset persistence", () => {
  it("remembers and retrieves the active preset per instance", () => {
    const store = useLayoutStore()
    store.registerBuiltinPreset(makeBuiltinPreset("a"))
    store.registerBuiltinPreset(makeBuiltinPreset("b"))
    store.switchPreset("a")
    store.rememberInstancePreset("inst-1", "a")
    expect(store.getInstancePresetId("inst-1")).toBe("a")
    // Fresh pinia reads from localStorage.
    setActivePinia(createPinia())
    const store2 = useLayoutStore()
    expect(store2.getInstancePresetId("inst-1")).toBe("a")
  })

  it("updates the persisted preset when rememberInstancePreset is called again", () => {
    const store = useLayoutStore()
    store.registerBuiltinPreset(makeBuiltinPreset("a"))
    store.registerBuiltinPreset(makeBuiltinPreset("b"))
    store.rememberInstancePreset("inst-1", "a")
    store.rememberInstancePreset("inst-1", "b")
    expect(store.getInstancePresetId("inst-1")).toBe("b")
  })
})

describe("layout store — edit mode", () => {
  function setupWithActive() {
    const store = useLayoutStore()
    store.registerBuiltinPreset(makeBuiltinPreset())
    store.switchPreset("legacy-instance")
    return store
  }

  it("enter/exit edit mode toggles state and snapshots the preset", () => {
    const store = setupWithActive()
    expect(store.editMode).toBe(false)
    store.enterEditMode()
    expect(store.editMode).toBe(true)
    expect(store.editModeSnapshot?.id).toBe("legacy-instance")
    store.exitEditMode()
    expect(store.editMode).toBe(false)
    expect(store.editModeSnapshot).toBeNull()
  })

  it("replaceSlotPanel mutates the active preset and flips dirty", () => {
    const store = setupWithActive()
    store.enterEditMode()
    store.replaceSlotPanel("main", "chat", "status-dashboard")
    const slots = store.activePreset.slots
    expect(slots.find((s) => s.zoneId === "main").panelId).toBe("status-dashboard")
    expect(store.editModeDirty).toBe(true)
  })

  it("removeSlot drops the target slot", () => {
    const store = setupWithActive()
    store.enterEditMode()
    store.removeSlot("right-sidebar", "status-dashboard")
    expect(store.activePreset.slots.filter((s) => s.zoneId === "right-sidebar")).toHaveLength(0)
    expect(store.editModeDirty).toBe(true)
  })

  it("addSlotToZone appends and makes zone visible", () => {
    const store = setupWithActive()
    store.enterEditMode()
    // Left sidebar is hidden in makeBuiltinPreset; addSlotToZone should
    // make it visible and add the slot.
    store.addSlotToZone("left-sidebar", "chat")
    expect(store.activePreset.zones["left-sidebar"].visible).toBe(true)
    expect(store.activePreset.slots.find((s) => s.zoneId === "left-sidebar")?.panelId).toBe("chat")
  })

  it("revertEditMode restores the snapshot", () => {
    const store = setupWithActive()
    store.enterEditMode()
    store.replaceSlotPanel("main", "chat", "status-dashboard")
    expect(store.activePreset.slots.find((s) => s.zoneId === "main").panelId).toBe(
      "status-dashboard",
    )
    store.revertEditMode()
    expect(store.activePreset.slots.find((s) => s.zoneId === "main").panelId).toBe("chat")
    expect(store.editModeDirty).toBe(false)
  })
})

describe("layout store — detached panels", () => {
  it("tracks detached panels without duplicates", () => {
    const store = useLayoutStore()
    store.markDetached("chat", "inst-1")
    store.markDetached("chat", "inst-1")
    expect(store.detachedPanels).toHaveLength(1)
    store.markDetached("chat", "inst-2")
    expect(store.detachedPanels).toHaveLength(2)
    store.unmarkDetached("chat", "inst-1")
    expect(store.detachedPanels).toHaveLength(1)
    expect(store.detachedPanels[0].instanceId).toBe("inst-2")
  })
})
