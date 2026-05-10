/**
 * Register the built-in tab kinds. Each phase enables more kinds:
 *
 *   Phase 2 — none (PlaceholderTab is the fallback)
 *   Phase 3 — kind: "inspector"
 *   Phase 4 — kind: "dashboard"
 *   Phase 5 — kinds: "attach", "session-viewer", "studio-editor",
 *             "catalog", "settings", "code-editor"
 */

import { registerTabKind, registerInspectorInnerTab } from "@/stores/tabKindRegistry"

import AgentInspectorTab from "@/components/shell/tabs/AgentInspectorTab.vue"
import InspectorOverview from "@/components/shell/tabs/inspector/InspectorOverview.vue"
import InspectorActivity from "@/components/shell/tabs/inspector/InspectorActivity.vue"
import InspectorTrace from "@/components/shell/tabs/inspector/InspectorTrace.vue"
import InspectorLog from "@/components/shell/tabs/inspector/InspectorLog.vue"
import Dashboard from "@/components/shell/tabs/Dashboard.vue"
import AttachTab from "@/components/shell/tabs/AttachTab.vue"
import SessionViewerTab from "@/components/shell/tabs/SessionViewerTab.vue"
import SavedSessionsTab from "@/components/shell/tabs/SavedSessionsTab.vue"
import StatsTab from "@/components/shell/tabs/StatsTab.vue"
import StudioEditorTab from "@/components/shell/tabs/StudioEditorTab.vue"
import CatalogTab from "@/components/shell/tabs/CatalogTab.vue"
import SettingsTab from "@/components/shell/tabs/SettingsTab.vue"
import CodeEditorTab from "@/components/shell/tabs/CodeEditorTab.vue"

let _registered = false

export function registerBuiltinTabKinds() {
  if (_registered) return
  _registered = true

  // ── Phase 3 — Inspector ───────────────────────────────────────
  registerTabKind({ kind: "inspector", component: AgentInspectorTab })
  registerInspectorInnerTab({
    id: "overview",
    component: InspectorOverview,
    label: "Overview",
    order: 10,
  })
  registerInspectorInnerTab({
    id: "activity",
    component: InspectorActivity,
    label: "Activity",
    order: 20,
  })
  registerInspectorInnerTab({
    id: "trace",
    component: InspectorTrace,
    label: "Trace",
    order: 30,
  })
  registerInspectorInnerTab({
    id: "log",
    component: InspectorLog,
    label: "Log",
    order: 40,
  })

  // ── Phase 4 — Dashboard ───────────────────────────────────────
  registerTabKind({ kind: "dashboard", component: Dashboard })

  // ── Phase 5 — AttachTab + thin embeds ─────────────────────────
  registerTabKind({ kind: "attach", component: AttachTab })
  registerTabKind({ kind: "session-viewer", component: SessionViewerTab })
  registerTabKind({ kind: "saved-sessions", component: SavedSessionsTab })
  registerTabKind({ kind: "stats", component: StatsTab })
  // Studio uses a file-tree + Monaco master-detail layout that
  // genuinely needs horizontal room — Monaco on a phone is fiddly
  // even ignoring the missing tree pane. On compact it shows an
  // UnderDensityPlaceholder with a "switch to desktop mode" button.
  // Catalog/Registry already reflows via Tailwind grid breakpoints
  // (1 → 2 → 3 columns) so no gating needed.
  registerTabKind({ kind: "studio-editor", component: StudioEditorTab, minDensity: "regular" })
  registerTabKind({ kind: "catalog", component: CatalogTab })
  registerTabKind({ kind: "settings", component: SettingsTab })
  registerTabKind({ kind: "code-editor", component: CodeEditorTab })
}
