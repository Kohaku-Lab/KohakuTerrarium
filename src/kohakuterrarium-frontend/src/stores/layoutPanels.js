/**
 * Panel + preset registration. Called once from main.js (synchronous).
 *
 * Presets use a binary split tree:
 *   SplitNode = { type: "split", direction: "horizontal"|"vertical",
 *                 ratio: 0-100, children: [Node, Node] }
 *   LeafNode  = { type: "leaf", panelId: string }
 */

import ChatPanelContainer from "@/components/chat/ChatPanelContainer.vue"
import EditorMain from "@/components/editor/EditorMain.vue"
import EditorStatus from "@/components/editor/EditorStatus.vue"
import FileTree from "@/components/editor/FileTree.vue"
import CanvasPanel from "@/components/panels/CanvasPanel.vue"
import CreaturesPanel from "@/components/panels/CreaturesPanel.vue"
import DebugPanel from "@/components/panels/DebugPanel.vue"
import DrivesPanel from "@/components/panels/DrivesPanel.vue"
import FilesPanel from "@/components/panels/FilesPanel.vue"
import ActivityPanel from "@/components/panels/ActivityPanel.vue"
import SettingsPanel from "@/components/panels/SettingsPanel.vue"
import StatePanel from "@/components/panels/StatePanel.vue"
import TerminalPanel from "@/components/panels/TerminalPanel.vue"
import ModulesPanel from "@/components/panels/modules/ModulesPanel.vue"
import StatusDashboard from "@/components/status/StatusDashboard.vue"
import StatusDashboardTab from "@/components/status/StatusDashboardTab.vue"

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

/** The preset every freshly opened session lands on, whatever its shape.
 *  The status rail carries the creature list, so a multi-creature graph
 *  needs no separate layout to be operable. */
export const DEFAULT_PRESET_ID = "chat-focus"

/** Chat focus — chat | status rail (top) + creature state (bottom).
 *  The rail holds session / creatures / tokens / jobs / modules; the
 *  state panel holds drives / scratchpad / memory / compaction. */
const CHAT_FOCUS_PRESET = {
  id: "chat-focus",
  label: "Chat Focus",
  shortcut: "Ctrl+1",
  tree: hsplit(70, leaf("chat"), vsplit(40, leaf("status-tab"), leaf("state"))),
}

/** Workspace — files + editor + chat for code-work creatures. */
const WORKSPACE_PRESET = {
  id: "workspace",
  label: "Workspace",
  shortcut: "Ctrl+2",
  tree: hsplit(
    20,
    leaf("files"),
    hsplit(62, leaf("monaco-editor"), vsplit(65, leaf("chat"), leaf("status-tab"))),
  ),
}

/** Chat + Terminal — chat left, terminal top-right, state + status bottom-right. */
const CHAT_TERMINAL_PRESET = {
  id: "chat-terminal",
  label: "Chat + Terminal",
  shortcut: "Ctrl+6",
  tree: hsplit(
    50,
    leaf("chat"),
    vsplit(65, leaf("terminal"), hsplit(50, leaf("state"), leaf("status-tab"))),
  ),
}

/** Multi-creature — the wide layout with a dedicated creature column. */
const MULTI_CREATURE_PRESET = {
  id: "multi-creature",
  label: "Multi-creature",
  shortcut: "Ctrl+3",
  tree: hsplit(
    18,
    leaf("creatures"),
    hsplit(66, leaf("chat"), vsplit(50, leaf("status-dashboard"), leaf("state"))),
  ),
}

/** Canvas — chat on left, canvas + modules on right. ``modules`` here
 *  takes the slot the legacy ``tool-options`` panel used to occupy
 *  (provider-native tool options) — the unified module surface
 *  subsumes that. */
const CANVAS_PRESET = {
  id: "canvas",
  label: "Canvas",
  shortcut: "Ctrl+4",
  tree: hsplit(45, leaf("chat"), vsplit(70, leaf("canvas"), leaf("modules"))),
}

/** Debug — chat + state + debug drawer. */
const DEBUG_PRESET = {
  id: "debug",
  label: "Debug",
  shortcut: "Ctrl+5",
  tree: vsplit(55, hsplit(60, leaf("chat"), leaf("state")), leaf("debug")),
}

const SETTINGS_PRESET = {
  id: "settings",
  label: "Settings",
  tree: hsplit(62, leaf("chat"), vsplit(55, leaf("settings"), leaf("activity"))),
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
  SETTINGS_PRESET,
  CHAT_TERMINAL_PRESET,
]

// ─── Registration ────────────────────────────────────────────────
//
// Labels and descriptions here are the English fallbacks; the picker
// and palette translate them through ``utils/i18n`` by panel id.

export function registerBuiltinPanels() {
  const layout = useLayoutStore()

  // ── Panels ──
  //
  // ``id: "chat"`` points at the new ``ChatPanelContainer`` (Option E).
  // The container renders a legacy single ``ChatPanel`` when the
  // chat-internal group tree is empty (default + back-compat path),
  // or the recursive ``ChatGroupNode`` tree when the user has opted
  // into multi-group via the Settings toggle / context-menu split.
  // The legacy ``ChatPanel`` is still exported and used directly by
  // ``SessionHistoryViewer.vue`` (read-only session viewer) and tests.
  layout.registerPanel({
    id: "chat",
    label: "Chat",
    description: "The conversation with the focused creature.",
    component: ChatPanelContainer,
  })
  layout.registerPanel({
    id: "status-dashboard",
    label: "Overview",
    description: "Session identity, model, tokens, and jobs on one scroll.",
    component: StatusDashboard,
  })
  // Legacy alias — legacy-editor preset references this id.
  layout.registerPanel({
    id: "file-tree",
    label: "File Tree",
    component: FileTree,
    hidden: true,
  })
  layout.registerPanel({
    id: "monaco-editor",
    label: "Editor",
    description: "Code editor for the workspace.",
    component: EditorMain,
  })
  // Legacy alias — legacy-editor preset references this id.
  layout.registerPanel({
    id: "editor-status",
    label: "Editor Status",
    component: EditorStatus,
    hidden: true,
  })
  layout.registerPanel({
    id: "files",
    label: "Files",
    description: "Workspace file tree and files the creature touched.",
    component: FilesPanel,
  })
  layout.registerPanel({
    id: "activity",
    label: "Jobs",
    description: "Model, context usage, and running jobs.",
    component: ActivityPanel,
  })
  layout.registerPanel({
    id: "settings",
    label: "Settings",
    description: "Per-instance settings.",
    component: SettingsPanel,
  })
  layout.registerPanel({
    id: "state",
    label: "Creature State",
    description: "Drives, scratchpad, memory search, and compaction for the focused creature.",
    component: StatePanel,
  })
  layout.registerPanel({
    id: "creatures",
    label: "Creatures",
    description: "Graph members and channels for a multi-creature session.",
    component: CreaturesPanel,
  })
  layout.registerPanel({
    id: "canvas",
    label: "Canvas",
    description: "Images and artifacts the creature produced.",
    component: CanvasPanel,
  })
  layout.registerPanel({
    id: "debug",
    label: "Debug",
    description: "Runtime logs and diagnostics.",
    component: DebugPanel,
  })
  layout.registerPanel({
    id: "status-tab",
    label: "Status",
    description: "Session, creatures, tokens, jobs, and modules in one rail.",
    component: StatusDashboardTab,
  })
  layout.registerPanel({
    id: "terminal",
    label: "Terminal",
    description: "A terminal in the creature's working directory.",
    component: TerminalPanel,
  })
  layout.registerPanel({
    id: "modules",
    label: "Modules",
    description: "Tools, plugins, triggers, and other runtime modules.",
    component: ModulesPanel,
  })
  // The full Drive record panel. The compact per-creature view lives in
  // the Creature State panel; this one is the option for whole-session
  // management and stays out of the default presets.
  layout.registerPanel({
    id: "drives",
    label: "Drives",
    description: "Every drive in the session with full management.",
    component: DrivesPanel,
  })

  // ── Presets ──
  layout.registerBuiltinPreset(LEGACY_INSTANCE_PRESET)
  layout.registerBuiltinPreset(LEGACY_EDITOR_PRESET)
  for (const preset of DEFAULT_PRESETS) {
    layout.registerBuiltinPreset(preset)
  }
}
