<template>
  <div class="relative h-full shrink-0" :style="{ width: width + 'px' }">
    <nav class="h-full flex flex-col border-r border-warm-200 dark:border-warm-700 bg-warm-200/50 dark:bg-warm-800/40 overflow-hidden">
      <!-- Brand + cluster pill + command palette trigger -->
      <div class="relative flex items-center gap-2 px-3 py-3">
        <BrandMark class="w-7 h-7 rounded-full shrink-0" />
        <span class="kt-text-body flex-1 truncate">
          <span class="font-bold text-amber">Kohaku</span>
          <span class="font-light text-iolite-light dark:text-iolite-light">Terrarium</span>
        </span>
        <SitePill data-test="cluster-pill" @click.stop="togglePopover" />
        <SitePopover :open="popoverOpen" @close="popoverOpen = false" />
        <button class="i-carbon-search w-4 h-4 text-warm-400 hover:text-warm-700" :title="t('shell.rail.commandPalette')" @click="openPalette" />
      </div>
      <div class="mx-2 border-t border-warm-200 dark:border-warm-700" />

      <!-- Group: Top — Dashboard above Attached -->
      <RailGroupTop />

      <div class="mx-2 mt-1 border-t border-warm-200 dark:border-warm-700" />

      <!-- Group: Attached -->
      <RailGroupAttached />

      <div class="mx-2 mt-1 border-t border-warm-200 dark:border-warm-700" />

      <!-- Group: Quick -->
      <RailGroupQuick />

      <div class="mx-2 mt-1 border-t border-warm-200 dark:border-warm-700" />

      <!-- Group: Pinned -->
      <div class="flex-1 overflow-y-auto">
        <RailGroupPinned />
      </div>

      <!-- Footer -->
      <div class="mx-2 border-t border-warm-200 dark:border-warm-700" />
      <div class="flex items-center justify-between gap-2 px-3 py-1.5">
        <!-- Host-picker chip — clickable indicator of which backend
             we're talking to, opens the modal to add / switch hosts. -->
        <HostStatusChip :show-label="true" @open="openHostPicker" />
      </div>
      <div class="flex items-center justify-between gap-2 px-3 py-2">
        <button class="w-9 h-9 sm:w-5 sm:h-5 flex items-center justify-center text-warm-400 hover:text-warm-700 rounded sm:rounded-none" :class="theme.dark ? 'i-carbon-sun' : 'i-carbon-moon'" :title="theme.dark ? t('shell.rail.themeToLight') : t('shell.rail.themeToDark')" @click="theme.toggle()" />
        <button class="text-xs sm:text-[10px] uppercase tracking-wider text-warm-400 hover:text-warm-700 px-2 py-1 rounded" :title="t('shell.rail.cycleLocale')" @click="cycleLocale">
          {{ locale.current ?? "en" }}
        </button>
      </div>
    </nav>

    <!-- Drag handle — 4px column on the right edge for resize. -->
    <div class="absolute top-0 right-0 h-full w-[4px] cursor-col-resize hover:bg-iolite/30" :class="dragging ? 'bg-iolite/50' : ''" :title="'Drag to resize · double-click to reset'" @pointerdown="startDrag" @dblclick="resetWidth" />
  </div>
</template>

<script setup>
import { ref } from "vue"

import HostStatusChip from "@/components/host-picker/HostStatusChip.vue"
import BrandMark from "@/components/shell/BrandMark.vue"
import RailGroupTop from "@/components/shell/RailGroupTop.vue"
import RailGroupAttached from "@/components/shell/RailGroupAttached.vue"
import RailGroupQuick from "@/components/shell/RailGroupQuick.vue"
import RailGroupPinned from "@/components/shell/RailGroupPinned.vue"
import SitePill from "@/components/cluster/SitePill.vue"
import SitePopover from "@/components/cluster/SitePopover.vue"
import { useRailWidth } from "@/composables/useRailWidth"
import { useThemeStore } from "@/stores/theme"
import { useLocaleStore } from "@/stores/locale"
import { usePaletteStore } from "@/stores/palette"
import { useI18n } from "@/utils/i18n"

const theme = useThemeStore()
const locale = useLocaleStore()
const palette = usePaletteStore()
const { t } = useI18n()
const { width, dragging, startDrag, resetWidth } = useRailWidth()
const popoverOpen = ref(false)

function togglePopover() {
  popoverOpen.value = !popoverOpen.value
}

function openPalette() {
  // Palette store exposes openPalette / closePalette / toggle.
  if (typeof palette.openPalette === "function") palette.openPalette()
  else if (typeof palette.toggle === "function") palette.toggle()
}

function openHostPicker() {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event("kt-open-host-picker"))
  }
}

function cycleLocale() {
  if (typeof locale.cycle === "function") locale.cycle()
  else if (typeof locale.toggle === "function") locale.toggle()
}
</script>
