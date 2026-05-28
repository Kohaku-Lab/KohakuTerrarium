/**
 * Resize-handle math for two-child split nodes. Lifted from
 * ``components/layout/LayoutNode.vue`` so the chat-internal split tree
 * (``ChatGroupNode.vue``) can reuse the exact same pointer/percent
 * geometry without duplicating it.
 *
 * REACTIVITY CONTRACT — caller must pass ``getNode``, NOT a destructured
 * ``node`` value. Why: the chat-internal tree mutates a leaf into a
 * split (and vice versa) on the SAME ``ChatGroupNode`` instance. Vue
 * reuses the component (no re-mount, no fresh setup) and just patches
 * ``props.node``. A destructured ``node`` parameter would freeze on
 * the original value at call time — when a leaf becomes a horizontal
 * split, the computed styles would still read ``node.direction ===
 * undefined`` (from the OLD leaf) and apply the vertical-fallback
 * styles ``{ height: 50%, width: 100% }``, producing the visible
 * "horizontal split has height: 50% on its children" bug.
 *
 *   GOOD:  useSplitResize({ getNode: () => props.node, ... })
 *   BAD:   useSplitResize({ node: props.node, ... })   // freezes!
 *
 * The composable is intentionally stateless beyond ``containerEl`` +
 * ``dragging`` — the *authoritative* ratio lives in the caller's store
 * (chat groupTree or layout tree), and the caller is responsible for
 * persisting + clamping it.
 *
 * @param {object} args
 * @param {() => { direction: "horizontal" | "vertical", ratio?: number }} args.getNode
 *   Function returning the CURRENT split node. Called inside every
 *   reactive computation so updates to ``props.node`` propagate.
 * @param {(pct: number) => void} args.onChange called with the new
 *   ratio (10-90, clamped) on every pointer-move tick.
 * @param {() => void} [args.onCommit] called once on pointer-up so the
 *   caller can persist the final ratio (the LayoutNode equivalent is
 *   ``layout.persistTreeRatios()``).
 */
import { computed, ref } from "vue"

export function useSplitResize({ getNode, onChange, onCommit }) {
  const containerEl = ref(null)
  const dragging = ref(false)

  const direction = computed(() => getNode?.()?.direction)

  const ratio = computed(() => {
    const r = getNode?.()?.ratio
    return typeof r === "number" && Number.isFinite(r) ? r : 50
  })

  const firstStyle = computed(() =>
    direction.value === "horizontal"
      ? { width: ratio.value + "%", height: "100%" }
      : { height: ratio.value + "%", width: "100%" },
  )

  const secondStyle = computed(() =>
    direction.value === "horizontal"
      ? { width: 100 - ratio.value + "%", height: "100%" }
      : { height: 100 - ratio.value + "%", width: "100%" },
  )

  const handleClass = computed(() =>
    direction.value === "horizontal"
      ? "w-[3px] cursor-col-resize hover:bg-iolite/30 active:bg-iolite/50"
      : "h-[3px] cursor-row-resize hover:bg-iolite/30 active:bg-iolite/50",
  )

  function onPointerDown(e) {
    if (!containerEl.value) return
    dragging.value = true
    try {
      e.target.setPointerCapture?.(e.pointerId)
    } catch {
      /* swallow — capture is best-effort; some pointer types don't support it */
    }

    const onMove = (ev) => {
      if (!dragging.value || !containerEl.value) return
      const rect = containerEl.value.getBoundingClientRect()
      const dir = direction.value
      const span = dir === "horizontal" ? rect.width : rect.height
      if (!span) return
      const offset = dir === "horizontal" ? ev.clientX - rect.left : ev.clientY - rect.top
      const pct = (offset / span) * 100
      // Clamp so a child never gets less than 10% of the parent —
      // matches LayoutNode's implicit min via the same caller-side
      // clamp in ``layout.setTreeRatio``. Keeps the resize handle
      // reachable even when the user yanks the cursor offscreen.
      const clamped = Math.max(10, Math.min(90, pct))
      onChange?.(clamped)
    }

    const onUp = (ev) => {
      dragging.value = false
      try {
        ev.target?.releasePointerCapture?.(ev.pointerId)
      } catch {
        /* swallow */
      }
      ev.target?.removeEventListener?.("pointermove", onMove)
      ev.target?.removeEventListener?.("pointerup", onUp)
      ev.target?.removeEventListener?.("pointercancel", onUp)
      onCommit?.()
    }

    e.target.addEventListener("pointermove", onMove)
    e.target.addEventListener("pointerup", onUp)
    e.target.addEventListener("pointercancel", onUp)
  }

  return {
    containerEl,
    dragging,
    firstStyle,
    secondStyle,
    handleClass,
    onPointerDown,
  }
}
