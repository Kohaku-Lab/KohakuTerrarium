// Alias boundary for the VS Code webview.
//
// The shared components import ``@/composables/useVisibilityInterval``; the
// webview build maps that specifier here (vite.config.mjs). Rather than keep a
// second copy of the poller — which previously dropped the in-flight skip and
// let a slow backend stack overlapping 1.5s transcript polls — this re-exports
// the exact production pure helper the Dashboard uses. Keeping one
// implementation is the only way the two hosts cannot drift.
export { createVisibilityInterval } from '../../../../../src/kohakuterrarium-frontend/src/composables/visibilityInterval.js'
