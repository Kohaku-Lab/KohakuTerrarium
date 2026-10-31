/**
 * Message renderers: fenced blocks in a reply drawn by a package component.
 *
 * A renderer is named by a dotted id (``pkg.poll``). A reply that holds a
 * closed code fence whose info string is that id shows the component in
 * place of the fence; the stored text is unchanged. The ids are announced in
 * ``client_hello`` so a plugin can offer the format to the model only while a
 * client that draws it is attached.
 */

import { markRaw, reactive } from "vue"

export const messageRenderers = reactive(new Map())

/** Register the renderer for ``id``; return whether it took (the first registration of an id wins). */
export function registerMessageRenderer(id, component, { key, version = 1 } = {}) {
  if (typeof id !== "string" || !id) throw new Error("registerMessageRenderer needs an id")
  if (!component) throw new Error(`registerMessageRenderer ${id} needs a component`)
  const owner = key || `id:${id}`
  const existing = messageRenderers.get(id)
  if (existing) {
    if (existing.key !== owner) {
      console.warn(
        `[message-renderers] ${id} already provided by ${existing.key}; ignoring ${owner}`,
      )
    }
    return false
  }
  messageRenderers.set(id, {
    id,
    key: owner,
    component: markRaw(component),
    version: Number.isInteger(version) && version >= 1 ? version : 1,
  })
  return true
}

export function unregisterMessageRenderersByKey(keys) {
  const drop = new Set(keys)
  for (const [id, entry] of [...messageRenderers.entries()])
    if (drop.has(entry.key)) messageRenderers.delete(id)
}

export function clearMessageRenderers() {
  messageRenderers.clear()
}

/** ``{id: version}`` of every registered renderer, for ``client_hello``. */
export function messageRendererVersions() {
  const out = {}
  for (const [id, entry] of messageRenderers) out[id] = entry.version
  return out
}

/** Registered ids that occur in ``text``; a cheap check before splitting a reply. */
export function renderersIn(text) {
  if (typeof text !== "string" || !messageRenderers.size) return []
  return [...messageRenderers.keys()].filter((id) => text.includes(id))
}
