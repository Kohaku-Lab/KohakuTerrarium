import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"

import { useLayoutStore } from "./layout.js"
import { _resetUIPrefsForTests } from "@/utils/uiPrefs.js"

const stub = (name) => ({ name, render: () => null })

vi.mock("@/components/chat/ChatPanel.vue", () => ({
  default: stub("ChatPanel"),
}))
vi.mock("@/components/editor/EditorMain.vue", () => ({
  default: stub("EditorMain"),
}))
vi.mock("@/components/editor/EditorStatus.vue", () => ({
  default: stub("EditorStatus"),
}))
vi.mock("@/components/editor/FileTree.vue", () => ({
  default: stub("FileTree"),
}))
vi.mock("@/components/panels/ActivityPanel.vue", () => ({
  default: stub("ActivityPanel"),
}))
vi.mock("@/components/panels/CanvasPanel.vue", () => ({
  default: stub("CanvasPanel"),
}))
vi.mock("@/components/panels/CreaturesPanel.vue", () => ({
  default: stub("CreaturesPanel"),
}))
vi.mock("@/components/panels/DebugPanel.vue", () => ({
  default: stub("DebugPanel"),
}))
vi.mock("@/components/panels/FilesPanel.vue", () => ({
  default: stub("FilesPanel"),
}))
vi.mock("@/components/panels/SettingsPanel.vue", () => ({
  default: stub("SettingsPanel"),
}))
vi.mock("@/components/panels/StatePanel.vue", () => ({
  default: stub("StatePanel"),
}))
vi.mock("@/components/status/StatusDashboard.vue", () => ({
  default: stub("StatusDashboard"),
}))

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

describe("layoutPanels — registerBuiltinPanels", () => {
  it("registers every panel id", async () => {
    const { registerBuiltinPanels } = await import("./layoutPanels.js")
    registerBuiltinPanels()
    const store = useLayoutStore()
    const expected = [
      "chat",
      "status-dashboard",
      "file-tree",
      "monaco-editor",
      "editor-status",
      "files",
      "activity",
      "state",
      "creatures",
      "canvas",
      "settings",
      "debug",
    ]
    for (const id of expected) {
      const p = store.getPanel(id)
      expect(p, `panel ${id} should be registered`).not.toBeNull()
      expect(p.component).toBeTruthy()
    }
  })

  it("registers default presets with tree field", async () => {
    const { registerBuiltinPanels } = await import("./layoutPanels.js")
    registerBuiltinPanels()
    const store = useLayoutStore()
    for (const id of ["chat-focus", "workspace", "multi-creature", "canvas", "debug", "settings"]) {
      const p = store.allPresets[id]
      expect(p, `preset ${id} should exist`).toBeDefined()
      expect(p.tree, `preset ${id} should have a tree`).toBeDefined()
      expect(p.tree.type).toMatch(/leaf|split/)
    }
  })

  function leaves(node, out = []) {
    if (!node) return out
    if (node.type === "leaf") out.push(node.panelId)
    for (const child of node.children || []) leaves(child, out)
    return out
  }

  it("chat-focus is the default and pairs the status rail with creature state", async () => {
    const { DEFAULT_PRESET_ID, registerBuiltinPanels } = await import("./layoutPanels.js")
    registerBuiltinPanels()
    const store = useLayoutStore()
    expect(DEFAULT_PRESET_ID).toBe("chat-focus")
    const tree = store.allPresets["chat-focus"].tree
    expect(leaves(tree)).toEqual(["chat", "status-tab", "state"])
    // The state column gets the larger share: drives and scratchpad need
    // height, the status rail does not.
    const right = tree.children[1]
    expect(right.direction).toBe("vertical")
    expect(right.ratio).toBeLessThan(50)
  })

  it("hides legacy aliases from the picker but keeps them resolvable", async () => {
    const { registerBuiltinPanels } = await import("./layoutPanels.js")
    registerBuiltinPanels()
    const store = useLayoutStore()
    for (const id of ["file-tree", "editor-status"]) {
      expect(store.getPanel(id).hidden).toBe(true)
      expect(store.visiblePanelList.some((p) => p.id === id)).toBe(false)
    }
    const visible = store.visiblePanelList.map((p) => p.id)
    expect(visible).toContain("status-tab")
    expect(visible).toContain("drives")
  })

  it("gives every visible panel a distinct label and a description", async () => {
    const { registerBuiltinPanels } = await import("./layoutPanels.js")
    registerBuiltinPanels()
    const store = useLayoutStore()
    const labels = store.visiblePanelList.map((p) => p.label)
    expect(new Set(labels).size).toBe(labels.length)
    for (const p of store.visiblePanelList) {
      expect(p.description, `panel ${p.id} needs a description`).toBeTruthy()
    }
    expect(store.getPanel("status-dashboard").label).toBe("Overview")
    expect(store.getPanel("activity").label).toBe("Jobs")
    expect(store.getPanel("state").label).toBe("Creature State")
  })
})
