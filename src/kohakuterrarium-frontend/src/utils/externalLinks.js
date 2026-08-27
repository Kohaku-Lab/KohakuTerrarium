/*
 * Keep external links from swallowing the app.
 *
 * In a browser tab an external link is survivable — there is a back
 * button. Inside the pywebview shell (`kt app`, the installed desktop
 * app) there is no chrome at all, so a same-window navigation to
 * github.com strands the user with no way back.
 *
 * pywebview's OPEN_EXTERNAL_LINKS_IN_BROWSER setting (on by default)
 * does not help on its own: every backend wires it to *new-window*
 * navigation only — cocoa's createWebViewWithConfiguration, WebView2's
 * NewWindowRequested, GTK's `frame_name == '_blank'` check, Qt's
 * NavigationHandler. A plain `<a href>` with no target is never
 * intercepted, and that is exactly what markdown-it emits for links and
 * for linkified bare URLs.
 *
 * So the fix is on this side, in two layers:
 *   1. `applyExternalLinkRule` marks rendered markdown links `_blank`,
 *      which hands them to the handlers above (and to a real new tab in
 *      a browser).
 *   2. `installExternalLinkGuard` catches any anchor that layer 1 does
 *      not own — raw v-html elsewhere, components written later.
 */

function currentOrigin() {
  if (typeof window === "undefined" || !window.location) return null
  return window.location.origin
}

/**
 * Whether `href` points at an http(s) resource on another origin.
 *
 * Relative paths, fragments, same-origin absolutes and non-web schemes
 * (mailto:, tel:, data:, javascript:) are all NOT external: they either
 * stay inside the SPA or are handed to the OS by the webview already.
 */
export function isExternalUrl(href, origin = currentOrigin()) {
  if (!href) return false
  let url
  try {
    url = new URL(href, origin || "http://localhost/")
  } catch {
    return false
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return false
  return origin === null || url.origin !== origin
}

/** Send `url` to a real browser tab (the system browser under pywebview). */
export function openExternal(url) {
  if (typeof window === "undefined") return
  window.open(url, "_blank", "noopener,noreferrer")
}

/**
 * Teach a markdown-it instance to render external links as `_blank`.
 *
 * Wraps whatever `link_open` rule is already installed rather than
 * replacing it, so plugin-supplied rules keep running.
 */
export function applyExternalLinkRule(md) {
  const fallback =
    md.renderer.rules.link_open ||
    ((tokens, idx, options, env, self) => self.renderToken(tokens, idx, options, env, self))
  md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
    const token = tokens[idx]
    if (isExternalUrl(token.attrGet("href"))) {
      token.attrSet("target", "_blank")
      token.attrSet("rel", "noopener noreferrer")
    }
    return fallback(tokens, idx, options, env, self)
  }
  return md
}

/**
 * Intercept clicks on untargeted external anchors, document-wide.
 *
 * Capture phase, so it wins over component handlers that would
 * otherwise let the navigation through. Anchors that already carry a
 * target are left alone — their default behaviour is already correct
 * everywhere, and re-opening them here would only add a second code
 * path to keep working.
 *
 * Returns the uninstall function.
 */
export function installExternalLinkGuard(root = typeof document === "undefined" ? null : document) {
  if (!root) return () => {}

  const onClick = (event) => {
    if (event.defaultPrevented) return
    // Middle-click / ctrl-click / cmd-click already mean "new tab".
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)
      return
    const anchor = event.target?.closest?.("a[href]")
    if (!anchor) return
    const target = anchor.getAttribute("target")
    if (target && target !== "_self") return
    const href = anchor.getAttribute("href")
    if (!isExternalUrl(href)) return
    event.preventDefault()
    openExternal(new URL(href, window.location.href).href)
  }

  root.addEventListener("click", onClick, true)
  return () => root.removeEventListener("click", onClick, true)
}
