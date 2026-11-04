/**
 * Reading anchor: the line at the top of a scroll viewport, remembered as an element plus the
 * fraction of it that is above the viewport top, so a reflow (a width change) can put the same
 * line back at the top instead of keeping the old pixel offset.
 */

function boxChildren(element) {
  const view = element.ownerDocument?.defaultView
  return Array.from(element.children).filter(
    (child) => view?.getComputedStyle(child).display !== "inline",
  )
}

/** The deepest block holding the line at `y`, or the first block below it when `y` is in a gap. */
function blockAt(parent, y) {
  for (const child of boxChildren(parent)) {
    const rect = child.getBoundingClientRect()
    if (!rect.height) continue
    if (rect.top > y) return { element: child, rect }
    if (rect.bottom > y) return blockAt(child, y) ?? { element: child, rect }
  }
  return null
}

/** The anchor for the viewport's current top, or null when no content lies there. */
export function captureReadingAnchor(viewport) {
  if (typeof viewport?.getBoundingClientRect !== "function") return null
  const top = viewport.getBoundingClientRect().top
  const hit = blockAt(viewport, top)
  if (!hit) return null
  return {
    element: hit.element,
    fraction: Math.max(0, top - hit.rect.top) / hit.rect.height,
    below: Math.max(0, hit.rect.top - top),
  }
}

/** Scroll `viewport` so the anchored line is back where it was; false when the anchor is gone. */
export function restoreReadingAnchor(viewport, anchor) {
  if (!viewport || !anchor?.element?.isConnected || !viewport.contains(anchor.element)) return false
  const rect = anchor.element.getBoundingClientRect()
  const top = viewport.getBoundingClientRect().top
  const delta = rect.top + anchor.fraction * rect.height - (top + anchor.below)
  if (Math.abs(delta) >= 0.5) viewport.scrollTop += delta
  return true
}
