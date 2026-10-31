<template>
  <div class="fixed inset-0 z-40" @click.self="$emit('close')" @contextmenu.prevent="$emit('close')">
    <div class="absolute z-50 w-48 bg-warm-50 dark:bg-warm-900 border border-warm-200 dark:border-warm-700 rounded shadow-lg py-1 text-xs" :style="{ left: position.x + 'px', top: position.y + 'px' }" @click.stop>
      <div class="px-3 py-1 text-warm-400 truncate text-[10px] uppercase tracking-wider border-b border-warm-200 dark:border-warm-700 mb-1">
        {{ tab.id }}
      </div>

      <button class="w-full text-left px-3 py-1.5 flex items-center gap-2 hover:bg-warm-100 dark:hover:bg-warm-800" @click="emitAndClose('refresh')"><span class="i-carbon-renew text-iolite" /> {{ t("shell.tab.refresh") }}</button>

      <button v-if="canPopOut" class="w-full text-left px-3 py-1.5 flex items-center gap-2 hover:bg-warm-100 dark:hover:bg-warm-800" data-test="tab-move-to-window" @click="moveToWindow"><span class="i-carbon-launch text-iolite" /> {{ t("shell.tab.moveToWindow") }}</button>

      <div class="my-1 border-t border-warm-200 dark:border-warm-700" />

      <button class="w-full text-left px-3 py-1.5 flex items-center gap-2" :class="isDashboard ? 'opacity-40 cursor-not-allowed' : 'hover:bg-warm-100 dark:hover:bg-warm-800'" :title="isDashboard ? t('shell.tab.dashboardLocked') : ''" :disabled="isDashboard" @click="!isDashboard && emitAndClose('closeTab')"><span class="i-carbon-close" /> {{ t("shell.tab.close") }}</button>
      <button class="w-full text-left px-3 py-1.5 flex items-center gap-2" :class="hasLeft ? 'hover:bg-warm-100 dark:hover:bg-warm-800' : 'opacity-40 cursor-not-allowed'" :disabled="!hasLeft" @click="hasLeft && emitAndClose('closeLeft')"><span class="i-carbon-arrow-left" /> {{ t("shell.tab.closeLeft") }}</button>
      <button class="w-full text-left px-3 py-1.5 flex items-center gap-2" :class="hasRight ? 'hover:bg-warm-100 dark:hover:bg-warm-800' : 'opacity-40 cursor-not-allowed'" :disabled="!hasRight" @click="hasRight && emitAndClose('closeRight')"><span class="i-carbon-arrow-right" /> {{ t("shell.tab.closeRight") }}</button>
      <button class="w-full text-left px-3 py-1.5 flex items-center gap-2" :class="hasOthers ? 'hover:bg-warm-100 dark:hover:bg-warm-800' : 'opacity-40 cursor-not-allowed'" :disabled="!hasOthers" @click="hasOthers && emitAndClose('closeOthers')"><span class="i-carbon-close-large" /> {{ t("shell.tab.closeOthers") }}</button>
      <button class="w-full text-left px-3 py-1.5 flex items-center gap-2 hover:bg-warm-100 dark:hover:bg-warm-800" @click="emitAndClose('closeAll')"><span class="i-carbon-erase" /> {{ t("shell.tab.closeAll") }}</button>

      <template v-if="hasActions">
        <div class="my-1 border-t border-warm-200 dark:border-warm-700" />
        <ActionButtons target="tab" variant="menu" :context="actionContext" @ran="emit('close')" />
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue"

import { moveTabToWindow } from "@/composables/useDesktopPopout"
import ActionButtons from "@/ext/ActionButtons.vue"
import { actionsFor } from "@/ext/actions"
import { popoutWindowsSupported } from "@/utils/hostBridge"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()

const props = defineProps({
  tab: { type: Object, required: true },
  position: { type: Object, default: () => ({ x: 0, y: 0 }) },
  /** Index of ``tab`` within ``useTabsStore().tabs`` — drives the
   *  left/right enable/disable. Computed by the parent. */
  index: { type: Number, default: -1 },
  /** Total tab count (so ``Close right`` knows when nothing's to do). */
  total: { type: Number, default: 0 },
})
const emit = defineEmits(["close", "refresh", "closeTab", "closeLeft", "closeRight", "closeOthers", "closeAll"])

const hasActions = computed(() => actionsFor("tab").length > 0)
const actionContext = () => ({ id: props.tab.id, kind: props.tab.kind || "", label: props.tab.label || "" })
const isDashboard = computed(() => props.tab.id === "dashboard")
const hasLeft = computed(() => props.index > 0)
const hasRight = computed(() => props.index >= 0 && props.index < props.total - 1)
const hasOthers = computed(() => props.total > 1)

const canPopOut = computed(() => props.tab.kind === "attach" && popoutWindowsSupported())

function moveToWindow() {
  void moveTabToWindow(props.tab)
  emit("close")
}

function emitAndClose(name) {
  emit(name)
  emit("close")
}
</script>
