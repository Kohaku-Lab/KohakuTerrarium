/**
 * Extension actions: buttons and menu entries a package adds to host surfaces.
 *
 * An ``action`` names the surfaces it appears on (``targets``) and, for
 * messages, the roles it applies to. The host passes a read-only context
 * describing the thing acted on; the handler decides what to do with it.
 * Actions never change stored content.
 */

import { ElMessage } from "element-plus"
import { markRaw, reactive } from "vue"

import { contributionComponents, runtime } from "@/ext/state"

export const ACTION_TARGETS = [
  "message",
  "composer",
  "tab",
  "rail-item",
  "file",
  "graph-node",
  "graph-edge",
]
export const DEFAULT_MESSAGE_ROLES = ["assistant"]

export const actions = reactive(new Map())

/** Register one declared action; ``options`` are the manifest's ``action`` block. */
export function registerAction({ key, pkg, id, label, icon, order, options }) {
  actions.set(key, {
    key,
    pkg,
    id,
    label: label || id,
    icon: icon || "i-carbon-flash",
    order: Number.isFinite(order) ? order : 100,
    targets: markRaw([...(options?.targets || [])]),
    roles: markRaw([...(options?.roles || DEFAULT_MESSAGE_ROLES)]),
  })
}

export function clearActions() {
  actions.clear()
}

/** Actions for ``target`` in display order; messages are filtered by ``context.role``. */
export function actionsFor(target, context = {}) {
  return [...actions.values()]
    .filter((a) => a.targets.includes(target))
    .filter((a) => target !== "message" || a.roles.includes(context.role))
    .sort((a, b) => a.order - b.order)
}

const MENU_PREFIX = "action:"

/** Menu rows (`{ id, label, icon }`) for the actions of ``target``; empty when none apply. */
export function actionMenuItems(target, context = {}) {
  return actionsFor(target, context).map((a) => ({
    id: `${MENU_PREFIX}${a.key}`,
    label: a.label,
    icon: a.icon,
  }))
}

/** Whether a menu row id belongs to an extension action. */
export function isActionMenuId(id) {
  return typeof id === "string" && id.startsWith(MENU_PREFIX)
}

/** Run the action a menu row stands for. */
export function runActionMenuId(id, context) {
  const action = actions.get(String(id).slice(MENU_PREFIX.length))
  return action ? runAction(action, context) : Promise.resolve()
}

/** Run an action's handler with ``context``, loading its package first; failures show a toast. */
export async function runAction(action, context) {
  try {
    await runtime.ensureLoaded(action.pkg)
    const handler = contributionComponents.get(action.key)
    if (typeof handler !== "function") {
      throw new Error(`${action.pkg} did not register a handler for ${action.label}`)
    }
    await handler(context)
  } catch (err) {
    ElMessage({ message: `${action.label}: ${err?.message || err}`, type: "error" })
  }
}
