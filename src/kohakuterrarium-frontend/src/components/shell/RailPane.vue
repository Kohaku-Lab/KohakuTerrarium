<template>
  <div class="relative h-full shrink-0" :class="collapsible ? '' : 'w-full'" :style="collapsible ? { width: (isCollapsed ? COLLAPSED_RAIL_WIDTH : width) + 'px' } : null">
    <nav v-if="isCollapsed" class="h-full flex flex-col items-center gap-1 py-3 border-r border-warm-200 dark:border-warm-700 bg-[var(--kt-shell-rail)] dark:bg-warm-800/40 overflow-hidden" data-test="rail-collapsed">
      <button class="shrink-0 rounded-full" :title="t('shell.rail.expand')" @click="toggleCollapsed">
        <BrandMark class="w-7 h-7 rounded-full" />
      </button>
      <button class="shrink-0 w-8 h-8 flex items-center justify-center rounded-md text-warm-600 dark:text-warm-500 hover:text-warm-800 dark:hover:text-warm-200 hover:bg-warm-300/50 dark:hover:bg-warm-700/50" :title="t('shell.rail.expand')" data-test="rail-expand" @click="toggleCollapsed"><span class="i-carbon-side-panel-open" /></button>
      <div class="w-6 my-1 shrink-0 border-t border-warm-300 dark:border-warm-700" />
      <button class="shrink-0 w-8 h-8 flex items-center justify-center rounded-md text-warm-600 dark:text-warm-500 hover:text-warm-800 dark:hover:text-warm-200 hover:bg-warm-300/50 dark:hover:bg-warm-700/50" :title="t('shell.rail.commandPalette')" @click="openPalette"><span class="i-carbon-search" /></button>
      <button class="shrink-0 w-8 h-8 flex items-center justify-center rounded-md bg-iolite text-white hover:bg-iolite-shadow" :title="t('lab.new.title')" data-test="rail-strip-new" @click="newOpen = true"><span class="i-carbon-add-large" /></button>
      <button v-for="item in STRIP" :key="item.id" class="shrink-0 w-8 h-8 flex items-center justify-center rounded-md hover:bg-warm-300/50 dark:hover:bg-warm-700/50" :class="tabs.activeId === item.tab.id ? 'text-iolite' : 'text-warm-600 dark:text-warm-500 hover:text-warm-800 dark:hover:text-warm-200'" :title="t(item.label)" @click="tabs.openTab(item.tab)"><span :class="item.icon" /></button>
      <button class="shrink-0 w-8 h-8 flex items-center justify-center rounded-md text-warm-600 dark:text-warm-500 hover:text-warm-800 dark:hover:text-warm-200 hover:bg-warm-300/50 dark:hover:bg-warm-700/50" :title="t('lab.rail.studio')" @click="openStudio"><span class="i-carbon-tool-box" /></button>
      <span class="flex-1" />
      <button class="shrink-0 w-8 h-8 flex items-center justify-center rounded-md text-warm-600 dark:text-warm-500 hover:text-warm-800 dark:hover:text-warm-200 hover:bg-warm-300/50 dark:hover:bg-warm-700/50" :title="t('shell.quick.settings')" @click="tabs.openTab({ kind: 'settings', id: 'settings' })"><span class="i-carbon-settings" /></button>
      <button class="shrink-0 w-8 h-8 flex items-center justify-center rounded-md text-warm-600 dark:text-warm-400 hover:text-warm-800 dark:hover:text-warm-200" :title="theme.dark ? t('shell.rail.themeToLight') : t('shell.rail.themeToDark')" @click="theme.toggle()"><span :class="theme.dark ? 'i-carbon-sun' : 'i-carbon-moon'" /></button>
      <button class="shrink-0 text-[10px] uppercase tracking-wider text-warm-600 dark:text-warm-400 hover:text-warm-800 dark:hover:text-warm-200 py-1" :title="t('shell.rail.cycleLocale')" @click="cycleLocale">{{ locale.current ?? "en" }}</button>
    </nav>
    <nav v-else class="h-full flex flex-col border-r border-warm-200 dark:border-warm-700 bg-[var(--kt-shell-rail)] dark:bg-warm-800/40 overflow-hidden">
      <!-- Brand + cluster pill + command palette trigger -->
      <div class="relative flex shrink-0 items-center gap-2 px-3 py-3">
        <BrandMark class="w-7 h-7 rounded-full shrink-0" />
        <span class="kt-text-body flex-1 truncate">
          <span class="font-bold text-amber">Kohaku</span>
          <span class="font-light text-iolite dark:text-iolite-light">Terrarium</span>
        </span>
        <SitePill data-test="cluster-pill" @click.stop="togglePopover" />
        <SitePopover :open="popoverOpen" @close="popoverOpen = false" />
        <button class="i-carbon-search w-4 h-4 text-warm-600 dark:text-warm-400 hover:text-warm-800 dark:hover:text-warm-200" :title="t('shell.rail.commandPalette')" @click="openPalette" />
        <button v-if="collapsible" class="i-carbon-side-panel-close w-4 h-4 text-warm-600 dark:text-warm-400 hover:text-warm-800 dark:hover:text-warm-200" :title="t('shell.rail.collapse')" data-test="rail-collapse" @click="toggleCollapsed" />
      </div>
      <RailAppSwitch class="shrink-0" />

      <div class="flex-1 min-h-0 overflow-y-auto">
        <RailNav />

        <div class="mx-2 mt-1 border-t border-warm-200 dark:border-warm-700" />

        <RailGroupAttached />

        <div class="mx-2 mt-1 border-t border-warm-200 dark:border-warm-700" />

        <RailGroupPinned />
      </div>

      <!-- Footer -->
      <div class="mx-2 shrink-0 border-t border-warm-200 dark:border-warm-700" />
      <div class="flex shrink-0 items-center gap-1 px-2 pt-1.5">
        <button class="flex-1 min-w-0 flex items-center gap-2 px-1.5 py-1 rounded-md kt-text-body text-left hover:bg-warm-300/50 dark:hover:bg-warm-700/50" :class="tabs.activeId === 'settings' ? 'text-warm-800 dark:text-warm-200 font-medium' : 'text-warm-600 dark:text-warm-400'" data-test="rail-settings" @click="tabs.openTab({ kind: 'settings', id: 'settings' })"><span class="i-carbon-settings shrink-0" />{{ t("shell.quick.settings") }}</button>
        <button v-if="auth.isAdmin" class="w-7 h-7 flex items-center justify-center rounded-md text-warm-600 dark:text-warm-400 hover:bg-warm-300/50 dark:hover:bg-warm-700/50" :title="t('shell.quick.admin')" data-test="rail-admin" @click="tabs.openTab({ kind: 'admin', id: 'admin' })"><span class="i-carbon-user-admin" /></button>
      </div>
      <div class="flex shrink-0 items-center justify-between gap-2 px-3 py-1.5">
        <!-- Host-picker chip — clickable indicator of which backend
             we're talking to, opens the modal to add / switch hosts. -->
        <HostStatusChip :show-label="true" @open="openHostPicker" />
      </div>
      <div class="flex shrink-0 items-center justify-between gap-2 px-3 py-2">
        <button class="w-9 h-9 sm:w-5 sm:h-5 flex items-center justify-center text-warm-600 dark:text-warm-400 hover:text-warm-800 dark:hover:text-warm-200 rounded sm:rounded-none" :class="theme.dark ? 'i-carbon-sun' : 'i-carbon-moon'" :title="theme.dark ? t('shell.rail.themeToLight') : t('shell.rail.themeToDark')" @click="theme.toggle()" />
        <button class="text-xs sm:text-[10px] uppercase tracking-wider text-warm-600 dark:text-warm-400 hover:text-warm-800 dark:hover:text-warm-200 px-2 py-1 rounded" :title="t('shell.rail.cycleLocale')" @click="cycleLocale">
          {{ locale.current ?? "en" }}
        </button>
      </div>
    </nav>

    <NewSessionDialog v-if="newOpen" @close="newOpen = false" />

    <!-- Drag handle — 4px column on the right edge for resize. -->
    <div v-if="collapsible && !isCollapsed" class="absolute top-0 right-0 h-full w-[4px] cursor-col-resize hover:bg-iolite/30" :class="dragging ? 'bg-iolite/50' : ''" :title="'Drag to resize · double-click to reset'" @pointerdown="startDrag" @dblclick="resetWidth" />
  </div>
</template>

<script setup>
import { computed, ref } from "vue"

import HostStatusChip from "@/components/host-picker/HostStatusChip.vue"
import BrandMark from "@/components/shell/BrandMark.vue"
import RailGroupAttached from "@/components/shell/RailGroupAttached.vue"
import RailGroupPinned from "@/components/shell/RailGroupPinned.vue"
import NewSessionDialog from "@/components/shell/newSession/NewSessionDialog.vue"
import RailAppSwitch from "@/components/shell/rail/RailAppSwitch.vue"
import RailNav from "@/components/shell/rail/RailNav.vue"
import { useOpenStudio } from "@/components/shell/rail/useOpenStudio"
import SitePill from "@/components/cluster/SitePill.vue"
import SitePopover from "@/components/cluster/SitePopover.vue"
import { COLLAPSED_RAIL_WIDTH, useRailWidth } from "@/composables/useRailWidth"
import { useAuthStore } from "@/stores/auth"
import { useThemeStore } from "@/stores/theme"
import { useLocaleStore } from "@/stores/locale"
import { usePaletteStore } from "@/stores/palette"
import { useTabsStore } from "@/stores/tabs"
import { useI18n } from "@/utils/i18n"

/** The shell's left rail. `collapsible: false` (the phone drawer) always shows the full rail at its container's width. */
const props = defineProps({ collapsible: { type: Boolean, default: true } })

const theme = useThemeStore()
const locale = useLocaleStore()
const palette = usePaletteStore()
const tabs = useTabsStore()
const auth = useAuthStore()
const openStudio = useOpenStudio()
const { t } = useI18n()
const { width, collapsed, dragging, startDrag, resetWidth, toggleCollapsed } = useRailWidth()
const isCollapsed = computed(() => props.collapsible && collapsed.value)
const popoverOpen = ref(false)
const newOpen = ref(false)
const STRIP = [
  { id: "lab", label: "lab.rail.lab", icon: "i-carbon-home", tab: { kind: "dashboard", id: "dashboard" } },
  { id: "history", label: "lab.history.title", icon: "i-carbon-recently-viewed", tab: { kind: "saved-sessions", id: "saved-sessions" } },
  { id: "library", label: "lab.library.title", icon: "i-carbon-catalog", tab: { kind: "catalog", id: "catalog" } },
]

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
