<template>
  <div class="flex items-stretch border-t border-warm-200 dark:border-warm-700 bg-white dark:bg-warm-900 shrink-0" style="padding-bottom: env(safe-area-inset-bottom, 0px)">
    <button v-for="p in visiblePanels" :key="p.id" class="flex-1 flex flex-col items-center gap-0.5 py-2 min-w-0 transition-colors" :class="activeId === p.id ? 'text-iolite' : 'text-warm-400 hover:text-warm-600 dark:hover:text-warm-200'" :title="labelFor(p)" @click="$emit('select', p.id)">
      <div :class="iconFor(p.id)" class="text-lg" />
      <span class="text-[9px] leading-tight truncate w-full px-1 text-center">{{ labelFor(p) }}</span>
    </button>
    <button v-if="overflowPanels.length" ref="moreBtn" class="flex-1 flex flex-col items-center gap-0.5 py-2 min-w-0 transition-colors text-warm-400 hover:text-warm-600 dark:hover:text-warm-200" :class="overflowOpen ? 'text-iolite' : ''" @click="overflowOpen = !overflowOpen">
      <div class="i-carbon-overflow-menu-horizontal text-lg" />
      <span class="text-[9px] leading-tight">More</span>
    </button>

    <!-- Overflow popover. Constrained to 60vh + overflow-y-auto so
         long panel lists (every registered panel not in the active
         preset is here) stay scrollable on short viewports. -->
    <div v-if="overflowOpen" class="absolute right-2 z-50 mb-1 rounded-lg shadow-lg bg-white dark:bg-warm-800 border border-warm-200 dark:border-warm-700 py-1 min-w-[12rem] max-h-[60vh] overflow-y-auto" style="bottom: calc(env(safe-area-inset-bottom, 0px) + 60px)" @click.stop>
      <button v-for="p in overflowPanels" :key="p.id" class="w-full flex items-center gap-2 px-3 py-2 text-sm transition-colors" :class="activeId === p.id ? 'text-iolite bg-iolite/5' : 'text-warm-600 dark:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-700'" @click="onOverflowPick(p.id)">
        <div :class="iconFor(p.id)" class="text-base shrink-0" />
        <span class="truncate">{{ labelFor(p) }}</span>
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue"

import { useI18n } from "@/utils/i18n"

// At most this many panels appear directly in the bar. Beyond that,
// the rest collapse into an overflow menu.
const MAX_INLINE_PANELS = 5

const PANEL_ICONS = {
  chat: "i-carbon-chat",
  "status-dashboard": "i-carbon-information",
  "status-tab": "i-carbon-information",
  state: "i-carbon-notebook",
  modules: "i-carbon-grid",
  files: "i-carbon-folder",
  "file-tree": "i-carbon-folder",
  "monaco-editor": "i-carbon-code",
  "editor-status": "i-carbon-information",
  activity: "i-carbon-activity",
  settings: "i-carbon-settings",
  creatures: "i-carbon-network-4",
  canvas: "i-carbon-paint-brush",
  debug: "i-carbon-debug",
  terminal: "i-carbon-terminal",
}

const props = defineProps({
  panels: { type: Array, default: () => [] },
  activeId: { type: String, default: null },
})

const emit = defineEmits(["select"])

const { panelLabel } = useI18n()
const overflowOpen = ref(false)
const moreBtn = ref(null)

// If the active panel would otherwise live in the overflow menu, hoist
// it into the visible bar so the user always sees what's selected.
const inlinePanels = computed(() => {
  const list = props.panels.slice(0, MAX_INLINE_PANELS)
  if (props.panels.length <= MAX_INLINE_PANELS) return list
  if (list.some((p) => p.id === props.activeId)) return list
  const active = props.panels.find((p) => p.id === props.activeId)
  if (!active) return list
  return [...list.slice(0, MAX_INLINE_PANELS - 1), active]
})

const visiblePanels = computed(() => inlinePanels.value)

const overflowPanels = computed(() => {
  const inlineIds = new Set(visiblePanels.value.map((p) => p.id))
  return props.panels.filter((p) => !inlineIds.has(p.id))
})

function iconFor(id) {
  return PANEL_ICONS[id] || "i-carbon-cube-view"
}

function labelFor(p) {
  return panelLabel(p.id, p.label || p.id)
}

function onOverflowPick(id) {
  emit("select", id)
  overflowOpen.value = false
}

function onDocClick(e) {
  if (!overflowOpen.value) return
  // Close when clicking outside both the More button and the popover.
  if (moreBtn.value && (moreBtn.value === e.target || moreBtn.value.contains?.(e.target))) return
  overflowOpen.value = false
}

onMounted(() => {
  document.addEventListener("click", onDocClick)
})

onBeforeUnmount(() => {
  document.removeEventListener("click", onDocClick)
})
</script>
