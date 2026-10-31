/**
 * Extension commands: palette entries and in-app key shortcuts.
 *
 * A package declares a ``command`` contribution; its entry registers the
 * handler with ``kt.registerCommand``. The palette entry exists before the
 * package loads, and running it (or pressing its shortcut) loads the package
 * first.
 */

import { ElMessage } from "element-plus"

import { contributionComponents, runtime } from "@/ext/state"
import { usePaletteStore } from "@/stores/palette"

const MODIFIER_ORDER = ["Ctrl", "Alt", "Shift"]
export const RESERVED_SHORTCUTS = new Set([
  "Ctrl+K",
  "Ctrl+Shift+L",
  "Ctrl+Shift+0",
  "Ctrl+1",
  "Ctrl+2",
  "Ctrl+3",
  "Ctrl+4",
  "Ctrl+5",
  "Ctrl+6",
])

/** key -> contribution key, for every registered shortcut. */
export const commandShortcuts = new Map()

/** Canonical form of a declared shortcut: Meta folds into Ctrl, modifiers are ordered. */
export function normalizeShortcut(text) {
  const parts = String(text || "")
    .split("+")
    .map((p) => p.trim())
    .filter(Boolean)
  const key = parts.pop()
  if (!key) return ""
  const mods = new Set(parts.map((p) => (p === "Meta" ? "Ctrl" : p)))
  const ordered = MODIFIER_ORDER.filter((m) => mods.has(m))
  return [...ordered, key.length === 1 ? key.toUpperCase() : key].join("+")
}

/** Canonical shortcut of a keyboard event, or ``""`` for a bare modifier press. */
export function shortcutFromEvent(event) {
  const code = String(event.code || "")
  let key = event.key
  if (code.startsWith("Key")) key = code.slice(3)
  else if (code.startsWith("Digit")) key = code.slice(5)
  if (!key || ["Control", "Alt", "Shift", "Meta"].includes(key)) return ""
  const mods = []
  if (event.ctrlKey || event.metaKey) mods.push("Ctrl")
  if (event.altKey) mods.push("Alt")
  if (event.shiftKey) mods.push("Shift")
  return [...mods, key.length === 1 ? key.toUpperCase() : key].join("+")
}

/** Run an extension command, loading its package first; failures show a toast. */
export async function runExtensionCommand(pkg, key, label = "") {
  try {
    await runtime.ensureLoaded(pkg)
    const handler = contributionComponents.get(key)
    if (typeof handler !== "function") {
      throw new Error(`${pkg} did not register a handler for ${label || key}`)
    }
    await handler()
  } catch (err) {
    ElMessage({ message: `${label || key}: ${err?.message || err}`, type: "error" })
  }
}

/** Add the palette entry and shortcut of one declared command. */
export function registerCommand(pkg, contribution, key) {
  const shortcut = normalizeShortcut(contribution.shortcut)
  const taken = shortcut && (RESERVED_SHORTCUTS.has(shortcut) || commandShortcuts.has(shortcut))
  if (taken) {
    console.warn(`[ui-extensions] ${pkg}: shortcut ${contribution.shortcut} is already in use`)
  } else if (shortcut) {
    commandShortcuts.set(shortcut, { key, pkg, label: contribution.label })
  }
  usePaletteStore().register({
    id: key,
    label: contribution.label,
    icon: contribution.icon || "i-carbon-terminal",
    keywords: contribution.keywords || contribution.description || "",
    shortcut: taken ? "" : contribution.shortcut || "",
    handler: () => runExtensionCommand(pkg, key, contribution.label),
  })
}

export function unregisterCommands(keys) {
  const palette = usePaletteStore()
  const gone = new Set(keys)
  for (const key of gone) palette.unregister(key)
  for (const [shortcut, entry] of commandShortcuts) {
    if (gone.has(entry.key)) commandShortcuts.delete(shortcut)
  }
}

let _installed = false

/** Listen for extension shortcuts on the window. Idempotent. */
export function installCommandShortcuts() {
  if (_installed || typeof window === "undefined") return
  _installed = true
  window.addEventListener("keydown", (event) => {
    if (event.defaultPrevented || event.repeat) return
    const entry = commandShortcuts.get(shortcutFromEvent(event))
    if (!entry) return
    event.preventDefault()
    runExtensionCommand(entry.pkg, entry.key, entry.label)
  })
}
