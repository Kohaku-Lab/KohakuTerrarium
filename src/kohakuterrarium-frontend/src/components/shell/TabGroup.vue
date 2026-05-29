<template>
  <!--
    One tab-group leaf: a tab strip + the active tab's content. The
    body is a drop target for cross-group tab drags — dropping near an
    edge splits the group, dropping in the center moves the tab in.
  -->
  <div class="relative h-full flex flex-col overflow-hidden bg-warm-50 dark:bg-warm-950" :class="focused ? 'outline outline-1 -outline-offset-1 outline-iolite/40' : ''" @pointerdown="onFocus">
    <TabStrip :group-id="groupId" />
    <!--
      ``flex flex-col`` is load-bearing: TabContent uses ``flex-1`` to
      claim the remaining height (same contract as the old flat shell,
      where TabContent sat directly in MacroShell's flex-col). Without
      it, TabContent collapses to content-height and the inner view
      loses its scroll area. ``min-h-0`` lets the flex child shrink so
      its own ``overflow`` can scroll. The split tree only sizes this
      box; the content expands / overflows inside it.
    -->
    <div class="relative flex flex-col flex-1 min-h-0 min-w-0 overflow-hidden" @dragover="onBodyDragOver" @dragleave="onBodyDragLeave" @drop="onBodyDrop">
      <TabContent :group-id="groupId" />
      <!-- Edge/center tint overlay shown while a tab is dragged over. -->
      <div v-if="hoverEdge" class="pointer-events-none absolute z-20 bg-iolite/25 border border-iolite/50 transition-all" :class="overlayClass" />
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue"

import TabStrip from "@/components/shell/TabStrip.vue"
import TabContent from "@/components/shell/TabContent.vue"
import { useTabsStore } from "@/stores/tabs"
import { useSplitDrag } from "@/composables/useSplitDrag"

const props = defineProps({
  groupId: { type: String, required: true },
})

const tabs = useTabsStore()
const drag = useSplitDrag()

// Focus ring only matters once there's more than one group — a solo
// group is visually identical to the old single strip.
const focused = computed(() => tabs.focusedGroupId === props.groupId && tabs.groupCount > 1)

function onFocus() {
  if (tabs.focusedGroupId !== props.groupId) tabs.setFocusedGroup(props.groupId)
}

const hoverEdge = computed(() => drag.isHoveringEdgeOf(props.groupId))

function onBodyDragOver(ev) {
  drag.onBubbleDragOver(ev, props.groupId)
}
function onBodyDragLeave(ev) {
  drag.onBubbleDragLeave(ev, props.groupId)
}
function onBodyDrop(ev) {
  drag.onBubbleDrop(ev, props.groupId)
}

// Map the hovered edge to an overlay rectangle (Tailwind insets).
const overlayClass = computed(() => {
  switch (hoverEdge.value) {
    case "left":
      return "inset-y-0 left-0 w-1/2"
    case "right":
      return "inset-y-0 right-0 w-1/2"
    case "top":
      return "inset-x-0 top-0 h-1/2"
    case "bottom":
      return "inset-x-0 bottom-0 h-1/2"
    case "center":
      return "inset-0"
    default:
      return "hidden"
  }
})
</script>
