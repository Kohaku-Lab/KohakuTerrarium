<template>
  <el-dialog
    v-model="open"
    :show-close="false"
    :close-on-click-modal="true"
    width="520px"
    class="command-palette-dialog"
    align-center
    @close="onClose"
  >
    <template #header>
      <div class="flex items-center gap-2">
        <div class="i-carbon-search text-warm-400" />
        <input
          ref="inputEl"
          v-model="query"
          class="flex-1 bg-transparent outline-none text-sm text-warm-700 dark:text-warm-300"
          placeholder="Type a command…  (> @ # /)"
          @keydown="onKey"
        />
        <span class="text-[10px] text-warm-400 uppercase tracking-wider font-mono">
          {{ palette.parsed.prefix }}
        </span>
      </div>
    </template>

    <div class="max-h-80 overflow-y-auto text-xs">
      <button
        v-for="(r, idx) in results"
        :key="r.id"
        class="flex items-center gap-3 w-full px-3 py-2 text-left rounded transition-colors"
        :class="
          idx === highlighted
            ? 'bg-iolite/10 text-iolite'
            : 'text-warm-600 dark:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800'
        "
        @click="commit(r)"
        @mouseenter="highlighted = idx"
      >
        <div v-if="r.icon" :class="r.icon" class="text-[14px] shrink-0" />
        <div class="flex-1 min-w-0">
          <div class="font-medium truncate">{{ r.label }}</div>
          <div v-if="r.description" class="text-[9px] text-warm-400 truncate">
            {{ r.description }}
          </div>
        </div>
        <span v-if="r.shortcut" class="text-[9px] font-mono text-warm-400 shrink-0">{{
          r.shortcut
        }}</span>
      </button>
      <div v-if="results.length === 0" class="text-warm-400 text-center py-6 italic">
        No matches
      </div>
    </div>
  </el-dialog>
</template>

<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue"

import { usePaletteStore } from "@/stores/palette"
import { LAYOUT_EVENTS, onLayoutEvent } from "@/utils/layoutEvents"

const palette = usePaletteStore()

const open = computed({
  get: () => palette.open,
  set: (v) => (v ? palette.openPalette(palette.query) : palette.closePalette()),
})
const query = computed({
  get: () => palette.query,
  set: (v) => (palette.query = v),
})

const results = computed(() => palette.results)
const highlighted = ref(0)
const inputEl = ref(null)

watch(
  () => results.value,
  () => {
    highlighted.value = 0
  },
)

function commit(entry) {
  if (!entry) return
  palette.run(entry.id)
}

function onKey(e) {
  if (e.key === "ArrowDown") {
    e.preventDefault()
    if (results.value.length) {
      highlighted.value = (highlighted.value + 1) % results.value.length
    }
  } else if (e.key === "ArrowUp") {
    e.preventDefault()
    if (results.value.length) {
      highlighted.value = (highlighted.value - 1 + results.value.length) % results.value.length
    }
  } else if (e.key === "Enter") {
    e.preventDefault()
    const r = results.value[highlighted.value]
    if (r) commit(r)
  } else if (e.key === "Escape") {
    e.preventDefault()
    palette.closePalette()
  }
}

function onClose() {
  palette.closePalette()
}

// Focus the input when the palette opens.
watch(
  () => palette.open,
  async (v) => {
    if (v) {
      await nextTick()
      inputEl.value?.focus()
    }
  },
)

// Listen for Ctrl+K / palette:open events from the keyboard composable.
let unsub = () => {}
onMounted(() => {
  unsub = onLayoutEvent(LAYOUT_EVENTS.PALETTE_OPEN, () => {
    palette.openPalette("")
  })
})
onUnmounted(() => {
  unsub()
})
</script>

<style>
.command-palette-dialog .el-dialog__header {
  padding: 12px 16px 8px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  margin: 0;
}
.command-palette-dialog .el-dialog__body {
  padding: 8px;
}
</style>
