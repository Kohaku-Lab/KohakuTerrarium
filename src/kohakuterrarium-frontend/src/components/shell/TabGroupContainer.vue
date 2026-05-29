<template>
  <!--
    The macro content area: a recursive split tree of tab-groups. A
    single-leaf tree (the default) renders exactly one strip + content,
    visually identical to the old flat shell — the split affordances
    (drag a tab to an edge, or the keyboard shortcuts below) are simply
    live the whole time, no flag.
  -->
  <div class="flex-1 flex flex-col overflow-hidden">
    <SplitTreeNode v-if="tabs.tabTree" :node="tabs.tabTree" :path="[]" :on-set-ratio="setRatio">
      <template #leaf="{ id }">
        <TabGroup :group-id="id" />
      </template>
    </SplitTreeNode>
  </div>
</template>

<script setup>
import { onMounted, onUnmounted } from "vue"

import SplitTreeNode from "@/components/common/SplitTreeNode.vue"
import TabGroup from "@/components/shell/TabGroup.vue"
import { useTabsStore } from "@/stores/tabs"

const tabs = useTabsStore()

// Guarantee a tree exists even before persistence hydrates (the v-if
// also guards, but this avoids a blank first paint).
tabs._ensureTree()

function setRatio(path, ratio) {
  tabs.setTabGroupRatio(path, ratio)
}

// ── Keyboard (VS Code-style editor groups) ──
//
// Conflict guard: when a chat (``attach``) tab is the focused group's
// active tab, its own ``ChatPanelContainer`` already binds Ctrl+\\ /
// Ctrl+W / Ctrl+Tab for the chat-internal split tree. We yield those
// to it and only act at the macro level when the focused content is
// NOT a chat surface. Users can still split a chat tab out via drag.
function focusedIsChat() {
  const t = tabs.groupActiveTab(tabs.focusedGroupId)
  return t?.kind === "attach"
}

function onKey(ev) {
  const tag = ev.target?.tagName
  const editable = tag === "INPUT" || tag === "TEXTAREA" || ev.target?.isContentEditable
  if (editable) return
  if (!tabs.tabTree) return
  if (!ev.ctrlKey) return

  // Ctrl+\  → split focused group horizontally
  // Ctrl+Alt+\ → split vertically
  if (ev.key === "\\" && !ev.shiftKey) {
    if (focusedIsChat()) return
    if (!tabs.focusedGroupId) return
    ev.preventDefault()
    const direction = ev.altKey ? "vertical" : "horizontal"
    const active = tabs.tabGroups[tabs.focusedGroupId]?.activeId
    // Carry the active tab into the new group only when the group has
    // more than one tab (don't empty a single-tab group).
    const group = tabs.tabGroups[tabs.focusedGroupId]
    const moved = group && group.tabIds.length > 1 ? active : null
    tabs.splitTabGroup(tabs.focusedGroupId, direction, "after", moved, tabs.focusedGroupId)
    return
  }

  // Ctrl+Shift+W → close the focused group (only when >1 exists)
  if ((ev.key === "w" || ev.key === "W") && ev.shiftKey) {
    if (focusedIsChat()) return
    if (tabs.groupCount <= 1 || !tabs.focusedGroupId) return
    ev.preventDefault()
    tabs.removeTabGroup(tabs.focusedGroupId)
    return
  }

  // Ctrl+Tab / Ctrl+Shift+Tab → cycle focused group in tree order
  if (ev.key === "Tab") {
    if (focusedIsChat()) return
    if (tabs.groupCount < 2) return
    ev.preventDefault()
    cycleFocus(ev.shiftKey ? -1 : 1)
    return
  }

  // Ctrl+1..9 → focus the Nth group in tree order
  if (!ev.altKey && !ev.shiftKey && /^[1-9]$/.test(ev.key)) {
    if (focusedIsChat()) return
    const order = tabs.groupOrder()
    const idx = parseInt(ev.key, 10) - 1
    if (idx >= 0 && idx < order.length) {
      ev.preventDefault()
      tabs.setFocusedGroup(order[idx])
    }
  }
}

function cycleFocus(dir) {
  const order = tabs.groupOrder()
  if (order.length < 2) return
  let idx = order.indexOf(tabs.focusedGroupId)
  if (idx < 0) idx = 0
  const next = (idx + dir + order.length) % order.length
  tabs.setFocusedGroup(order[next])
}

onMounted(() => {
  globalThis.addEventListener?.("keydown", onKey)
})
onUnmounted(() => {
  globalThis.removeEventListener?.("keydown", onKey)
})
</script>
