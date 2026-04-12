/**
 * Panel + preset registration. Called once from main.js (synchronous).
 *
 * Presets use a binary split tree:
 *   SplitNode = { type: "split", direction: "horizontal"|"vertical",
 *                 ratio: 0-100, children: [Node, Node] }
 *   LeafNode  = { type: "leaf", panelId: string }
 */

import ChatPanel from "@/components/chat/ChatPanel.vue"
import EditorMain from "@/components/editor/EditorMain.vue"
import EditorStatus from "@/components/editor/EditorStatus.vue"
import FileTree from "@/components/editor/FileTree.vue"
import CanvasPanel from "@/components/panels/CanvasPanel.vue"
import CreaturesPanel from "@/components/panels/CreaturesPanel.vue"
import DebugPanel from "@/components/panels/DebugPanel.vue"
import FilesPanel from "@/components/panels/FilesPanel.vue"
import StatePanel from "@/components/panels/StatePanel.vue"
import TerminalPanel from "@/components/panels/TerminalPanel.vue"
import StatusDashboard from "@/components/status/StatusDashboard.vue"

import { useLayoutStore } from "@/stores/layout"

// ─── Helper to build tree nodes concisely ────────────────────────

function leaf(panelId) {
  return { type: "leaf", panelId }
}

function hsplit(ratio, left, right) {
  return {
    type: "split",
    direction: "horizontal",
    ratio,
    children: [left, right],
  }
}

function vsplit(ratio, top, bottom) {
  return {
    type: "split",
    direction: "vertical",
    ratio,
    children: [top, bottom],
  }
}

// ─── Presets ─────────────────────────────────────────────────────

/** Chat focus — default for single-creature instances.
 *  chat | status-dashboard(large, top) + state(small, bottom) */
const CHAT_FOCUS_PRESET = {
  id: "chat-focus",
  label: "Chat Focus",
  shortcut: "Ctrl+1",
  tree: hsplit(70, leaf("chat"), vsplit(65, leaf("status-dashboard"), leaf("state"))),
}

/** Workspace — files + editor + chat for code-work creatures. */
const WORKSPACE_PRESET = {
  id: "workspace",
  label: "Workspace",
  shortcut: "Ctrl+2",
  tree: hsplit(
    20,
    leaf("files"),
    hsplit(62, leaf("monaco-editor"), vsplit(65, leaf("chat"), leaf("activity"))),
  ),
}

/** Chat + Terminal — chat on left, terminal on top-right, status on bottom-right. */
const CHAT_TERMINAL_PRESET = {
  id: "chat-terminal",
  label: "Chat + Terminal",
  shortcut: "Ctrl+6",
  tree: hsplit(55, leaf("chat"), vsplit(70, leaf("terminal"), leaf("status-dashboard"))),
}

/** Multi-creature — default for terrarium instances. */
const MULTI_CREATURE_PRESET = {
  id: "multi-creature",
  label: "Multi-creature",
  shortcut: "Ctrl+3",
  tree: hsplit(
    18,
    leaf("creatures"),
    hsplit(66, leaf("chat"), vsplit(50, leaf("activity"), leaf("state"))),
  ),
}

/** Canvas — chat on left, canvas + activity on right. */
const CANVAS_PRESET = {
  id: "canvas",
  label: "Canvas",
  shortcut: "Ctrl+4",
  tree: hsplit(45, leaf("chat"), vsplit(70, leaf("canvas"), leaf("activity"))),
}

/** Debug — chat + state + debug drawer. */
const DEBUG_PRESET = {
  id: "debug",
  label: "Debug",
  shortcut: "Ctrl+5",
  tree: vsplit(55, hsplit(60, leaf("chat"), leaf("state")), leaf("debug")),
}

/** Legacy instance (old layout compat). */
const LEGACY_INSTANCE_PRESET = {
  id: "legacy-instance",
  label: "Legacy Instance",
  tree: hsplit(65, leaf("chat"), leaf("status-dashboard")),
}

/** Legacy editor (old layout compat). */
const LEGACY_EDITOR_PRESET = {
  id: "legacy-editor",
  label: "Legacy Editor",
  tree: hsplit(
    20,
    leaf("file-tree"),
    hsplit(60, leaf("monaco-editor"), vsplit(70, leaf("chat"), leaf("editor-status"))),
  ),
}

export const DEFAULT_PRESETS = [
  CHAT_FOCUS_PRESET,
  WORKSPACE_PRESET,
  MULTI_CREATURE_PRESET,
  CANVAS_PRESET,
  DEBUG_PRESET,
  CHAT_TERMINAL_PRESET,
]

// ─── Registration ────────────────────────────────────────────────

export function registerBuiltinPanels() {
  const layout = useLayoutStore()

  // ── Panels ──
  layout.registerPanel({ id: "chat", label: "Chat", component: ChatPanel })
  layout.registerPanel({
    id: "status-dashboard",
    label: "Status",
    component: StatusDashboard,
  })
  layout.registerPanel({
    id: "file-tree",
    label: "File Tree",
    component: FileTree,
  })
  layout.registerPanel({
    id: "monaco-editor",
    label: "Editor",
    component: EditorMain,
  })
  // Legacy alias — legacy-editor preset references this id.
  layout.registerPanel({
    id: "editor-status",
    label: "Activity",
    component: EditorStatus,
  })
  layout.registerPanel({ id: "files", label: "Files", component: FilesPanel })
  layout.registerPanel({
    id: "activity",
    label: "Activity",
    component: EditorStatus,
  })
  layout.registerPanel({ id: "state", label: "State", component: StatePanel })
  layout.registerPanel({
    id: "creatures",
    label: "Creatures",
    component: CreaturesPanel,
  })
  layout.registerPanel({
    id: "canvas",
    label: "Canvas",
    component: CanvasPanel,
  })
  layout.registerPanel({ id: "debug", label: "Debug", component: DebugPanel })
  layout.registerPanel({
    id: "terminal",
    label: "Terminal",
    component: TerminalPanel,
  })

  // ── Presets ──
  layout.registerBuiltinPreset(LEGACY_INSTANCE_PRESET)
  layout.registerBuiltinPreset(LEGACY_EDITOR_PRESET)
  for (const preset of DEFAULT_PRESETS) {
    layout.registerBuiltinPreset(preset)
  }
}
