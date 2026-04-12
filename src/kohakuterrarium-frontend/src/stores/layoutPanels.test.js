import { beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"

import { useLayoutStore } from "./layout.js"

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

beforeEach(() => {
  setActivePinia(createPinia())
  if (typeof localStorage !== "undefined") localStorage.clear()
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
})
