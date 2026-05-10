/**
 * Pure helpers over the preset tree shape.
 *
 *   SplitNode = { type: "split", direction, ratio, children: [Node, Node] }
 *   LeafNode  = { type: "leaf", panelId: string }
 *
 * Lives in utils/ (not stores/layoutPanels.js) so render code can
 * walk presets without pulling in the panel registry side-effects.
 */

/**
 * Walk a preset tree depth-first and return its leaf panel IDs in
 * visual reading order (left before right, top before bottom). Used
 * by the compact shell to derive a tab-bar order from any preset.
 *
 * Falls back to `preset.slots` (legacy zone+slot model) when no tree
 * is present, so legacy presets remain enumerable.
 */
export function presetLeafPanelIds(preset) {
  if (!preset) return []
  const tree = preset.tree
  if (!tree) {
    if (Array.isArray(preset.slots)) {
      const ids = []
      for (const slot of preset.slots) {
        if (slot?.panelId) ids.push(slot.panelId)
      }
      return ids
    }
    return []
  }
  const out = []
  const stack = [tree]
  while (stack.length) {
    const node = stack.pop()
    if (!node) continue
    if (node.type === "leaf") {
      if (node.panelId) out.push(node.panelId)
    } else if (node.type === "split" && Array.isArray(node.children)) {
      // Push right then left so left pops first → reading order.
      stack.push(node.children[1])
      stack.push(node.children[0])
    }
  }
  return out
}
