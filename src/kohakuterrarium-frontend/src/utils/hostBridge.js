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

/** Whether closing the window keeps the app running in the tray, or ``null`` when the shell has no tray. */
export async function getCloseToTray() {
  if (!hostSupports("get_close_to_tray")) return null
  try {
    return (await call("get_close_to_tray")) === true
  } catch {
    return null
  }
}

/** Keep the app running in the tray when its window is closed. Resolves the state afterwards. */
export async function setCloseToTray(enabled) {
  if (!hostSupports("set_close_to_tray")) return null
  try {
    const applied = await call("set_close_to_tray", Boolean(enabled))
    return applied === true ? Boolean(enabled) : !enabled
  } catch {
    return null
  }
}

/** The system-wide shortcut that raises the app, or ``null`` when none is set or the shell has none. */
export async function getSummonShortcut() {
  if (!hostSupports("get_summon_shortcut")) return null
  try {
    return (await call("get_summon_shortcut"))?.shortcut || null
  } catch {
    return null
  }
}

/** Set (an accelerator string) or clear (``null``) the system-wide summon shortcut. Resolves ``false`` if it cannot be used. */
export function setSummonShortcut(shortcut) {
  return callBoolean("set_summon_shortcut", shortcut ?? null)
}

/** Show how many sessions are `waiting`, `failed`, `running` and `finished` on the tray icon and taskbar entry. */
export function setAttentionState({ waiting = 0, failed = 0, running = 0, finished = 0 } = {}) {
  const count = (value) => Math.max(0, value | 0)
  return callBoolean("set_attention_state", {
    waiting: count(waiting),
    failed: count(failed),
    running: count(running),
    finished: count(finished),
  })
}

/** Show these sessions in the tray menu: `{ waiting: [{ id, label }], sessions: [{ id, label }] }`. */
export function setTraySessions({ waiting = [], sessions = [] } = {}) {
  const entries = (list) => list.map(({ id, label }) => ({ id: String(id), label: String(label) }))
  return callBoolean("set_tray_sessions", {
    waiting: entries(waiting),
    sessions: entries(sessions),
  })
}

/** Replace the extension items in the tray menu with `[{ id, label }]`. Resolves ``false`` if the shell has no tray menu. */
export function setTrayItems(items) {
  return callBoolean(
    "set_tray_items",
    (items || []).map(({ id, label }) => ({ id: String(id), label: String(label) })),
  )
}

/** Every shell setting with its current value, or ``null`` when the shell keeps none. */
export async function getShellSettings() {
  if (hostKind() !== KIND_TAURI || !hostSupports("get_shell_settings")) return null
  try {
    return (await call("get_shell_settings")) || null
  } catch {
    return null
  }
}

/** Apply a `{ key: value }` patch all-or-nothing. Resolves the full settings, or ``null`` when the shell refuses it. */
export async function patchShellSettings(patch) {
  if (hostKind() !== KIND_TAURI || !hostSupports("patch_shell_settings")) return null
  try {
    const result = await call("patch_shell_settings", patch)
    return result?.ok === true ? result.settings : null
  } catch {
    return null
  }
}

/**
 * How the backend process is doing: `{ state, port, pid, uptimeSec, restarts, memoryBytes, processes }`,
 * or ``null`` when the shell does not manage a backend.
 */
export async function getBackendStatus() {
  if (hostKind() !== KIND_TAURI || !hostSupports("get_backend_status")) return null
  try {
    return (await call("get_backend_status")) || null
  } catch {
    return null
  }
}

/** Restart the backend process and reload the app windows. Resolves ``false`` if a restart is already running. */
export function restartBackend() {
  return callBoolean("restart_backend")
}

/** Show the backend log in the file manager. */
export function openLogs() {
  return callBoolean("open_logs")
}

/** Reinstall the release shipped with the shell while the backend is stopped. Resolves ``false`` if a restart is already running. */
export function repairRuntime() {
  return callBoolean("repair_runtime")
}

/**
 * Save a diagnostics bundle (versions, logs, settings; credentials removed). Without
 * ``path`` the shell asks where. Resolves `{ ok, path?, files?, cancelled?, error? }`.
 */
export async function exportDiagnostics(path) {
  if (hostKind() !== KIND_TAURI || !hostSupports("export_diagnostics")) {
    return { ok: false, error: "unsupported" }
  }
  try {
    return (
      (path ? await call("export_diagnostics", path) : await call("export_diagnostics")) || {
        ok: false,
      }
    )
  } catch (error) {
    return { ok: false, error: String(error?.message || error) }
  }
}

/** Whether the shell can show the operating system's own file and folder dialogs. */
export function nativeDialogsSupported() {
  return hostKind() === KIND_TAURI && hostSupports("pick_folder")
}

/**
 * Ask for a folder in the OS dialog: `{ title, defaultPath }`. Resolves the path,
 * ``null`` when the user cancelled, or ``undefined`` when the shell has no dialog.
 */
export async function pickFolderNative(options = {}) {
  if (!nativeDialogsSupported()) return undefined
  try {
    return (await call("pick_folder", options)) || null
  } catch {
    return undefined
  }
}

/** Ask for files: `{ title, defaultPath, filters }`. Resolves paths (empty on cancel) or ``undefined`` without a dialog. */
export async function pickFilesNative(options = {}) {
  if (!nativeDialogsSupported()) return undefined
  try {
    const paths = await call("pick_files", options)
    return Array.isArray(paths) ? paths : []
  } catch {
    return undefined
  }
}

/** Ask where to save: `{ title, defaultPath, fileName, filters }`. Resolves a path, ``null`` on cancel, ``undefined`` without a dialog. */
export async function saveFileNative(options = {}) {
  if (!nativeDialogsSupported()) return undefined
  try {
    return (await call("save_file", options)) || null
  } catch {
    return undefined
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

/** Whether the shell can draw an extension overlay in its own native window. */
export function overlayWindowsSupported() {
  return hostKind() === KIND_TAURI && hostSupports("open_overlay_window")
}

async function callBoolean(name, ...args) {
  if (!hostSupports(name)) return false
  try {
    return (await call(name, ...args)) === true
  } catch {
    return false
  }
}

/** Open (or show) a native overlay window: `{ label, key, width, height, anchor, alwaysOnTop }`. */
export function openOverlayWindow(spec) {
  return callBoolean("open_overlay_window", spec)
}

/** Whether the shell can show a session in a window of its own. */
export function popoutWindowsSupported() {
  return hostKind() === KIND_TAURI && hostSupports("open_popout_window")
}

/** Close the calling session window; with ``showInMain`` the main window first shows that session. */
export function returnPopoutWindow(showInMain = false) {
  return callBoolean("popout_return", Boolean(showInMain))
}

/** Open (or raise) a native window for one session: `{ label, scope, title, x?, y? }`. */
export function openPopoutWindow(spec) {
  return callBoolean("open_popout_window", spec)
}

/** Tell the shell the session window's page is running and could call a native command. */
export function ackPopoutWindow() {
  return callBoolean("popout_ack")
}

/** Close the native overlay window with `label`. */
export function closeOverlayWindow(label) {
  return callBoolean("close_overlay_window", String(label))
}

/** Start dragging the calling overlay window. */
export function startOverlayDrag() {
  return callBoolean("overlay_start_drag")
}

/** Move and resize the calling overlay window (logical screen pixels). */
export function setOverlayBounds(x, y, width, height) {
  return callBoolean("overlay_set_bounds", x, y, width, height)
}

/** Report the `[x, y, width, height]` rectangles of the calling overlay that catch the mouse. */
export function setOverlayHitRegions(regions) {
  return callBoolean("overlay_set_hit_regions", regions)
}

/** Tell the shell the overlay page is running and could call a native command. */
export function ackOverlayWindow() {
  return callBoolean("overlay_ack")
}

/**
 * Files and folders the app was launched with or handed while running and the
 * page has not taken yet: ``[{ kind: "session" | "workspace", path }]``.
 */
export async function takePendingOpens() {
  if (!hostSupports("take_pending_opens")) return []
  try {
    const opens = await call("take_pending_opens")
    return Array.isArray(opens) ? opens : []
  } catch {
    return []
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
