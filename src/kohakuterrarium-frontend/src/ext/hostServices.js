/**
 * Native-aware services offered to extensions: notifications, opening links,
 * host identity and a small per-package key-value store. Each works in a plain
 * browser and uses the desktop shell's native version when there is one.
 */

import { ElMessage } from "element-plus"

import { hostKind, openExternalViaHost, showDesktopNotification } from "@/utils/hostBridge"

const OPENABLE_SCHEMES = ["http:", "https:", "mailto:"]
const STORAGE_PREFIX = "kt.ext."

/** ``"native"`` when the shell showed a system notification, else ``"toast"`` after an in-app message. */
export async function notifyDesktop(title, body = "") {
  if (await showDesktopNotification(String(title), String(body))) return "native"
  ElMessage({ message: body ? `${title}: ${body}` : String(title), type: "info" })
  return "toast"
}

/** Open an http(s) or mailto link outside the app; ``false`` for anything else. */
export async function openExternal(url) {
  let parsed
  try {
    parsed = new URL(String(url))
  } catch {
    return false
  }
  if (!OPENABLE_SCHEMES.includes(parsed.protocol)) return false
  if (await openExternalViaHost(parsed.href)) return true
  if (typeof window === "undefined") return false
  return window.open(parsed.href, "_blank", "noopener,noreferrer") !== null
}

/** Which shell hosts the page: ``"tauri"``, ``"pywebview"`` or ``"browser"``. */
export function hostInfo() {
  return { kind: hostKind() || "browser" }
}

/** JSON key-value store scoped to one package; it falls back to memory when storage is blocked. */
export function createPackageStorage(pkg) {
  const memory = new Map()
  const name = (key) => `${STORAGE_PREFIX}${pkg}.${key}`
  return {
    get(key, fallback = null) {
      try {
        const raw = localStorage.getItem(name(key))
        return raw === null ? fallback : JSON.parse(raw)
      } catch {
        return memory.has(key) ? memory.get(key) : fallback
      }
    },
    set(key, value) {
      memory.set(key, value)
      try {
        localStorage.setItem(name(key), JSON.stringify(value))
      } catch {
        // Blocked or full storage keeps the value for this page only.
      }
    },
    remove(key) {
      memory.delete(key)
      try {
        localStorage.removeItem(name(key))
      } catch {
        // Nothing stored to remove.
      }
    },
  }
}
