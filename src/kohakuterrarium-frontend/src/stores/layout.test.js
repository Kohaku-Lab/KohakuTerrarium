import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"

import { useLayoutStore } from "./layout.js"
import { _resetUIPrefsForTests } from "@/utils/uiPrefs.js"

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

let storage

beforeEach(() => {
  _resetUIPrefsForTests()
  setActivePinia(createPinia())
  storage = new Map()
  vi.stubGlobal("localStorage", {
    getItem: (key) => (storage.has(key) ? storage.get(key) : null),
    setItem: (key, value) => storage.set(key, String(value)),
    removeItem: (key) => storage.delete(key),
    clear: () => storage.clear(),
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
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

describe("layout store — hidden panels and descriptions", () => {
  it("normalizes hidden and description and filters hidden panels from the visible list", () => {
    const store = useLayoutStore()
    store.registerPanel({
      id: "shown",
      label: "Shown",
      component: fakeComponent("Shown"),
      description: "A panel",
    })
    store.registerPanel({
      id: "alias",
      label: "Alias",
      component: fakeComponent("Alias"),
      hidden: true,
    })
    expect(store.getPanel("shown").hidden).toBe(false)
    expect(store.getPanel("shown").description).toBe("A panel")
    expect(store.getPanel("alias").hidden).toBe(true)
    expect(store.getPanel("alias").description).toBe("")
    expect(store.panelList.map((p) => p.id)).toEqual(expect.arrayContaining(["shown", "alias"]))
    expect(store.visiblePanelList.map((p) => p.id)).toContain("shown")
    expect(store.visiblePanelList.map((p) => p.id)).not.toContain("alias")
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
    localStorage.removeItem("kt.layout.activePreset")
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

describe("layout store — save-as-new keeps the edited tree", () => {
  function editableBuiltin(id = "chat-focus") {
    return {
      ...makeBuiltinPreset(id),
      tree: {
        type: "split",
        direction: "horizontal",
        ratio: 50,
        children: [
          { type: "leaf", panelId: "chat" },
          { type: "leaf", panelId: "status-dashboard" },
        ],
      },
    }
  }

  it("a builtin edited then saved as new lands the edited tree in the new preset", () => {
    const store = useLayoutStore()
    store.registerBuiltinPreset(editableBuiltin())
    store.switchPreset("chat-focus")
    store.enterEditMode()
    const leaf = store.activePreset.tree.children[1]
    store.replaceTreePanel(leaf, "drives")
    expect(store.activePreset.tree.children[1].panelId).toBe("drives")

    const saved = store.saveAsNewPreset("mine", "Mine")
    expect(saved.tree.children[1].panelId).toBe("drives")
    // The flow the shell runs right after the modal closes.
    if (store.editMode) store.exitEditMode()

    expect(store.activePresetId).toBe("mine")
    expect(store.activePreset.builtin).toBe(false)
    expect(store.activePreset.id).toBe("mine")
    expect(store.activePreset.tree.children[1].panelId).toBe("drives")
    // Persisted copy agrees with the in-memory one, so no reload is needed.
    expect(JSON.parse(storage.get("kt.presets.user")).mine.tree.children[1].panelId).toBe("drives")
    // The builtin returns to its pristine shape.
    expect(store.builtinPresets["chat-focus"].tree.children[1].panelId).toBe("status-dashboard")
    expect(store.editMode).toBe(false)
  })

  it("exitEditMode never restores a snapshot over a different active preset", () => {
    const store = useLayoutStore()
    store.registerBuiltinPreset(editableBuiltin("chat-focus"))
    store.registerBuiltinPreset(editableBuiltin("workspace"))
    store.switchPreset("chat-focus")
    store.enterEditMode()
    store.replaceTreePanel(store.activePreset.tree.children[1], "drives")
    store.saveAsNewPreset("mine", "Mine")
    store.switchPreset("workspace")
    store.exitEditMode()
    expect(store.userPresets.mine.tree.children[1].panelId).toBe("drives")
    expect(store.builtinPresets.workspace.tree.children[1].panelId).toBe("status-dashboard")
  })

  it("patches can never rewrite a preset's identity", () => {
    const store = useLayoutStore()
    store.registerBuiltinPreset(editableBuiltin())
    store.switchPreset("chat-focus")
    store.saveAsNewPreset("mine", "Mine")
    store.enterEditMode()
    store.addSlotToZone("main", "drives")
    expect(store.activePreset.id).toBe("mine")
    expect(store.activePreset.builtin).toBe(false)
  })
})
