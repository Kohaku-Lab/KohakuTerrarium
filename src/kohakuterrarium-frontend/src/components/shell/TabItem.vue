<template>
  <div class="group h-8 flex items-center gap-1.5 pl-3 pr-1.5 text-xs border-r border-warm-200 dark:border-warm-700 cursor-pointer select-none shrink-0" :class="active ? 'bg-warm-50 dark:bg-warm-950 text-warm-800 dark:text-warm-200 border-b-2 border-b-iolite' : 'text-warm-500 hover:bg-warm-200/40 dark:hover:bg-warm-800/40'" :draggable="true" @click="$emit('activate')" @mousedown.middle.prevent="onMiddleClick" @dragstart="onDragStart" @dragover.prevent @drop="$emit('drop', $event)" @contextmenu.prevent="onContextMenu">
    <!-- Pinned indicator. Dashboard's kind icon is already a house, so
         we don't render an extra one beside it; the kind icon below
         is enough on its own. -->
    <span v-if="isPinned && !isDashboard" class="i-carbon-pin-filled text-iolite text-xs shrink-0" />
    <span :class="[iconClass, isDashboard ? 'text-iolite' : '']" class="text-sm shrink-0" />
    <span class="truncate max-w-32">{{ label }}</span>
    <button class="i-carbon-renew ml-1 opacity-0 group-hover:opacity-100 hover:text-iolite" :title="t('shell.tab.refreshTip')" @click.stop="onRefresh" />
    <button v-if="!isDashboard" class="i-carbon-close opacity-0 group-hover:opacity-100 hover:text-warm-700" :title="t('shell.tab.closeTab')" @click.stop="$emit('close')" />
  </div>

  <TabContextMenu v-if="menuOpen" :tab="tab" :is-pinned="isPinned" :position="menuPos" :index="tabIndex" :total="totalTabs" @close="menuOpen = false" @refresh="onRefresh" @toggle-pin="togglePin" @close-tab="$emit('close')" @close-left="closeLeft" @close-right="closeRight" @close-others="closeOthers" @close-all="closeAll" />
</template>

<script setup>
import { computed, ref } from "vue"

import TabContextMenu from "@/components/shell/TabContextMenu.vue"
import { useTabsStore } from "@/stores/tabs"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()

const props = defineProps({
  tab: { type: Object, required: true },
  active: { type: Boolean, default: false },
})
const emit = defineEmits(["activate", "close", "drop"])

const tabs = useTabsStore()
const menuOpen = ref(false)
const menuPos = ref({ x: 0, y: 0 })

const isPinned = computed(() => tabs.pinnedIds.has(props.tab.id))
const isDashboard = computed(() => props.tab.id === "dashboard")
const tabIndex = computed(() => tabs.tabs.findIndex((t) => t.id === props.tab.id))
const totalTabs = computed(() => tabs.tabs.length)

function onDragStart(ev) {
  ev.dataTransfer.effectAllowed = "move"
  ev.dataTransfer.setData("text/plain", props.tab.id)
}

function onContextMenu(ev) {
  menuPos.value = { x: ev.clientX, y: ev.clientY }
  menuOpen.value = true
}

function onMiddleClick() {
  // Dashboard never closes. Other tabs use middle-click as a quick
  // close gesture (matches every browser tab strip).
  if (isDashboard.value) return
  emit("close")
}

function togglePin() {
  if (isDashboard.value) return // dashboard is implicitly always-pinned
  if (isPinned.value) tabs.unpinTab(props.tab.id)
  else tabs.pinTab(props.tab.id)
}

function onRefresh() {
  tabs.refreshTab(props.tab.id)
}

function closeLeft() {
  tabs.closeLeft(props.tab.id)
}

function closeRight() {
  tabs.closeRight(props.tab.id)
}

function closeOthers() {
  tabs.closeOthers(props.tab.id)
}

function closeAll() {
  tabs.closeAll()
}

const iconClass = computed(
  () =>
    ({
      dashboard: "i-carbon-home",
      attach: "i-carbon-chat",
      inspector: "i-carbon-radar",
      "session-viewer": "i-carbon-recently-viewed",
      "saved-sessions": "i-carbon-list",
      "studio-editor": "i-carbon-tool-box",
      catalog: "i-carbon-catalog",
      settings: "i-carbon-settings",
      "code-editor": "i-carbon-code",
    })[props.tab.kind] ?? "i-carbon-circle",
)

const label = computed(() => {
  // Tab labels mix UI chrome ("Dashboard", "Catalog", section
  // suffixes like "watch" / "creature") with user data (config_name,
  // session name, entity slug). The chrome bits go through i18n; the
  // user data stays raw because translating "alice" makes no sense.
  const tab = props.tab
  switch (tab.kind) {
    case "dashboard":
      return t("shell.rail.dashboard")
    case "attach":
      return tab.config_name ?? tab.target ?? "attach"
    case "inspector":
      return `${tab.config_name ?? tab.target ?? "agent"} · ${t("shell.tab.suffix.watch")}`
    case "session-viewer":
      return tab.name ?? "session"
    case "saved-sessions":
      return t("shell.rail.savedSessions")
    case "studio-editor":
      if (tab.entityKind === "creature") return `${tab.entity} · ${t("shell.tab.suffix.creature")}`
      if (tab.entityKind === "module") return `${tab.entity} · ${t("shell.tab.suffix.module")}`
      if (tab.entityKind === "workspace") return `${tab.workspace} · ${t("shell.tab.suffix.workspace")}`
      return t("shell.quick.studio")
    case "catalog":
      return t("shell.quick.catalog")
    case "settings":
      return t("shell.quick.settings")
    case "code-editor":
      return tab.slug ?? "editor"
    default:
      return tab.kind
  }
})
</script>
