/**
 * Shared layout plumbing for the node-link views: runs a placement whenever
 * the layout structure changes, publishes the chosen candidate and its
 * metrics, applies hand-dragged positions on top, and signals when the
 * viewport should fit the new layout.
 */

import { computed, onScopeDispose, ref, shallowRef, watch } from "vue"

import { useI18n } from "@/utils/i18n"

/**
 * @param view       per-surface view store
 * @param input      computed {nodes: [{id, kind, ...}], edges, groups}
 * @param structure  computed string; a change triggers a new layout
 * @param run        async (input, {aspect}) => {boxes, routes, chosen?, metrics?, tried?};
 *                   a change of `view.viewportAspect` re-runs it and refits
 * @param sizeOf     (node) => {width, height}
 * @param fitScope   computed string; a change makes the next layout fit the viewport
 */
export function useViewLayout({ view, input, structure, run, sizeOf, fitScope }) {
  const { t } = useI18n()
  const positions = shallowRef(new Map())
  const routes = shallowRef(new Map())
  const labels = shallowRef(new Map())
  const layoutError = ref("")
  const fitSignal = ref(0)
  let token = 0
  let lastFitScope = null

  async function relayout() {
    const mine = ++token
    if (!input.value.nodes.length) {
      positions.value = new Map()
      routes.value = new Map()
      view.setLayoutInfo(null)
      return
    }
    try {
      const aspect = view.viewportAspect
      const result = await run(input.value, { aspect })
      if (mine !== token) return
      positions.value = result.boxes
      routes.value = result.routes || new Map()
      labels.value = result.labels || new Map()
      view.setLayoutInfo(
        result.metrics
          ? { chosen: result.chosen, metrics: result.metrics, tried: result.tried }
          : null,
      )
      layoutError.value = ""
      const scope = `${fitScope.value}|${aspect}`
      if (scope !== lastFitScope) {
        lastFitScope = scope
        fitSignal.value += 1
      }
    } catch (err) {
      if (mine === token)
        layoutError.value = t("graph.layoutFailed", { error: err?.message || String(err) })
    }
  }

  watch([structure, () => view.viewportAspect], relayout, { immediate: true })
  // A view switched away from drops its in-flight layout; only the shown view reports.
  onScopeDispose(() => {
    token += 1
  })

  const boxes = computed(() => {
    const out = new Map()
    for (const n of input.value.nodes) {
      const size = sizeOf(n)
      const override = view.positionOverrides[n.id]
      const laid = positions.value.get(n.id)
      const pos = override
        ? { x: override.x, y: override.y }
        : laid
          ? { x: laid.x, y: laid.y }
          : null
      if (pos) out.set(n.id, { ...pos, width: size.width, height: size.height })
    }
    return out
  })

  return { boxes, routes, labels, layoutError, fitSignal, relayout }
}
