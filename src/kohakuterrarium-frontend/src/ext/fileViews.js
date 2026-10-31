/**
 * File views: package components that show a workspace file in place of the
 * code editor, chosen by file name pattern.
 *
 * A pattern is ``*.ext`` (matched against the end of the base name, case
 * insensitive, so ``*.tar.gz`` works) or an exact base name. A view is only a
 * read-only presentation of the buffer; editing stays in the code editor.
 */

import { markRaw, reactive } from "vue"

export const fileViews = reactive(new Map())

export function registerFileView({ key, pkg, id, label, icon, order, component, options }) {
  fileViews.set(key, {
    key,
    pkg,
    id,
    label: label || id,
    icon: icon || "i-carbon-view",
    order: Number.isFinite(order) ? order : 100,
    component: markRaw(component),
    patterns: markRaw([...(options?.patterns || [])]),
    defaultView: options?.defaultView === true,
  })
}

export function clearFileViews() {
  fileViews.clear()
}

function baseName(path) {
  return String(path || "")
    .split(/[\\/]/)
    .pop()
}

/** Whether ``name`` (a base name) matches ``pattern`` (`*.ext` or an exact name). */
export function matchesPattern(pattern, name) {
  const lower = String(name).toLowerCase()
  const want = String(pattern).toLowerCase()
  if (want.startsWith("*.")) return lower.length > want.length - 1 && lower.endsWith(want.slice(1))
  return lower === want
}

/** Views that can show ``path``, in display order. */
export function fileViewsFor(path) {
  const name = baseName(path)
  if (!name) return []
  return [...fileViews.values()]
    .filter((view) => view.patterns.some((pattern) => matchesPattern(pattern, name)))
    .sort((a, b) => a.order - b.order)
}

/** The view a file opens in without the user choosing: the first one marked default. */
export function defaultFileView(path) {
  return fileViewsFor(path).find((view) => view.defaultView) || null
}
