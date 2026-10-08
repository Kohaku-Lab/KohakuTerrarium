/**
 * Fixed-row-height windowing for long debug lists: which rows to mount for
 * a scroll position, plus the spacer heights that keep the scrollbar true.
 * O(1) per call; the caller renders rows [start, end).
 */
export function visibleRange({
  scrollTop = 0,
  viewportHeight = 0,
  rowHeight,
  count,
  overscan = 8,
}) {
  if (!count || !rowHeight) return { start: 0, end: 0, padTop: 0, padBottom: 0 }
  const fits = Math.ceil(Math.max(0, viewportHeight) / rowHeight)
  const first = Math.min(Math.floor(Math.max(0, scrollTop) / rowHeight), Math.max(0, count - fits))
  const shown = fits + 1
  const start = Math.max(0, Math.min(count, first - overscan))
  const end = Math.max(start, Math.min(count, first + shown + overscan))
  return { start, end, padTop: start * rowHeight, padBottom: (count - end) * rowHeight }
}

/** scrollTop that brings row `index` into view, or null when it already is. */
export function scrollTopFor(index, { scrollTop, viewportHeight, rowHeight }) {
  const top = index * rowHeight
  if (top < scrollTop) return top
  if (top + rowHeight > scrollTop + viewportHeight) return top + rowHeight - viewportHeight
  return null
}
