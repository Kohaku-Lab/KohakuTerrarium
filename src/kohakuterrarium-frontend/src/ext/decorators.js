/**
 * Message decorators: package UI drawn beside a message, never inside it.
 *
 * A decorator names a position (``header`` or ``footer``) and the message
 * roles it applies to. The component reads a read-only description of the
 * message and draws extra display; it cannot change the message. The host
 * caps the height of each decorator and how many one package adds to a message.
 */

import { markRaw, reactive } from "vue"

export const DECORATOR_POSITIONS = ["header", "footer"]
export const DEFAULT_DECORATOR_HEIGHT = 120
export const MAX_DECORATORS_PER_PACKAGE = 3

export const decorators = reactive(new Map())

/** Register a decorator; ``options`` come from the manifest (``position``, ``maxHeight``, ``roles``). */
export function registerDecorator({ key, pkg, id, label, order, component, options }) {
  decorators.set(key, {
    key,
    pkg,
    id,
    label: label || id,
    order: Number.isFinite(order) ? order : 100,
    component: markRaw(component),
    position: DECORATOR_POSITIONS.includes(options?.position) ? options.position : "footer",
    maxHeight:
      Number.isFinite(options?.maxHeight) && options.maxHeight > 0
        ? options.maxHeight
        : DEFAULT_DECORATOR_HEIGHT,
    roles: markRaw([...(options?.roles?.length ? options.roles : ["assistant"])]),
  })
}

export function clearDecorators() {
  decorators.clear()
}

/** Decorators for one position of a message with ``role``, in order, at most the per-package cap each. */
export function decoratorsFor(position, role) {
  const perPackage = new Map()
  return [...decorators.values()]
    .filter((d) => d.position === position && d.roles.includes(role))
    .sort((a, b) => a.order - b.order)
    .filter((d) => {
      const count = perPackage.get(d.pkg) || 0
      perPackage.set(d.pkg, count + 1)
      return count < MAX_DECORATORS_PER_PACKAGE
    })
}
