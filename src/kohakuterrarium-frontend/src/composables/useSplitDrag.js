/**
 * HTML5 drag-and-drop state machine for the macro tab-group split
 * surface — the window-manager analogue of ``useChatTabDrag`` (which
 * does the same job inside one chat tab). Shares ``edgeOf`` with that
 * composable; differs in (a) a distinct payload MIME so a chat-tab drag
 * never drops into a macro group (and vice-versa), and (b) it drives
 * the singleton ``tabs`` store instead of a per-scope chat store.
 *
 * Drop semantics on a group's content body / tab strip:
 *   - center 50% → MOVE the tab into the group;
 *   - an edge 25% band → SPLIT the group, the moved tab landing on that
 *     side ("left"/"top" = before, "right"/"bottom" = after).
 *
 * Module-scoped state is intentional: a dragstart in group A must
 * surface the hover-edge tint in group B as the cursor crosses it.
 */
import { computed, ref } from "vue"

import { edgeOf } from "@/composables/useChatTabDrag"
import { useTabsStore } from "@/stores/tabs"

const DATA_KIND = "application/x-kt-macrotab"

// { srcGroupId, tab } | null  — the tab id being dragged + its origin.
const _dragState = ref(null)
// { groupId, edge } | null  — which group/edge the cursor is over.
const _hoverState = ref(null)

export function useSplitDrag() {
  const tabs = useTabsStore()
  const dragging = computed(() => _dragState.value)
  const hoverEdge = computed(() => _hoverState.value)

  function isHoveringEdgeOf(groupId) {
    return _hoverState.value && _hoverState.value.groupId === groupId
      ? _hoverState.value.edge
      : null
  }

  function isOurDrag(ev) {
    try {
      if (ev.dataTransfer?.types && Array.from(ev.dataTransfer.types).includes(DATA_KIND)) {
        return true
      }
    } catch {
      /* swallow */
    }
    return !!_dragState.value
  }

  function _readPayload(ev) {
    try {
      const raw = ev.dataTransfer?.getData?.(DATA_KIND)
      if (raw) return JSON.parse(raw)
    } catch {
      /* swallow */
    }
    return _dragState.value
  }

  function onTabDragStart(ev, srcGroupId, tab) {
    if (!srcGroupId || !tab) return
    _dragState.value = { srcGroupId, tab }
    _hoverState.value = null
    try {
      ev.dataTransfer.effectAllowed = "move"
      ev.dataTransfer.setData(DATA_KIND, JSON.stringify({ srcGroupId, tab }))
      ev.dataTransfer.setData("text/plain", tab)
    } catch {
      /* swallow — older Safari throws on some MIME types */
    }
  }

  function onTabDragEnd() {
    _dragState.value = null
    _hoverState.value = null
  }

  function onBubbleDragOver(ev, groupId) {
    if (!isOurDrag(ev)) return
    ev.preventDefault()
    try {
      ev.dataTransfer.dropEffect = "move"
    } catch {
      /* swallow */
    }
    const rect = ev.currentTarget?.getBoundingClientRect?.()
    const edge = edgeOf(rect, ev.clientX, ev.clientY)
    _hoverState.value = edge ? { groupId, edge } : null
  }

  function onBubbleDragLeave(ev, groupId) {
    if (_hoverState.value?.groupId === groupId) _hoverState.value = null
  }

  function onBubbleDrop(ev, groupId) {
    if (!isOurDrag(ev)) return
    ev.preventDefault()
    const payload = _readPayload(ev)
    _hoverState.value = null
    _dragState.value = null
    if (!payload || !payload.srcGroupId || !payload.tab) return
    if (!tabs.tabGroups?.[groupId]) return
    const rect = ev.currentTarget?.getBoundingClientRect?.()
    const edge = edgeOf(rect, ev.clientX, ev.clientY)
    if (!edge) return
    if (edge === "center") {
      if (payload.srcGroupId === groupId) return // no-op same-group center drop
      tabs.moveTabToGroup(payload.srcGroupId, payload.tab, groupId, -1)
      return
    }
    const direction = edge === "left" || edge === "right" ? "horizontal" : "vertical"
    const side = edge === "left" || edge === "top" ? "before" : "after"
    tabs.splitTabGroup(groupId, direction, side, payload.tab, payload.srcGroupId)
  }

  function onTabStripDragOver(ev, groupId) {
    if (!isOurDrag(ev)) return
    ev.preventDefault()
    try {
      ev.dataTransfer.dropEffect = "move"
    } catch {
      /* swallow */
    }
    if (groupId) _hoverState.value = { groupId, edge: "center" }
  }

  function onTabStripDrop(ev, dstGroupId, dstIndex) {
    if (!isOurDrag(ev)) return
    ev.preventDefault()
    const payload = _readPayload(ev)
    _hoverState.value = null
    _dragState.value = null
    if (!payload || !payload.srcGroupId || !payload.tab) return
    if (!tabs.tabGroups?.[dstGroupId]) return
    tabs.moveTabToGroup(payload.srcGroupId, payload.tab, dstGroupId, dstIndex)
  }

  function cancelDrag() {
    _dragState.value = null
    _hoverState.value = null
  }

  return {
    dragging,
    hoverEdge,
    isHoveringEdgeOf,
    isOurDrag,
    onTabDragStart,
    onTabDragEnd,
    onBubbleDragOver,
    onBubbleDragLeave,
    onBubbleDrop,
    onTabStripDragOver,
    onTabStripDrop,
    cancelDrag,
  }
}

/** Reset the module-scoped drag state. Tests only. */
export function _resetForTests() {
  _dragState.value = null
  _hoverState.value = null
}
