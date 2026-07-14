/**
 * Register the built-in tab kinds. Each phase enables more kinds:
 *
 *   Phase 2 — none (PlaceholderTab is the fallback)
 *   Phase 3 — kind: "inspector"
 *   Phase 4 — kind: "dashboard"
 *   Phase 5 — kinds: "attach", "session-viewer", "studio-editor",
 *             "catalog", "settings", "code-editor"
 */

import { registerTabKind } from "@/stores/tabKindRegistry"

import AgentInspectorTab from "@/components/shell/tabs/AgentInspectorTab.vue"
import Dashboard from "@/components/shell/tabs/Dashboard.vue"
import AttachTab from "@/components/shell/tabs/AttachTab.vue"
import SessionViewerTab from "@/components/shell/tabs/SessionViewerTab.vue"
import SavedSessionsTab from "@/components/shell/tabs/SavedSessionsTab.vue"
import StatsTab from "@/components/shell/tabs/StatsTab.vue"
import StudioEditorTab from "@/components/shell/tabs/StudioEditorTab.vue"
import CatalogTab from "@/components/shell/tabs/CatalogTab.vue"
import ExtensionsTab from "@/components/shell/tabs/ExtensionsTab.vue"
import SettingsTab from "@/components/shell/tabs/SettingsTab.vue"
import CodeEditorTab from "@/components/shell/tabs/CodeEditorTab.vue"
import AdminTab from "@/components/shell/tabs/AdminTab.vue"

let _registered = false

export function registerBuiltinTabKinds() {
  if (_registered) return
  _registered = true

  // ── Phase 3 — Inspector ───────────────────────────────────────
  // The inspector IS the session-history-viewer bound to the live
  // session id (UXI-01); there are no separate inner tabs.
  registerTabKind({ kind: "inspector", component: AgentInspectorTab })

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
  registerTabKind({ kind: "extensions", component: ExtensionsTab })
  registerTabKind({ kind: "settings", component: SettingsTab })
  registerTabKind({ kind: "code-editor", component: CodeEditorTab })
  // Admin portal (L4 user/invitation/token management). The launcher is
  // gated on the admin role in the rail; the tab itself re-checks.
  registerTabKind({ kind: "admin", component: AdminTab })
}
