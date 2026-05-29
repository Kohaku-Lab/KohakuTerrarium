/**
 * Generic binary split-tree primitive.
 *
 * One tree shape, three consumers: the chat group tree
 * (``stores/chat.js`` + ``ChatGroupNode.vue``), the macro tab-group
 * tree (``stores/tabs.js`` + ``TabGroupNode.vue``), and — in a later
 * follow-up — the workspace layout tree (``layoutPanels.js`` +
 * ``LayoutNode.vue``). Before this util those trees each carried their
 * own near-identical walk/find/split/prune helpers; this is the single
 * source of truth.
 *
 *   LeafNode  = { type: "leaf", id: string }
 *   SplitNode = { type: "split", direction: "horizontal" | "vertical",
 *                 ratio: 10..90, children: [Node, Node] }
 *
 * Leaves are keyed by an opaque ``id`` (a chat ``groupId``, a macro
 * tab-group id, a workspace ``panelId`` — the util does not care). All
 * functions are pure EXCEPT ``setRatioAt`` which mutates in place (see
 * its doc-comment for why that's deliberate).
 */

/** Depth-first walk in visual reading order (left/top before
 *  right/bottom). ``visit(id, path)`` runs on every leaf; ``path`` is
 *  the array of child indices from the root to that leaf. */
export function walkTree(tree, visit, path = []) {
  if (!tree) return
  if (tree.type === "leaf") {
    visit(tree.id, path)
    return
  }
  if (tree.type === "split") {
    walkTree(tree.children?.[0], visit, [...path, 0])
    walkTree(tree.children?.[1], visit, [...path, 1])
  }
}

/** First leaf id in reading order, or ``null`` for an empty tree. */
export function firstLeafId(tree) {
  let found = null
  walkTree(tree, (id) => {
    if (found == null) found = id
  })
  return found
}

/** All leaf ids in reading order. */
export function leafOrder(tree) {
  const out = []
  walkTree(tree, (id) => out.push(id))
  return out
}

/** Path (array of child indices) to the leaf with ``id``, or ``null``. */
export function findLeafPath(tree, id, path = []) {
  if (!tree) return null
  if (tree.type === "leaf") return tree.id === id ? path : null
  if (tree.type === "split") {
    const left = findLeafPath(tree.children?.[0], id, [...path, 0])
    if (left) return left
    return findLeafPath(tree.children?.[1], id, [...path, 1])
  }
  return null
}

/** Split the leaf ``targetId`` into a split node. The new leaf
 *  (``newId``) lands on the side given by ``edge`` (``"before"`` =
 *  left/top, ``"after"`` = right/bottom). Returns a NEW tree;
 *  no-op (returns the same tree) if ``targetId`` is absent. */
export function splitLeaf(tree, targetId, direction, edge, newId) {
  if (!tree) return tree
  if (tree.type === "leaf") {
    if (tree.id !== targetId) return tree
    const movedLeaf = { type: "leaf", id: newId }
    const keptLeaf = { type: "leaf", id: tree.id }
    const children = edge === "before" ? [movedLeaf, keptLeaf] : [keptLeaf, movedLeaf]
    return { type: "split", direction, ratio: 50, children }
  }
  if (tree.type === "split") {
    return {
      ...tree,
      children: [
        splitLeaf(tree.children?.[0], targetId, direction, edge, newId),
        splitLeaf(tree.children?.[1], targetId, direction, edge, newId),
      ],
    }
  }
  return tree
}

/** Remove the leaf ``id``, collapsing the surviving sibling into the
 *  parent's slot. Returns the new tree (``null`` if the last leaf was
 *  removed). */
export function pruneLeaf(tree, id) {
  if (!tree) return null
  if (tree.type === "leaf") {
    return tree.id === id ? null : tree
  }
  if (tree.type === "split") {
    const left = pruneLeaf(tree.children?.[0], id)
    const right = pruneLeaf(tree.children?.[1], id)
    if (!left && !right) return null
    if (!left) return right
    if (!right) return left
    return { ...tree, children: [left, right] }
  }
  return tree
}

/** Mutate a split's ``ratio`` at ``path`` (array of child indices),
 *  IN PLACE, clamped to 10..90.
 *
 *  Why in-place: during a splitter drag the ratio updates on every
 *  pointermove. Replacing the tree with a fresh object each move would
 *  unmount + remount the recursive node components — the in-flight
 *  pointer capture lives on the OLD handle's DOM node, so the drag
 *  would break after the first move. Mutating in place keeps the DOM
 *  stable; Vue/Pinia reactivity still detects the nested mutation.
 *
 *  Silent no-op if ``path`` doesn't point at a split node. */
export function setRatioAt(tree, path, ratio) {
  if (!tree || !Array.isArray(path)) return
  if (path.length === 0) {
    if (tree.type !== "split") return
    tree.ratio = Math.max(10, Math.min(90, ratio))
    return
  }
  if (tree.type !== "split") return
  const [idx, ...rest] = path
  if (idx !== 0 && idx !== 1) return
  setRatioAt(tree.children?.[idx], rest, ratio)
}

/** Build a single-leaf tree. */
export function leafTree(id) {
  return { type: "leaf", id }
}

/** True when every leaf id is present in ``validIds`` (a Set or array
 *  with ``.includes``). Used by stores to validate a restored tree
 *  against their group/panel registry before adopting it. */
export function treeLeavesValid(tree, validIds) {
  const has = validIds instanceof Set ? (id) => validIds.has(id) : (id) => validIds?.includes?.(id)
  let ok = true
  walkTree(tree, (id) => {
    if (!has(id)) ok = false
  })
  return ok
}
