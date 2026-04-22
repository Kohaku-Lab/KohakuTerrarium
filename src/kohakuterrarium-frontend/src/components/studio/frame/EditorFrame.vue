<template>
  <div class="h-full w-full flex flex-col bg-warm-50 dark:bg-warm-950 overflow-hidden">
    <!-- Page head strip -->
    <header v-if="$slots.head" class="shrink-0 h-11 flex items-center gap-2 px-3 border-b border-warm-200 dark:border-warm-800 bg-warm-100/70 dark:bg-warm-950/70">
      <slot name="head" />
    </header>

    <!-- Tab strip (optional) -->
    <TabStrip v-if="tabs && tabs.length" :tabs="tabs" :active="activeTab" @select="$emit('tab:select', $event)" @close="$emit('tab:close', $event)" />

    <!-- 3-col body -->
    <Splitpanes class="flex-1 min-h-0" :dbl-click-splitter="false" @resized="onResize">
      <Pane :size="leftPct" :min-size="hasLeft ? minLeftPct : 0" :max-size="40">
        <div class="h-full overflow-hidden flex flex-col border-r border-warm-200 dark:border-warm-800 bg-warm-100/30 dark:bg-warm-900/30">
          <slot name="left" />
        </div>
      </Pane>

      <Pane :size="mainPct">
        <div class="h-full overflow-hidden flex flex-col">
          <slot name="main" />
        </div>
      </Pane>

      <Pane :size="rightPct" :min-size="hasRight ? minRightPct : 0" :max-size="45">
        <div class="h-full overflow-hidden flex flex-col border-l border-warm-200 dark:border-warm-800 bg-warm-100/30 dark:bg-warm-900/30">
          <slot name="right" />
        </div>
      </Pane>
    </Splitpanes>

    <!-- Status bar -->
    <footer v-if="$slots.status" class="shrink-0 h-6 flex items-center gap-2 px-3 text-[11px] text-warm-500 dark:text-warm-500 border-t border-warm-200 dark:border-warm-800 bg-warm-100/50 dark:bg-warm-950/70">
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

const props = defineProps({
  /** Stable id used to persist column widths per frame. */
  frameId: { type: String, required: true },
  /** Tabs array: [{ id, label, icon?, dirty?, pinned? }]. Empty hides the strip. */
  tabs: { type: Array, default: () => [] },
  /** Active tab id. */
  activeTab: { type: String, default: "" },
  /** Initial pixel widths; persisted across sessions. */
  defaultLeft: { type: Number, default: 220 },
  /** Initial right-column width in pixels. */
  defaultRight: { type: Number, default: 320 },
  /** Minimum widths enforced at the splitter level. */
  minLeft: { type: Number, default: 160 },
  /** Right-side minimum. */
  minRight: { type: Number, default: 220 },
})

defineEmits(["tab:select", "tab:close"])

const slots = useSlots()
const hasLeft = computed(() => !!slots.left)
const hasRight = computed(() => !!slots.right)

const ui = useStudioUiStore()

// Splitpanes is percentage-based. Convert our pixel defaults into
// a reasonable starting percentage (recomputed on mount when we know
// the container width).
const containerWidth = ref(1280)
const widths = ref({ left: props.defaultLeft, right: props.defaultRight })

onMounted(() => {
  const persisted = ui.getColumns(props.frameId, {
    left: props.defaultLeft,
    right: props.defaultRight,
  })
  widths.value = persisted
  // Try to pick up the real container width once mounted
  queueMeasure()
})

function queueMeasure() {
  requestAnimationFrame(() => {
    const el = document.querySelector(`[data-studio-frame="${props.frameId}"]`)
    if (el) containerWidth.value = el.clientWidth || 1280
  })
}

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
  // splitpanes emits an array with { size } for each pane
  if (!Array.isArray(event) || event.length !== 3) return
  const [l, , r] = event
  const w = Math.max(containerWidth.value, 400)
  const leftPx = Math.round((l.size / 100) * w)
  const rightPx = Math.round((r.size / 100) * w)
  widths.value = { left: leftPx, right: rightPx }
  ui.setColumns(props.frameId, widths.value)
}
</script>

<style scoped>
/* Slim, unobtrusive splitter — matches warm-* tones */
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
