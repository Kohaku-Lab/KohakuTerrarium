// Host-neutral link-opener seam shared by the Dashboard and the VS Code webview.
//
// A host installs one opener via ``providePlatformLinkOpener``; the shared link
// leaves (card actions and Markdown links) call it on a USER CLICK for any
// target the platform itself must resolve — a relative reference or an absolute
// http(s) URL. The Dashboard installs none, so its anchors keep the browser's
// native navigation (relative links resolve against ``window.location.origin``,
// ``#hash`` links stay in-document, ``mailto:``/``tel:`` stay OS-handled).
//
// The VS Code webview installs an opener that forwards the raw model-authored
// reference to the Host's narrow ``platform.openLink`` operation, which resolves
// it against the live backend URL and calls ``vscode.env.openExternal``. The
// opener is invoked only from a user gesture — nothing here auto-opens a link —
// and it receives the reference, never the Host token.
import { inject, provide } from "vue"

export const PLATFORM_LINK_OPENER_KEY = "ktPlatformLinkOpener"

export function providePlatformLinkOpener(opener) {
  provide(PLATFORM_LINK_OPENER_KEY, opener)
}

// ``undefined`` (no provider) is distinct from an installed opener: the shared
// leaves only fall back to browser navigation when no host installed one.
export function usePlatformLinkOpener() {
  return inject(PLATFORM_LINK_OPENER_KEY, undefined)
}
