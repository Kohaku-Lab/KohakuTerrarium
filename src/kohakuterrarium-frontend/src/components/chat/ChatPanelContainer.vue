<template>
  <!--
    The chat workspace slot.

    ``groupTree`` is ALWAYS initialised (a single-leaf tree at first
    mount) so the VSCode-style drag-to-split + right-click affordances
    are immediately available without any flag, toggle, or
    pre-opt-in step. Users who never drag a tab see one group with
    one tab strip — visually byte-identical to the pre-Option-E
    experience — but the gestures are live the whole time.

    The store is resolved ONCE here and provided to every descendant
    via ``provide('chatStore', chat)``. Children must inject — they
    must NOT call ``useChatStore()`` again, since that would risk
    falling back to a different scope (the ``SessionHistoryViewer``
    precedent). This is the canonical chat-scope guard for the slot.
  -->
  <!--
    Outer is plain h-full + overflow-hidden (no flex) so the
    ChatGroupNode child's ``h-full`` propagates reliably regardless
    of what's inside (nested splits use flex-row OR flex-col). With
    a flex-col outer, nested horizontal splits were collapsing
    parents to content-height instead of inheriting the slot's
    height.
  -->
  <div class="h-full w-full overflow-hidden">
    <ChatGroupNode v-if="chat.groupTree" :node="chat.groupTree" :path="[]" :instance="instance" :read-only="readOnly" :empty-title="emptyTitle" :empty-subtitle="emptySubtitle" />
  </div>
</template>

<script setup>
import { onMounted, onUnmounted, provide, watch } from "vue"

import ChatGroupNode from "@/components/chat/ChatGroupNode.vue"
import { useChatStore } from "@/stores/chat"

const props = defineProps({
  instance: { type: Object, default: null },
  readOnly: { type: Boolean, default: false },
  emptyTitle: { type: String, default: "" },
  emptySubtitle: { type: String, default: "" },
})

// SCOPE INVARIANT — same pattern as the existing ChatPanel: bind
// explicitly to the instance id / graph_id so a sibling AttachTab
// hosting a different scope can't accidentally clobber our store.
const chat = useChatStore(props.instance?.id || props.instance?.graph_id || undefined)
provide("chatStore", chat)

/**
 * Ensure ``groupTree`` is populated for the current scope.
 *
 * Always runs — there is NO feature flag and NO opt-in step. The
 * drag-to-split affordance is the primary chat-layout interaction
 * (per Option E §3), and gating it behind a flag would defeat the
 * whole point. Users who never drag see a single group; they're
 * still in "groups mode" but it's transparent.
 *
 * First try to restore a persisted layout for this scope; on a
 * miss, seed a single-leaf tree from the current legacy tabs (or
 * an empty group if no tabs yet — they'll land in it via
 * ``_addTab``).
 */
function ensureGroupTree() {
  if (chat._loadGroupState()) return
  chat.enableGroups()
}

// Run SYNCHRONOUSLY in setup so the first render already sees a
// populated ``groupTree`` — otherwise the initial paint flashes an
// empty container before ``onMounted`` fires and Vue's reactivity
// re-renders the tree. The parent ``AttachTab`` calls
// ``initForInstance`` before mounting the layout tree, so
// ``chat._instanceId`` is already set when this runs.
ensureGroupTree()

// Re-seed on instance change. ``initForInstance`` resets in-memory
// group state along with legacy tabs (see ``stores/chat.js``), so
// on every flip we either restore that scope's persisted layout or
// seed a fresh single-leaf tree.
watch(
  () => chat._instanceId,
  () => ensureGroupTree(),
)

// Defensive guard: ``groupTree`` should never become null while we're
// mounted (the UI blocks removing the last group). If it ever does
// — programmatic call, race, future regression — re-seed immediately
// so drag-to-split affordances stay live.
watch(
  () => chat.groupTree,
  (tree) => {
    if (tree == null) chat.enableGroups()
  },
)

// ── Keyboard shortcuts (Stage E4) ──
//
// Shortcuts only fire when the container is actually in the page
// (mount/unmount lifecycle handles this). To avoid double-handling
// across multiple ``ChatPanelContainer`` instances (e.g. workspace
// preset with two chat slots — unusual but legal), the focused
// group must belong to THIS store before we react.
function onKey(ev) {
  // Guard: keystrokes inside a textarea / input shouldn't trigger
  // shortcuts (the user is typing). EXCEPT for Escape which we
  // delegate to ChatPanel anyway.
  const tag = ev.target?.tagName
  const editable = tag === "INPUT" || tag === "TEXTAREA" || ev.target?.isContentEditable
  if (editable && ev.key !== "Escape") return

  // ``groupTree`` is guaranteed populated by ``ensureGroupTree`` on
  // mount — no need to gate shortcuts on it. If it's somehow null
  // (e.g. fresh store before mount completes), skip silently.
  if (!chat.groupTree) return

  // Ctrl+\ → split focused group horizontally.
  if (ev.key === "\\" && ev.ctrlKey && !ev.altKey && !ev.shiftKey) {
    ev.preventDefault()
    if (chat.focusedGroupId) {
      chat.splitGroup(chat.focusedGroupId, "horizontal", "after", null)
    }
    return
  }
  // Ctrl+Alt+\ → split focused group vertically.
  if (ev.key === "\\" && ev.ctrlKey && ev.altKey && !ev.shiftKey) {
    ev.preventDefault()
    if (chat.focusedGroupId) {
      chat.splitGroup(chat.focusedGroupId, "vertical", "after", null)
    }
    return
  }
  // Ctrl+W → close active tab in focused group.
  if ((ev.key === "w" || ev.key === "W") && ev.ctrlKey && !ev.altKey && !ev.shiftKey) {
    const focused = chat.groups[chat.focusedGroupId]
    const tab = focused?.activeTab
    if (!tab || tab === "root") return // let browser handle it
    ev.preventDefault()
    chat.closeTab(tab)
    return
  }
  // Ctrl+Shift+W → close focused group (only when more than one
  // exists — never strand the user with zero chat surfaces).
  if ((ev.key === "w" || ev.key === "W") && ev.ctrlKey && ev.shiftKey) {
    if (!chat.focusedGroupId) return
    const groupCount = Object.keys(chat.groups || {}).length
    if (groupCount <= 1) return // single group — nothing to close
    ev.preventDefault()
    chat.removeGroup(chat.focusedGroupId)
    return
  }
  // Ctrl+Tab / Ctrl+Shift+Tab → cycle focused group in tree order.
  if (ev.key === "Tab" && ev.ctrlKey) {
    if (Object.keys(chat.groups || {}).length < 2) return
    ev.preventDefault()
    cycleFocus(ev.shiftKey ? -1 : 1)
    return
  }
  // Ctrl+1..9 → focus Nth group in tree order.
  if (ev.ctrlKey && !ev.altKey && !ev.shiftKey && /^[1-9]$/.test(ev.key)) {
    const order = leafOrder()
    const idx = parseInt(ev.key, 10) - 1
    if (idx >= 0 && idx < order.length) {
      ev.preventDefault()
      chat.setFocusedGroup(order[idx])
    }
    return
  }
}

function leafOrder() {
  const out = []
  const visit = (node) => {
    if (!node) return
    if (node.type === "leaf") {
      out.push(node.groupId)
      return
    }
    if (node.type === "split") {
      visit(node.children?.[0])
      visit(node.children?.[1])
    }
  }
  visit(chat.groupTree)
  return out
}

function cycleFocus(dir) {
  const order = leafOrder()
  if (order.length < 2) return
  const cur = chat.focusedGroupId
  let idx = order.indexOf(cur)
  if (idx < 0) idx = 0
  const next = (idx + dir + order.length) % order.length
  chat.setFocusedGroup(order[next])
}

onMounted(() => {
  globalThis.addEventListener?.("keydown", onKey)
})
onUnmounted(() => {
  globalThis.removeEventListener?.("keydown", onKey)
})
</script>
