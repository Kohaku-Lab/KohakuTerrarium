// Host-neutral platform-origin seam shared by the Dashboard and the VS Code
// webview. A host installs the origin its same-origin backend URLs resolve
// against via ``providePlatformOrigin``; shared components consume it through
// ``usePlatformOrigin`` without reaching for any host global.
//
// The Dashboard leaves it uninstalled, so the shared Markdown link rule keeps
// the browser's ``window.location.origin`` (same-origin links stay in-app). The
// VS Code webview installs ``null`` explicitly: its ``window.location.origin``
// is the opaque ``vscode-webview://`` document, which must never be mistaken for
// the backend origin, so card links are treated as external instead of being
// silently rewritten against a fake same-origin.
import { inject, provide } from "vue"

export const PLATFORM_ORIGIN_KEY = "ktPlatformOrigin"

export function providePlatformOrigin(origin) {
  provide(PLATFORM_ORIGIN_KEY, origin)
}

// ``undefined`` (no provider) is distinct from an explicit ``null``: the shared
// consumer only falls back to the browser origin when no host installed one.
export function usePlatformOrigin() {
  return inject(PLATFORM_ORIGIN_KEY, undefined)
}
