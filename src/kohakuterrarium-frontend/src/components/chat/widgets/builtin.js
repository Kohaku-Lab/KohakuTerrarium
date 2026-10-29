/**
 * Built-in widget renderers that ship with the dashboard.
 *
 * ``ensureBuiltinWidgetRenderers`` is idempotent; the chat store calls it
 * before the first ``client_hello`` and ``WidgetBlock`` before the first
 * lookup, so both the dashboard and the shared chat package see them.
 */

import { registerWidgetRenderer } from "../../../ext/widgets.js"
import AppFrame from "./app/AppFrame.vue"
import AskTextRenderer from "./legacy/AskTextRenderer.vue"
import CardRenderer from "./legacy/CardRenderer.vue"
import ConfirmRenderer from "./legacy/ConfirmRenderer.vue"
import { LEGACY_CHROME } from "./legacy/legacyShared.js"
import NotificationRenderer from "./legacy/NotificationRenderer.vue"
import ProgressRenderer from "./legacy/ProgressRenderer.vue"
import SelectionRenderer from "./legacy/SelectionRenderer.vue"

export const BUILTIN_KEY = "builtin"

const LEGACY_COMPONENTS = {
  "kt.card": CardRenderer,
  "kt.ask_text": AskTextRenderer,
  "kt.confirm": ConfirmRenderer,
  "kt.selection": SelectionRenderer,
  "kt.progress": ProgressRenderer,
  "kt.notification": NotificationRenderer,
}

let registered = false

export function ensureBuiltinWidgetRenderers() {
  if (registered) return
  registered = true
  for (const [id, component] of Object.entries(LEGACY_COMPONENTS)) {
    registerWidgetRenderer(id, component, {
      key: BUILTIN_KEY,
      trust: "builtin",
      surfaces: ["chat"],
      ...LEGACY_CHROME[id],
    })
  }
  registerWidgetRenderer("kt.app", AppFrame, {
    key: BUILTIN_KEY,
    trust: "sandboxed",
    version: 1,
    surfaces: ["chat", "canvas"],
    summary: (widget) => widget.title || "App",
    icon: () => "i-carbon-application-web text-warm-500",
  })
}

/** Forget the one-time registration flag (tests that clear the registry). */
export function resetBuiltinWidgetRenderers() {
  registered = false
}
