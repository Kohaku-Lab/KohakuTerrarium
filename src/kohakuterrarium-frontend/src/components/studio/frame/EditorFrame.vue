<template>
  <div class="kt-v2 kt-v2-canvas h-full w-full flex flex-col overflow-hidden" :data-studio-frame="frameId">
    <header v-if="$slots.head" class="kt-v2-line shrink-0 h-12 flex items-center gap-2 px-4 border-b">
      <slot name="head" />
    </header>

    <!-- Only a second open view (a module's doc beside its source) earns a strip. -->
    <TabStrip v-if="tabs && tabs.length > 1" :tabs="tabs" :active="activeTab" @select="$emit('tab:select', $event)" @close="$emit('tab:close', $event)" />

    <Splitpanes class="flex-1 min-h-0" :dbl-click-splitter="false" @resized="onResize">
      <Pane :size="leftPct" :min-size="hasLeft ? minLeftPct : 0" :max-size="40">
        <div class="kt-v2-panel kt-v2-edge h-full overflow-hidden flex flex-col border-r">
          <slot name="left" />
        </div>
      </Pane>

      <Pane :size="mainPct">
        <div class="h-full overflow-hidden flex flex-col">
          <slot name="main" />
        </div>
      </Pane>

      <Pane :size="rightPct" :min-size="hasRight ? minRightPct : 0" :max-size="45">
        <div class="kt-v2-panel kt-v2-edge h-full overflow-hidden flex flex-col border-l">
          <slot name="right" />
        </div>
      </Pane>
    </Splitpanes>

    <footer v-if="$slots.status" class="kt-v2-chrome kt-v2-line shrink-0 h-7 flex items-center gap-2 px-4 text-[11px] text-warm-500 border-t">
      <slot name="status" />
    </footer>
  </div>
</template>

<script setup>
import { Pane, Splitpanes } from "splitpanes"
import "splitpanes/dist/splitpanes.css"
import { computed, onMounted, ref, useSlots } from "vue"

import { useStudioUiStore } from "@/stores/studio/ui"

import TabStrip from "./TabStrip.vue"

/** An editor page: a head, a left pool, the main column, a right detail column and a status line; column widths persist per `frameId`. */
const props = defineProps({
  frameId: { type: String, required: true },
  /** Open views [{ id, label, icon?, dirty?, pinned? }]; the strip shows from two. */
  tabs: { type: Array, default: () => [] },
  activeTab: { type: String, default: "" },
  defaultLeft: { type: Number, default: 220 },
  defaultRight: { type: Number, default: 320 },
  minLeft: { type: Number, default: 160 },
  minRight: { type: Number, default: 220 },
})

defineEmits(["tab:select", "tab:close"])

const slots = useSlots()
const hasLeft = computed(() => !!slots.left)
const hasRight = computed(() => !!slots.right)

const ui = useStudioUiStore()

// Splitpanes sizes are percentages; pixel widths convert against the measured frame width.
const containerWidth = ref(1280)
const widths = ref({ left: props.defaultLeft, right: props.defaultRight })

onMounted(() => {
  widths.value = ui.getColumns(props.frameId, {
    left: props.defaultLeft,
    right: props.defaultRight,
  })
  requestAnimationFrame(() => {
    const el = document.querySelector(`[data-studio-frame="${props.frameId}"]`)
    if (el) containerWidth.value = el.clientWidth || 1280
  })
})

const leftPct = computed(() => toPct(hasLeft.value ? widths.value.left : 0))
const rightPct = computed(() => toPct(hasRight.value ? widths.value.right : 0))
const mainPct = computed(() => 100 - leftPct.value - rightPct.value)
const minLeftPct = computed(() => toPct(props.minLeft))
const minRightPct = computed(() => toPct(props.minRight))

function toPct(px) {
  if (!px) return 0
  return Math.max(4, Math.min(50, (px / Math.max(containerWidth.value, 400)) * 100))
}

function onResize(event) {
  if (!Array.isArray(event) || event.length !== 3) return
  const [l, , r] = event
  const w = Math.max(containerWidth.value, 400)
  widths.value = { left: Math.round((l.size / 100) * w), right: Math.round((r.size / 100) * w) }
  ui.setColumns(props.frameId, widths.value)
}
</script>

<style scoped>
:deep(.splitpanes__splitter) {
  background: transparent;
  position: relative;
  width: 4px;
  margin: 0 -2px;
  z-index: 5;
}
:deep(.splitpanes__splitter:hover) {
  background: rgba(90, 79, 207, 0.2);
}
:deep(.splitpanes__splitter:active) {
  background: rgba(90, 79, 207, 0.4);
}
</style>
