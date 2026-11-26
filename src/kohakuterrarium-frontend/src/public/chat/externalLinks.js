/** Whether `href` points at an http(s) resource outside `origin`. */
export function isExternalUrl(href, origin = null) {
  if (!href) return false
  let url
  try {
    url = new URL(href, origin || "http://localhost/")
  } catch {
    return false
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return false
  if (!origin && !/^(?:https?:)?\/\//i.test(href)) return false
  return origin === null || url.origin !== origin
}

/** Path prefixes the backend serves itself; every other same-origin path is a page of the app. */
export const BACKEND_PATH_PREFIXES = Object.freeze(["/api/", "/ws/"])

const IMAGE_EXTENSION = /\.(?:apng|avif|bmp|gif|ico|jpe?g|png|svg|webp)$/i

/**
 * Whether `href` is a backend file or endpoint (for example an artifact under
 * `/api/`) on `origin` rather than a page of the app. With no `origin` only a
 * root-relative path can qualify.
 */
export function isBackendResourceUrl(href, origin = null) {
  if (typeof href !== "string" || !href.trim()) return false
  const raw = href.trim()
  if (!origin && (HAS_SCHEME.test(raw) || raw.startsWith("//") || !raw.startsWith("/")))
    return false
  let url
  try {
    url = new URL(raw, origin ? `${origin}/` : "http://localhost/")
  } catch {
    return false
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return false
  if (origin && url.origin !== origin) return false
  return BACKEND_PATH_PREFIXES.some((prefix) => url.pathname.startsWith(prefix))
}

/** Whether the path of `href` names an image file by its extension. */
export function isImageUrl(href) {
  if (typeof href !== "string") return false
  let path
  try {
    path = new URL(href, "http://localhost/").pathname
  } catch {
    return false
  }
  return IMAGE_EXTENSION.test(path)
}

// Any explicit scheme (including `javascript:`, `data:`, `mailto:`, protocol
// relative `//host`). A href with a scheme is never a bare relative path.
const HAS_SCHEME = /^[a-z][a-z0-9+.-]*:/i
// Only these schemes may become live navigations; everything else (script,
// data, file, blob, custom handlers) is dropped rather than rendered.
const NAVIGABLE_SCHEME = /^https?:$/i
// OS-handled schemes the browser already owns. They must never gain a new
// restriction from a host link-guard.
const OS_SCHEME = /^(?:mailto|tel):/i

/**
 * Host-neutral resolver for a model-authored link target (a card Markdown link
 * or a UI-event link action). It is the single place both hosts turn a target
 * into a renderable href, so the Dashboard and the VS Code webview agree on the
 * safety rules:
 *
 * - `javascript:`/`data:`/`file:`/unknown schemes are never rendered (XSS).
 * - `#anchor` and `mailto:`/`tel:` stay in-page / OS-handled, unchanged.
 * - an absolute http(s) target is rendered as-is (external unless same-origin).
 * - a protocol-relative `//host/path` needs an origin to pick a scheme.
 * - a relative path resolves against the host's explicit `origin`.
 *
 * `origin` is the platform origin the host installed (browser origin, an
 * explicit backend origin, or `null`). When it is unknown (`null`) a relative
 * path cannot be resolved *locally*: the result is
 * ``{ href: null, unavailable: true }``. That is only the genuine no-origin
 * case — a host that owns a backend URL installs a platform link opener (see
 * ``platformLink.js``) and the shared leaves route the reference there instead.
 *
 * Never returns the host token or a credentialed service URL: it only ever
 * echoes a target the model already wrote and the explicit public origin.
 */
export function resolvePlatformLink(value, origin = null) {
  if (typeof value !== "string") return { href: null }
  const raw = value.trim()
  if (!raw) return { href: null }

  if (raw.startsWith("#")) return { href: raw, external: false }

  if (HAS_SCHEME.test(raw)) {
    if (!NAVIGABLE_SCHEME.test(raw.split(":", 1)[0] + ":")) {
      // mailto:/tel: are OS-handled and kept; script/data/file are dropped.
      return OS_SCHEME.test(raw) ? { href: raw, external: false } : { href: null }
    }
    let url
    try {
      url = new URL(raw)
    } catch {
      return { href: null }
    }
    return { href: url.href, external: origin == null || url.origin !== origin }
  }

  // Protocol-relative: the scheme comes from the origin when we have one.
  const base = origin ? `${origin}/` : null
  if (!origin && raw.startsWith("//")) return { href: null, unavailable: true }

  let url
  try {
    url = new URL(raw, base || "http://localhost/")
  } catch {
    return { href: null }
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return { href: null }
  // A relative path with no origin cannot be resolved to the host's backend.
  if (!origin) return { href: null, unavailable: true }
  return { href: url.href, external: url.origin !== origin }
}

/**
 * Whether a target must be handed to the host's platform opener rather than the
 * page itself. Mirrors the existing browser external-link rule: an absolute
 * http(s) URL and a relative reference are opened by the platform; an in-page
 * `#hash` and the OS-owned `mailto:`/`tel:` schemes keep their default handling,
 * and an unsafe scheme is never opened. Only consulted when an opener is
 * installed, so the browser host is completely unaffected.
 */
export function shouldOpenThroughHost(value) {
  if (typeof value !== "string") return false
  const raw = value.trim()
  if (!raw || raw.startsWith("#")) return false
  if (OS_SCHEME.test(raw)) return false
  if (HAS_SCHEME.test(raw)) return NAVIGABLE_SCHEME.test(raw.split(":", 1)[0] + ":")
  return true
}

/**
 * Teach a markdown-it instance to render external links and backend file links
 * as `_blank`, so neither replaces the app in its own tab.
 */
export function applyExternalLinkRule(md, origin = null) {
  const fallback =
    md.renderer.rules.link_open ||
    ((tokens, idx, options, env, self) => self.renderToken(tokens, idx, options, env, self))
  md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
    const token = tokens[idx]
    const href = token.attrGet("href")
    if (isExternalUrl(href, origin) || isBackendResourceUrl(href, origin)) {
      token.attrSet("target", "_blank")
      token.attrSet("rel", "noopener noreferrer")
    }
    return fallback(tokens, idx, options, env, self)
  }
  return md
}
