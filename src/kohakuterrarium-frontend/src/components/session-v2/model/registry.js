/**
 * Which component renders each v2 widget, side view and session tab.
 * Components load on first use, so a session pays only for what it opens.
 */

import { defineAsyncComponent } from "vue"

const lazy = (loader) => defineAsyncComponent(loader)

/** Floating widgets the dock opens; each also renders full height when pinned to the side view. */
export const WIDGETS = {
  agents: lazy(() => import("../dock/widgets/AgentsWidget.vue")),
  channels: lazy(() => import("../dock/widgets/ChannelsWidget.vue")),
  drives: lazy(() => import("../dock/widgets/DrivesWidget.vue")),
  jobs: lazy(() => import("../dock/widgets/JobsWidget.vue")),
  canvas: lazy(() => import("../dock/widgets/CanvasMiniWidget.vue")),
  scratchpad: lazy(() => import("../dock/widgets/ScratchpadWidget.vue")),
  plugins: lazy(() => import("../dock/widgets/PluginsWidget.vue")),
  search: lazy(() => import("../dock/widgets/SearchWidget.vue")),
  usage: lazy(() => import("../dock/widgets/UsageWidget.vue")),
}

/** Side view contents beside the chat column. `widget` hosts a pinned widget. */
export const SIDES = {
  canvas: lazy(() => import("../side/CanvasSide.vue")),
  subagent: lazy(() => import("../side/SubagentSide.vue")),
  peek: lazy(() => import("../side/ConversationPeekSide.vue")),
  drive: lazy(() => import("../side/DriveDetailSide.vue")),
  terminal: lazy(() => import("../side/TerminalSide.vue")),
  graph: lazy(() => import("../side/GraphSide.vue")),
}

/** Whole-page session tabs other than Chat. */
export const TABS = {
  graph: lazy(() => import("../graph/GraphTab.vue")),
  status: lazy(() => import("../status/StatusTab.vue")),
  workspace: lazy(() => import("../workspace/WorkspaceTab.vue")),
  debug: lazy(() => import("../debug/DebugTab.vue")),
  settings: lazy(() => import("../settings/SettingsTab.vue")),
}

export const WIDGET_IDS = Object.keys(WIDGETS)
export const SIDE_KINDS = Object.keys(SIDES)
export const TAB_IDS = Object.keys(TABS)
