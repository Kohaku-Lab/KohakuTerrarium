/**
 * HTML5 native drag-and-drop state machine for the multi-chat-panel
 * Option E surface. Each ``ChatPanel`` instance consumes this to
 *
 *   - mark its tab elements ``draggable=true`` and emit a typed
 *     payload (``application/x-kt-tab``) on dragstart;
 *   - report bubble-hover state so the bubble can render edge tints;
 *   - dispatch the drop into the chat store via ``moveTab`` /
 *     ``splitGroup`` based on which 25%-edge zone (or center) the
 *     cursor was in.
 *
 * The composable is intentionally stateful at MODULE scope (single
 * ``_state`` object) so two ``ChatPanel`` instances see the SAME
 * dragging tab — the dragstart in panel A surfaces ``hover-edge`` in
 * panel B as the cursor crosses B's bubble. Without module-level
 * shared state, each ``ChatPanel`` would start a fresh local store
 * on import and the source-vs-destination link would be lost.
 *
 * Composable usage:
 *
 *     const drag = useChatTabDrag(chatStore)
 *     <div :draggable="true"
 *          @dragstart="drag.onTabDragStart($event, groupId, tab)"
 *          @dragend="drag.onTabDragEnd()" />
 *     <div @dragover.prevent="drag.onBubbleDragOver($event, groupId)"
 *          @drop.prevent="drag.onBubbleDrop($event, groupId)" />
 *
 * Always pass the SAME ``chatStore`` reference (resolved once at
 * ``ChatPanelContainer`` via ``inject('chatStore')``) — multiple
 * stores would split the drag state across scopes and undo the
 * shared-source semantics above.
 */
import { computed, ref } from "vue"

// Module-scoped reactive state. ``ref`` is fine here even outside a
// component because Vue's reactivity system attaches lazily on first
// read; nothing observes the ref until a component consumes the
// composable.
const _dragState = ref(null) // { srcGroupId, tab, sourceChat }
const _hoverState = ref(null) // { groupId, edge }

/** Compute which 25%-edge of a rectangle a cursor is in.
 *  ``edgeOf({left, top, width, height}, clientX, clientY)`` →
 *  ``"left" | "right" | "top" | "bottom" | "center" | null``.
 *
 *  Left/right take priority over top/bottom when the cursor is in a
 *  corner region (the user's intent is usually horizontal split when
 *  dragging side-to-side). Returns ``null`` only when the rect has
 *  no area (defensive against zero-size bubbles before mount). */
export function edgeOf(rect, clientX, clientY) {
  if (!rect || !rect.width || !rect.height) return null
  const xPct = (clientX - rect.left) / rect.width
  const yPct = (clientY - rect.top) / rect.height
  if (xPct < 0 || xPct > 1 || yPct < 0 || yPct > 1) return null
  if (xPct < 0.25) return "left"
  if (xPct > 0.75) return "right"
  if (yPct < 0.25) return "top"
  if (yPct > 0.75) return "bottom"
  return "center"
}

const DATA_KIND = "application/x-kt-tab"

export function useChatTabDrag(chat) {
  const dragging = computed(() => _dragState.value)
  const hoverEdge = computed(() => _hoverState.value)

  function isHoveringEdgeOf(groupId) {
    return _hoverState.value && _hoverState.value.groupId === groupId
      ? _hoverState.value.edge
      : null
  }

  function onTabDragStart(ev, srcGroupId, tab) {
    if (!srcGroupId || !tab) return
    _dragState.value = { srcGroupId, tab }
    _hoverState.value = null
    try {
      ev.dataTransfer.effectAllowed = "move"
      ev.dataTransfer.setData(DATA_KIND, JSON.stringify({ srcGroupId, tab }))
      // Some browsers blank the default ghost when no text payload
      // exists; setting both ensures cross-browser ghost rendering.
      ev.dataTransfer.setData("text/plain", tab)
    } catch {
      /* swallow — older Safari throws on some MIME types */
    }
  }

  function onTabDragEnd() {
    _dragState.value = null
    _hoverState.value = null
  }

  function _readPayload(ev) {
    // ``application/x-kt-tab`` is only readable on ``drop`` in most
    // browsers (security-sensitive payloads are masked during
    // ``dragover``). Fall back to the module-scoped ``_dragState``
    // so the dragover handler can still surface the right edge tint
    // without round-tripping through dataTransfer.
    try {
      const raw = ev.dataTransfer?.getData?.(DATA_KIND)
      if (raw) return JSON.parse(raw)
    } catch {
      /* swallow */
    }
    return _dragState.value
  }

  function _isOurDrag(ev) {
    // During ``dragover`` ``dataTransfer.types`` exposes the kind
    // (case-insensitive). Module-level ``_dragState`` is the fallback
    // for browsers that mask payload during dragover.
    try {
      if (ev.dataTransfer?.types && Array.from(ev.dataTransfer.types).includes(DATA_KIND)) {
        return true
      }
    } catch {
      /* swallow */
    }
    return !!_dragState.value
  }

  function onBubbleDragOver(ev, groupId) {
    if (!_isOurDrag(ev)) return
    ev.preventDefault()
    try {
      ev.dataTransfer.dropEffect = "move"
    } catch {
      /* swallow */
    }
    const rect = ev.currentTarget?.getBoundingClientRect?.()
    const edge = edgeOf(rect, ev.clientX, ev.clientY)
    if (!edge) {
      _hoverState.value = null
      return
    }
    _hoverState.value = { groupId, edge }
  }

  function onBubbleDragLeave(ev, groupId) {
    if (_hoverState.value?.groupId === groupId) _hoverState.value = null
  }

  function onBubbleDrop(ev, groupId) {
    if (!_isOurDrag(ev)) return
    ev.preventDefault()
    const payload = _readPayload(ev)
    _hoverState.value = null
    _dragState.value = null
    if (!payload || !payload.srcGroupId || !payload.tab) return
    if (!chat?.groups?.[groupId]) return
    const rect = ev.currentTarget?.getBoundingClientRect?.()
    const edge = edgeOf(rect, ev.clientX, ev.clientY)
    if (!edge) return
    if (edge === "center") {
      // Move to the destination group (append at the end).
      if (payload.srcGroupId === groupId) return // no-op same-group center drop
      chat.moveTab(payload.srcGroupId, payload.tab, groupId, chat.groups[groupId].tabs.length)
      return
    }
    // Edge → split. The new group lands on the chosen side, carrying
    // the moved tab. ``before`` = left/top, ``after`` = right/bottom.
    // Pass ``srcGroupId`` so the tab is REMOVED from its origin (the
    // ``a|b|c drag c onto a's side`` case must produce ``c|a|b`` not
    // ``a|c|b|c`` — without ``srcGroupId``, ``splitGroup`` adds the
    // tab to the new sibling but never removes it from the source).
    const direction = edge === "left" || edge === "right" ? "horizontal" : "vertical"
    const side = edge === "left" || edge === "top" ? "before" : "after"
    chat.splitGroup(groupId, direction, side, payload.tab, payload.srcGroupId)
  }

  function onTabStripDragOver(ev, groupId) {
    if (!_isOurDrag(ev)) return
    ev.preventDefault()
    try {
      ev.dataTransfer.dropEffect = "move"
    } catch {
      /* swallow */
    }
    // Surface a "this drop will move the tab into this group" hover
    // signal — the bubble below will render the center-tint overlay,
    // making it clear the operation is MOVE (merge into tab strip),
    // NOT split. Without this, the user dragging onto a tab strip
    // sees no indicator until they hit the bubble below — which
    // shows an edge / split tint that misleads about what'll happen.
    if (groupId) _hoverState.value = { groupId, edge: "center" }
  }

  function onTabStripDrop(ev, dstGroupId, dstIndex) {
    if (!_isOurDrag(ev)) return
    ev.preventDefault()
    const payload = _readPayload(ev)
    _hoverState.value = null
    _dragState.value = null
    if (!payload || !payload.srcGroupId || !payload.tab) return
    if (!chat?.groups?.[dstGroupId]) return
    chat.moveTab(payload.srcGroupId, payload.tab, dstGroupId, dstIndex)
  }

  function cancelDrag() {
    _dragState.value = null
    _hoverState.value = null
  }

  return {
    dragging,
    hoverEdge,
    isHoveringEdgeOf,
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
