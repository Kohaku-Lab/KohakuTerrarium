<template>
  <div :data-test="`project-${project.id}`">
    <div class="group flex items-center gap-1.5 px-3 py-1 cursor-pointer hover:bg-warm-300/50 dark:hover:bg-warm-700/50" :title="tooltip" data-test="project-row" @click="projects.toggleExpanded(project.id)" @contextmenu.prevent="openMenu">
      <span class="shrink-0 text-[10px] text-warm-500" :class="expanded ? 'i-carbon-chevron-down' : 'i-carbon-chevron-right'" />
      <span class="shrink-0" :class="[icon, project.exists === false ? 'text-coral' : 'text-warm-600 dark:text-warm-400']" />
      <span class="flex-1 min-w-0 truncate kt-text-body" :class="project.hidden ? 'text-warm-500 italic' : 'text-warm-800 dark:text-warm-200'" data-test="project-name">{{ project.name }}</span>
      <span v-if="project.pinned" class="i-carbon-pin-filled shrink-0 text-[10px] text-warm-500" data-test="project-pinned" />
      <span v-if="project.running_count" class="shrink-0 flex items-center gap-1 text-[10px] text-iolite" :title="t('lab.rail.projects.running', { n: project.running_count })" data-test="project-running"><span class="w-1.5 h-1.5 rounded-full bg-iolite" />{{ project.running_count }}</span>
      <span class="shrink-0 kt-text-caption text-warm-500 tabular-nums group-hover:hidden" :title="t('lab.rail.projects.sessions', { n: project.session_count })" data-test="project-count">{{ project.session_count }}</span>
      <button v-if="project.hidden" type="button" class="shrink-0 text-[10px] px-1 rounded text-iolite hover:bg-iolite/10" data-test="project-unhide" @click.stop="projects.unhide(project.id)">{{ t("lab.rail.projects.unhide") }}</button>
      <button type="button" class="shrink-0 w-4 h-4 hidden group-hover:flex items-center justify-center rounded text-warm-500 hover:text-warm-800 dark:hover:text-warm-200" :title="t('lab.rail.projects.more')" data-test="project-more" @click.stop="openMenu"><span class="i-carbon-overflow-menu-horizontal" /></button>
    </div>

    <div v-if="expanded" class="flex flex-col pb-1" data-test="project-sessions">
      <div v-if="entry?.error && !entry.rows.length" class="pl-8 pr-3 py-1 text-[11px] text-coral" data-test="project-sessions-error">{{ t("lab.rail.projects.loadFailed") }}</div>
      <div v-else-if="!entry?.loaded" class="pl-8 pr-3 py-1 text-[11px] text-warm-500 italic">{{ t("sessions.loading") }}</div>
      <div v-else-if="!entry.rows.length" class="pl-8 pr-3 py-1 text-[11px] text-warm-500 italic" data-test="project-sessions-empty">{{ t("lab.rail.projects.noSessions") }}</div>
      <RailProjectSession v-for="s in entry?.rows || []" :key="s.name" :session="s" />
      <button v-if="entry?.loaded && entry.rows.length" type="button" class="pl-8 pr-3 py-1 text-left text-[11px] text-iolite hover:underline" data-test="project-show-all" @click="openHistory">{{ t("lab.rail.projects.showAll", { n: entry.total }) }}</button>
    </div>

    <Teleport to="body">
      <div v-if="menuAt" class="fixed inset-0 z-40" @click="menuAt = null" @contextmenu.prevent="menuAt = null">
        <div class="absolute z-50 w-52 bg-warm-50 dark:bg-warm-900 border border-warm-200 dark:border-warm-700 rounded shadow-lg py-1 text-xs" :style="{ left: `${menuAt.x}px`, top: `${menuAt.y}px` }" data-test="project-menu" @click.stop>
          <div class="px-3 py-1 text-warm-400 truncate text-[10px] uppercase tracking-wider border-b border-warm-200 dark:border-warm-700 mb-1">{{ project.name }}</div>
          <template v-for="(item, i) in menuItems" :key="item.id || `sep-${i}`">
            <div v-if="item.separator" class="border-t border-warm-200 dark:border-warm-700 my-1" />
            <button v-else type="button" class="w-full text-left px-3 py-1.5 flex items-center gap-2 hover:bg-warm-100 dark:hover:bg-warm-800 disabled:opacity-50 disabled:cursor-not-allowed" :class="item.id === 'forget' ? 'text-coral' : ''" :disabled="item.enabled === false" :title="item.hint || ''" :data-test="`project-action-${item.id}`" @click="choose(item.id)"><span :class="MENU_ICONS[item.id]" />{{ item.label }}</button>
          </template>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<script setup>
import { computed, ref, watch } from "vue"
import { ElMessageBox } from "element-plus"

import RailProjectSession from "@/components/shell/rail/RailProjectSession.vue"
import { newHereBlocker, projectMenuItems } from "@/components/shell/rail/projectMenu"
import { isRemoteProject, useProjectsStore } from "@/stores/projects"
import { useTabsStore } from "@/stores/tabs"
import { useI18n } from "@/utils/i18n"
import { nativeMenuSupported, showNativeMenu } from "@/utils/nativeMenu"

/**
 * One project in the sidebar: its folder, running and session counts, and,
 * when open, its newest sessions with a way to the rest in History. Its menu
 * (right click or the hover button) starts a session there, renames, pins,
 * hides or forgets it. `new-session` asks the section for the dialog.
 */
const props = defineProps({ project: { type: Object, required: true } })
const emit = defineEmits(["new-session"])
const MENU_ICONS = {
  "new-session": "i-carbon-add-large",
  "open-history": "i-carbon-recently-viewed",
  rename: "i-carbon-edit",
  pin: "i-carbon-pin",
  unpin: "i-carbon-pin-filled",
  hide: "i-carbon-view-off",
  unhide: "i-carbon-view",
  forget: "i-carbon-trash-can",
}

const { t } = useI18n()
const projects = useProjectsStore()
const tabs = useTabsStore()
const menuAt = ref(null)

const expanded = computed(() => projects.isExpanded(props.project.id))
const entry = computed(() => projects.sessions[props.project.id] || null)
const menuItems = computed(() => projectMenuItems(props.project, t))
const icon = computed(() => {
  if (props.project.exists === false) return "i-carbon-folder-off"
  if (isRemoteProject(props.project)) return "i-carbon-cloud"
  return expanded.value ? "i-carbon-folder-open" : "i-carbon-folder"
})
const tooltip = computed(() => {
  const lines = [props.project.path]
  if (props.project.node) lines.push(t("lab.rail.projects.onNode", { node: props.project.node }))
  if (props.project.exists === false) lines.push(t("lab.rail.projects.missing"))
  return lines.join("\n")
})

watch(
  [expanded, entry],
  ([open, cached]) => {
    if (open && !cached) void projects.loadSessions(props.project.id)
  },
  { immediate: true },
)

async function openMenu(event) {
  if (nativeMenuSupported()) {
    const items = menuItems.value.map((item) => (item.separator ? item : { id: item.id, label: item.label, enabled: item.enabled }))
    if (await showNativeMenu(items, choose)) return
  }
  const x = Math.min(event?.clientX ?? 60, (window.innerWidth || 1024) - 216)
  const y = Math.min(event?.clientY ?? 100, (window.innerHeight || 768) - 240)
  menuAt.value = { x: Math.max(4, x), y: Math.max(4, y) }
}

function openHistory() {
  tabs.openTab({ kind: "saved-sessions", id: "saved-sessions", project: props.project.id })
}

async function rename() {
  let answer
  try {
    answer = await ElMessageBox.prompt(t("lab.rail.projects.renamePrompt"), t("lab.rail.projects.rename"), {
      inputValue: props.project.name || "",
      confirmButtonText: t("common.save"),
      cancelButtonText: t("common.cancel"),
    })
  } catch {
    return
  }
  await projects.rename(props.project.id, answer?.value ?? "")
}

function choose(id) {
  menuAt.value = null
  switch (id) {
    case "new-session":
      if (!newHereBlocker(props.project, t)) emit("new-session", props.project)
      return
    case "open-history":
      return openHistory()
    case "rename":
      return void rename()
    case "pin":
      return void projects.setPinned(props.project.id, true)
    case "unpin":
      return void projects.setPinned(props.project.id, false)
    case "hide":
      return void projects.hide(props.project.id)
    case "unhide":
      return void projects.unhide(props.project.id)
    case "forget":
      return void projects.forget(props.project.id)
  }
}
</script>
