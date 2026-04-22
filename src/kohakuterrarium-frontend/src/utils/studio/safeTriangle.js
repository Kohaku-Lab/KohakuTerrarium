/**
 * Safe-triangle geometry helpers.
 *
 * "Safe triangle" = the wedge between the mouse's position when it
 * left the source element and the near edge of a destination
 * element. If the mouse stays inside this wedge, its path is likely
 * heading for the destination — so we preserve the hover state
 * instead of clearing it.
 *
 * Popularized by Notion / macOS hierarchical menus to solve the
 * "can't click buttons in the detail panel because hovering over
 * another item changes the content" UX trap.
 */

/**
 * Signed area of the triangle (p1, p2, p3). Sign indicates orientation.
 *
 * @param {{x: number, y: number}} p1
 * @param {{x: number, y: number}} p2
 * @param {{x: number, y: number}} p3
 * @returns {number}
 */
export function sign(p1, p2, p3) {
  return (p1.x - p3.x) * (p2.y - p3.y) - (p2.x - p3.x) * (p1.y - p3.y)
}

/**
 * True iff point `p` lies inside the triangle `(a, b, c)` (inclusive
 * of edges). Uses the "same side" cross-product test — works for
 * both CW and CCW triangles.
 */
export function pointInTriangle(p, a, b, c) {
  const s1 = sign(p, a, b)
  const s2 = sign(p, b, c)
  const s3 = sign(p, c, a)
  const hasNeg = s1 < 0 || s2 < 0 || s3 < 0
  const hasPos = s1 > 0 || s2 > 0 || s3 > 0
  return !(hasNeg && hasPos)
}

/**
 * Build the safe triangle between an `origin` mouse point and a
 * destination `DOMRect` (typically the detail panel's bounding box).
 *
 * Assumes the destination is to the right of the origin (the typical
 * left-pool → right-detail layout). If the destination is elsewhere,
 * the triangle still works geometrically but the user probably
 * doesn't want this preservation.
 *
 * Returns the two base points of the triangle; combined with
 * `origin` they make the full wedge.
 */
export function safeTriangleBase(destRect) {
  return [
    { x: destRect.left, y: destRect.top },
    { x: destRect.left, y: destRect.bottom },
  ]
}

/**
 * Test whether `p` lies inside the rectangle described by `rect`
 * (a DOMRect-shaped object). Inclusive of edges.
 */
export function pointInRect(p, rect) {
  return p.x >= rect.left && p.x <= rect.right && p.y >= rect.top && p.y <= rect.bottom
}
