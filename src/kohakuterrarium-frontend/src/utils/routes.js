/**
 * Desktop ↔ mobile route mapping.
 *
 * A single table keyed by desktop route. Each entry has a matcher
 * (static string or regex with a single capture group) and a
 * translator to the mobile equivalent, plus the inverse.
 *
 * Adding a new page means adding ONE entry here, instead of editing
 * `toMobileRoute` and `toDesktopRoute` in App.vue and hoping the two
 * stay in sync.
 */

const STATIC = [
  { desktop: "/", mobile: "/mobile" },
  { desktop: "/new", mobile: "/mobile/new" },
  { desktop: "/sessions", mobile: "/mobile/sessions" },
  { desktop: "/settings", mobile: "/mobile/settings" },
  { desktop: "/registry", mobile: "/mobile/registry" },
]

const DYNAMIC = [
  {
    desktop: /^\/instances\/(.+)$/,
    mobile: /^\/mobile\/(?!(?:new|sessions|settings|registry)(?:\/|$))(.+)$/,
    toMobile: (id) => `/mobile/${id}`,
    toDesktop: (id) => `/instances/${id}`,
  },
  {
    desktop: /^\/sessions\/(.+)$/,
    mobile: /^\/mobile\/sessions\/(.+)$/,
    toMobile: (name) => `/mobile/sessions/${name}`,
    toDesktop: (name) => `/sessions/${name}`,
  },
]

export function toMobileRoute(path) {
  for (const entry of STATIC) {
    if (entry.desktop === path) return entry.mobile
  }
  for (const entry of DYNAMIC) {
    const m = path.match(entry.desktop)
    if (m) return entry.toMobile(m[1])
  }
  return null
}

export function toDesktopRoute(path) {
  for (const entry of STATIC) {
    if (entry.mobile === path) return entry.desktop
  }
  for (const entry of DYNAMIC) {
    const m = path.match(entry.mobile)
    if (m) return entry.toDesktop(m[1])
  }
  return "/"
}
