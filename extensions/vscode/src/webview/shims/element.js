// Element Plus integration for the VS Code webview build.
//
// The shared production UIEventBlock renders real Element Plus widgets
// (buttons, inputs, checkbox/radio groups, progress, links), so this module
// re-exports the REAL package instead of hand-rolling native replacements, and
// pulls in the production component stylesheet so the widgets are styled inside
// the webview (its bundled CSS is served under the webview CSP, never a CDN).
// The only host-specific override is ``ElMessage``: the chat store fires it for
// toast notifications, and the webview surfaces those through its native
// notification region (``notifications.mjs``) rather than silently no-op'ing.
import 'element-plus/dist/index.css'

import * as RealElementPlus from 'element-plus/es/index.mjs'

import { showNotification } from '../notifications.mjs'

export * from 'element-plus/es/index.mjs'

export const ElMessage = (options) => showNotification(options)
for (const type of ['info', 'success', 'warning', 'error']) {
  ElMessage[type] = (options) => showNotification({ ...(typeof options === 'string' ? { message: options } : options), type })
}

// Named so a consumer that genuinely needs the real toast (with its own
// styling/stacking) can reach it; the export above stays host-native.
export const RealElMessage = RealElementPlus.ElMessage
