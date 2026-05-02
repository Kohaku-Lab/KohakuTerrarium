<template>
  <div class="fixed inset-0 z-40" @click.self="$emit('close')" @contextmenu.prevent="$emit('close')">
    <div class="absolute z-50 w-48 bg-warm-50 dark:bg-warm-900 border border-warm-200 dark:border-warm-700 rounded shadow-lg py-1 text-xs" :style="{ left: position.x + 'px', top: position.y + 'px' }" @click.stop>
      <div class="px-3 py-1 text-warm-400 truncate text-[10px] uppercase tracking-wider border-b border-warm-200 dark:border-warm-700 mb-1">
        {{ tab.id }}
      </div>

      <button class="w-full text-left px-3 py-1.5 flex items-center gap-2 hover:bg-warm-100 dark:hover:bg-warm-800" @click="emitAndClose('refresh')"><span class="i-carbon-renew text-iolite" /> {{ t("shell.tab.refresh") }}</button>

      <div class="my-1 border-t border-warm-200 dark:border-warm-700" />

      <button class="w-full text-left px-3 py-1.5 flex items-center gap-2" :class="isDashboard ? 'opacity-40 cursor-not-allowed' : 'hover:bg-warm-100 dark:hover:bg-warm-800'" :title="isDashboard ? t('shell.tab.dashboardPinned') : ''" :disabled="isDashboard" @click="!isDashboard && emitAndClose('togglePin')">
        <span :class="isPinned ? 'i-carbon-pin-filled text-iolite' : 'i-carbon-pin'" />
        {{ isPinned ? t("shell.tab.unpin") : t("shell.tab.pin") }}
      </button>

      <div class="my-1 border-t border-warm-200 dark:border-warm-700" />

      <button class="w-full text-left px-3 py-1.5 flex items-center gap-2" :class="isDashboard ? 'opacity-40 cursor-not-allowed' : 'hover:bg-warm-100 dark:hover:bg-warm-800'" :title="isDashboard ? t('shell.tab.dashboardLocked') : ''" :disabled="isDashboard" @click="!isDashboard && emitAndClose('closeTab')"><span class="i-carbon-close" /> {{ t("shell.tab.close") }}</button>
      <button class="w-full text-left px-3 py-1.5 flex items-center gap-2" :class="hasLeft ? 'hover:bg-warm-100 dark:hover:bg-warm-800' : 'opacity-40 cursor-not-allowed'" :disabled="!hasLeft" @click="hasLeft && emitAndClose('closeLeft')"><span class="i-carbon-arrow-left" /> {{ t("shell.tab.closeLeft") }}</button>
      <button class="w-full text-left px-3 py-1.5 flex items-center gap-2" :class="hasRight ? 'hover:bg-warm-100 dark:hover:bg-warm-800' : 'opacity-40 cursor-not-allowed'" :disabled="!hasRight" @click="hasRight && emitAndClose('closeRight')"><span class="i-carbon-arrow-right" /> {{ t("shell.tab.closeRight") }}</button>
      <button class="w-full text-left px-3 py-1.5 flex items-center gap-2" :class="hasOthers ? 'hover:bg-warm-100 dark:hover:bg-warm-800' : 'opacity-40 cursor-not-allowed'" :disabled="!hasOthers" @click="hasOthers && emitAndClose('closeOthers')"><span class="i-carbon-close-large" /> {{ t("shell.tab.closeOthers") }}</button>
      <button class="w-full text-left px-3 py-1.5 flex items-center gap-2 hover:bg-warm-100 dark:hover:bg-warm-800" @click="emitAndClose('closeAll')"><span class="i-carbon-erase" /> {{ t("shell.tab.closeAll") }}</button>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue"

import { useI18n } from "@/utils/i18n"

const { t } = useI18n()

const props = defineProps({
  tab: { type: Object, required: true },
  isPinned: { type: Boolean, default: false },
  position: { type: Object, default: () => ({ x: 0, y: 0 }) },
  /** Index of ``tab`` within ``useTabsStore().tabs`` — drives the
   *  left/right enable/disable. Computed by the parent. */
  index: { type: Number, default: -1 },
  /** Total tab count (so ``Close right`` knows when nothing's to do). */
  total: { type: Number, default: 0 },
})
const emit = defineEmits(["close", "refresh", "togglePin", "closeTab", "closeLeft", "closeRight", "closeOthers", "closeAll"])

const isDashboard = computed(() => props.tab.id === "dashboard")
const hasLeft = computed(() => props.index > 0)
const hasRight = computed(() => props.index >= 0 && props.index < props.total - 1)
const hasOthers = computed(() => props.total > 1)

function emitAndClose(name) {
  emit(name)
  emit("close")
}
</script>
