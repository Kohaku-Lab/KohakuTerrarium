/**
 * Flattened, windowed file tree: the visible rows of an editor-store tree
 * (`{path, name, type, children, has_children}`) given the expanded paths,
 * and the slice of rows a scroll viewport shows.
 */

const _sorted = new WeakMap()

/** Children sorted directories-first then by name; cached per children array. */
export function sortedChildren(node) {
  const children = node?.children
  if (!children || !children.length) return []
  let cached = _sorted.get(children)
  if (!cached) {
    cached = [...children].sort((a, b) => {
      if (a.type !== b.type) return a.type === "directory" ? -1 : 1
      return String(a.name).localeCompare(String(b.name))
    })
    _sorted.set(children, cached)
  }
  return cached
}

/** Whether a directory node can expand (advertised or already holding children). */
export function canExpand(node) {
  if (node?.type !== "directory") return false
  if (typeof node.has_children === "boolean") return node.has_children
  return (node.children || []).length > 0
}

/** Visible rows [{node, depth}] under `root` (root itself excluded), in display order. */
export function flattenTree(root, expanded) {
  const rows = []
  const walk = (node, depth) => {
    for (const child of sortedChildren(node)) {
      rows.push({ node: child, depth })
      if (child.type === "directory" && expanded.has(child.path)) walk(child, depth + 1)
    }
  }
  if (root) walk(root, 0)
  return rows
}

/** [start, end) of rows to render for a viewport, with `overscan` rows each side. */
export function visibleRange(total, scrollTop, viewportHeight, rowHeight, overscan = 8) {
  if (total <= 0 || rowHeight <= 0) return [0, 0]
  const count = Math.ceil(Math.max(0, viewportHeight) / rowHeight)
  const first = Math.min(Math.floor(Math.max(0, scrollTop) / rowHeight), Math.max(0, total - count))
  const start = Math.max(0, first - overscan)
  const end = Math.min(total, first + count + overscan)
  return [start, Math.max(start, end)]
}
