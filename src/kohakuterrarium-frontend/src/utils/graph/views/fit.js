/**
 * Viewport fitting that keeps the drawing out from under the minimap: the
 * drawing is fitted to the whole canvas when that clears the minimap, else
 * to the larger of the band above it and the band beside it. A drawing too
 * big even at the legible floor keeps its top-left in view. Pure.
 */

export const FIT_PAD = 0.12
export const FIT_MAX = 1.1
const MAP_GAP = 12
const EDGE = 16

/** Offset along one axis: centred when the drawing fits, else its start (top / left) kept in view. */
function axisOffset(start, span, areaStart, areaSpan, zoom) {
  if (span * zoom + 2 * EDGE <= areaSpan)
    return areaStart + areaSpan / 2 - (start + span / 2) * zoom
  return areaStart + EDGE - start * zoom
}

function fitInto(bounds, area, minZoom) {
  const pad = 1 + 2 * FIT_PAD
  const raw = Math.min(area.width / (bounds.width * pad), area.height / (bounds.height * pad))
  const zoom = Math.max(minZoom, Math.min(FIT_MAX, raw))
  return {
    zoom,
    x: axisOffset(bounds.x, bounds.width, area.x, area.width, zoom),
    y: axisOffset(bounds.y, bounds.height, area.y, area.height, zoom),
  }
}

function covers(vp, bounds, mapRect) {
  const x0 = bounds.x * vp.zoom + vp.x
  const y0 = bounds.y * vp.zoom + vp.y
  const x1 = x0 + bounds.width * vp.zoom
  const y1 = y0 + bounds.height * vp.zoom
  return x0 < mapRect.x1 && mapRect.x0 < x1 && y0 < mapRect.y1 && mapRect.y0 < y1
}

/**
 * Viewport {x, y, zoom} fitting `bounds` (flow coords) into `canvas`
 * ({width, height}); `map` ({width, height, right, bottom}: size and inset
 * from the canvas's bottom-right) is the minimap, or null.
 */
export function fitViewport(bounds, canvas, map, minZoom) {
  const whole = { x: 0, y: 0, width: canvas.width, height: canvas.height }
  const full = fitInto(bounds, whole, minZoom)
  if (!map) return full
  const mapRect = {
    x0: canvas.width - map.right - map.width,
    y0: canvas.height - map.bottom - map.height,
    x1: canvas.width - map.right,
    y1: canvas.height - map.bottom,
  }
  if (!covers(full, bounds, mapRect)) return full
  const above = { ...whole, height: Math.max(mapRect.y0 - MAP_GAP, 1) }
  const beside = { ...whole, width: Math.max(mapRect.x0 - MAP_GAP, 1) }
  const a = fitInto(bounds, above, minZoom)
  const b = fitInto(bounds, beside, minZoom)
  return a.zoom >= b.zoom ? a : b
}
