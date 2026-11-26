<template>
  <div class="pb-1" data-test="rail-projects">
    <div class="flex items-center gap-1 px-3 py-1">
      <span class="flex-1 kt-text-caption uppercase tracking-wider text-warm-600 dark:text-warm-500 font-medium">{{ t("lab.rail.projects.title") }}</span>
      <button type="button" class="w-5 h-5 flex items-center justify-center rounded text-warm-500 hover:text-warm-800 dark:hover:text-warm-200 hover:bg-warm-300/50 dark:hover:bg-warm-700/50" :class="projects.includeHidden ? 'text-iolite' : ''" :aria-pressed="projects.includeHidden" :title="projects.includeHidden ? t('lab.rail.projects.hideHidden') : t('lab.rail.projects.showHidden')" data-test="projects-show-hidden" @click="projects.setIncludeHidden(!projects.includeHidden)"><span :class="projects.includeHidden ? 'i-carbon-view' : 'i-carbon-view-off'" /></button>
      <button type="button" class="w-5 h-5 flex items-center justify-center rounded text-warm-500 hover:text-warm-800 dark:hover:text-warm-200 hover:bg-warm-300/50 dark:hover:bg-warm-700/50" :title="t('lab.rail.projects.add')" data-test="projects-add" @click="pickerOpen = true"><span class="i-carbon-add" /></button>
    </div>

    <div v-if="empty" class="px-3 py-1.5 flex flex-col items-start gap-1" data-test="projects-empty">
      <span class="text-[11px] text-warm-600 dark:text-warm-400 italic">{{ t("lab.rail.projects.empty") }}</span>
      <button type="button" class="text-[11px] text-iolite hover:underline flex items-center gap-1" data-test="projects-empty-add" @click="pickerOpen = true"><span class="i-carbon-folder-add" />{{ t("lab.rail.projects.add") }}</button>
    </div>

    <RailProjectRow v-for="p in projects.projects" :key="p.id" :project="p" @new-session="startIn" />

    <button v-if="projects.unassigned.session_count > 0" type="button" class="w-full flex items-center gap-1.5 px-3 py-1 text-left hover:bg-warm-300/50 dark:hover:bg-warm-700/50" data-test="projects-none" @click="openNoProject">
      <span class="w-[10px] shrink-0" />
      <span class="i-carbon-document-blank shrink-0 text-warm-500" />
      <span class="flex-1 min-w-0 truncate kt-text-body text-warm-600 dark:text-warm-400">{{ t("lab.rail.projects.noProject") }}</span>
      <span v-if="projects.unassigned.running_count" class="shrink-0 flex items-center gap-1 text-[10px] text-iolite"><span class="w-1.5 h-1.5 rounded-full bg-iolite" />{{ projects.unassigned.running_count }}</span>
      <span class="shrink-0 kt-text-caption text-warm-500 tabular-nums">{{ projects.unassigned.session_count }}</span>
    </button>

    <DirectoryPickerDialog v-model="pickerOpen" @pick="onPick" />
    <NewSessionDialog v-if="newPwd !== null" :key="newPwd" :initial-pwd="newPwd" @close="newPwd = null" />
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue"

import DirectoryPickerDialog from "@/components/common/DirectoryPickerDialog.vue"
import RailProjectRow from "@/components/shell/rail/RailProjectRow.vue"
import NewSessionDialog from "@/components/shell/newSession/NewSessionDialog.vue"
import { useConversationsStore } from "@/stores/conversations"
import { NO_PROJECT, useProjectsStore } from "@/stores/projects"
import { useTabsStore } from "@/stores/tabs"
import { useI18n } from "@/utils/i18n"

/**
 * The sidebar's Projects: the working folders sessions ran in (and the ones
 * added by hand), the "No project" bucket, and adding a folder. Polls while
 * shown and re-reads when a session starts or stops.
 */
const { t } = useI18n()
const projects = useProjectsStore()
const conversations = useConversationsStore()
const tabs = useTabsStore()
const pickerOpen = ref(false)
const newPwd = ref(null)

const empty = computed(() => projects.loaded && !projects.projects.length && !projects.unassigned.session_count)

function startIn(project) {
  newPwd.value = project.path
}

function openNoProject() {
  tabs.openTab({ kind: "saved-sessions", id: "saved-sessions", project: NO_PROJECT })
}

async function onPick(path) {
  if (path) await projects.add(path)
}

watch(
  () => conversations.liveRows.length,
  () => {
    if (projects.loaded) void projects.load({ force: true })
  },
)

onMounted(() => projects.startPolling())
onBeforeUnmount(() => projects.stopPolling())
</script>
