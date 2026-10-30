/**
 * Desktop host bridge: the one place the frontend talks to its native shell.
 *
 * Two shells embed this page: pywebview (`kt app`) exposes `window.pywebview.api`
 * and Tauri (the installed app) exposes `window.__TAURI_INTERNALS__.invoke`. Both
 * implement the same operations under the same snake_case names. A plain browser
 * has neither, and every operation then reports "unavailable" instead of throwing.
 * Callers test capabilities, never the shell kind.
 */

export const DESKTOP_PROTOCOL = 1

const KIND_PYWEBVIEW = "pywebview"
const KIND_TAURI = "tauri"

/** The embedding shell, or ``null`` in a plain browser. */
export function hostKind() {
  if (typeof window === "undefined") return null
  if (window.pywebview?.api) return KIND_PYWEBVIEW
  if (typeof window.__TAURI_INTERNALS__?.invoke === "function") return KIND_TAURI
  return null
}

/** Whether a native shell is hosting this page. */
export function hasDesktopHost() {
  return hostKind() !== null
}

function call(name, ...args) {
  switch (hostKind()) {
    case KIND_PYWEBVIEW: {
      const fn = window.pywebview.api[name]
      return typeof fn === "function" ? fn(...args) : undefined
    }
    case KIND_TAURI:
      return window.__TAURI_INTERNALS__.invoke(name, args.length ? { args } : {})
    default:
      return undefined
  }
}

/** Whether the shell implements `name`. Tauri commands are assumed present. */
export function hostSupports(name) {
  switch (hostKind()) {
    case KIND_PYWEBVIEW:
      return typeof window.pywebview.api[name] === "function"
    case KIND_TAURI:
      return true
    default:
      return false
  }
}

/** Shell capabilities for this protocol version, or ``null`` when unavailable. */
export async function desktopCapabilities() {
  if (!hostSupports("get_desktop_capabilities")) return null
  try {
    const capabilities = await call("get_desktop_capabilities")
    if (capabilities?.surface === "desktop" && capabilities.protocol === DESKTOP_PROTOCOL) {
      return capabilities
    }
  } catch {
    // A shell that cannot answer is treated as having no native features.
  }
  return null
}

/** Ask the shell to flash or bounce its window. Resolves ``false`` when it cannot. */
export async function requestDesktopAttention() {
  if (!hostSupports("request_desktop_attention")) return false
  try {
    return (await call("request_desktop_attention")) === true
  } catch {
    return false
  }
}

/** Open `url` in the system browser through the shell. Resolves ``false`` when it cannot. */
export async function openExternalViaHost(url) {
  if (!hostSupports("open_external")) return false
  try {
    return (await call("open_external", String(url))) === true
  } catch {
    return false
  }
}

/** Show a system notification through the shell. Resolves ``false`` when it cannot. */
export async function showDesktopNotification(title, body) {
  if (!hostSupports("show_notification")) return false
  try {
    return (await call("show_notification", String(title), String(body))) === true
  } catch {
    return false
  }
}

/** Whether the shell starts the app at login, or ``null`` when it cannot say. */
export async function getAutostart() {
  if (!hostSupports("get_autostart")) return null
  try {
    return (await call("get_autostart")) === true
  } catch {
    return null
  }
}

/** Ask the shell to start (or stop starting) the app at login. Resolves the new state. */
export async function setAutostart(enabled) {
  if (!hostSupports("set_autostart")) return null
  try {
    const applied = await call("set_autostart", Boolean(enabled))
    return applied === true ? Boolean(enabled) : !enabled
  } catch {
    return null
  }
}

/** Whether a newer shell build is published: `{ available, version? }`, or ``null`` if the shell cannot check. */
export async function checkShellUpdate() {
  if (!hostSupports("check_shell_update")) return null
  try {
    return (await call("check_shell_update")) || null
  } catch {
    return null
  }
}

/** Download and install the newer shell build and restart into it. Resolves ``false`` on failure. */
export async function installShellUpdate() {
  if (!hostSupports("install_shell_update")) return false
  try {
    return (await call("install_shell_update")) === true
  } catch {
    return false
  }
}

/** The deep link that arrived before the page could ask for it, or ``null``. */
export async function takeDeepLink() {
  if (!hostSupports("take_deep_link")) return null
  try {
    return (await call("take_deep_link")) || null
  } catch {
    return null
  }
}

/**
 * Run `listener` once a shell is available: immediately when one already is,
 * otherwise when pywebview announces itself. Returns an unsubscribe function.
 */
export function onHostReady(listener) {
  if (typeof window === "undefined") return () => {}
  if (hasDesktopHost()) listener()
  window.addEventListener("pywebviewready", listener)
  return () => window.removeEventListener("pywebviewready", listener)
}
