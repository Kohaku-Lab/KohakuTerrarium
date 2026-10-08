<template>
  <aside class="h-full min-h-0 flex flex-col" data-test="v2-file-column">
    <header class="h-9 shrink-0 flex items-center gap-2 px-3 border-b kt-v2-line">
      <span class="i-carbon-folder text-warm-500" />
      <span class="text-xs font-medium text-warm-600 dark:text-warm-300 truncate flex-1" :title="root">{{ rootName }}</span>
      <button class="i-carbon-renew text-sm text-warm-400 hover:text-warm-700 dark:hover:text-warm-200" :title="t('ws.refresh')" @click="refreshAll" />
      <button class="i-carbon-side-panel-close text-sm text-warm-400 hover:text-warm-700 dark:hover:text-warm-200" :title="t('ws.hideFiles')" @click="$emit('collapse')" />
    </header>

    <div class="flex-1 min-h-0">
      <FileTreeView ref="treeEl" :root="root" @select="$emit('open-file', $event)" />
    </div>

    <section class="shrink-0 border-t kt-v2-line flex flex-col" :class="touchedOpen ? 'max-h-[45%] min-h-32' : ''">
      <button class="h-8 shrink-0 flex items-center gap-2 px-3 text-[11px] uppercase tracking-wider text-warm-500 hover:text-warm-700 dark:hover:text-warm-200" data-test="v2-touched-toggle" @click="setTouchedOpen(!touchedOpen)">
        <span :class="touchedOpen ? 'i-carbon-chevron-down' : 'i-carbon-chevron-right'" />
        <span class="flex-1 text-left">{{ t("ws.touched") }}</span>
        <span v-if="touchedOpen" class="font-mono normal-case">{{ touchedCount }}</span>
      </button>
      <div v-if="touchedOpen" class="flex-1 min-h-0 overflow-y-auto px-2 pb-2 text-[11px]">
        <div v-if="!touchedCount" class="text-warm-400 text-center py-4">{{ t("ws.touchedEmpty") }}</div>
        <template v-for="action in TOUCHED_ACTIONS" :key="action">
          <div v-if="touched[action].length" class="mb-2">
            <div class="px-1 mb-0.5 text-[10px] uppercase tracking-wider text-warm-400">{{ t(`ws.action.${action}`) }} · {{ touched[action].length }}</div>
            <button v-for="entry in touched[action]" :key="`${entry.action}\u0000${entry.path}\u0000${entry.command}`" class="w-full flex items-center gap-2 px-1.5 py-1 rounded text-left enabled:hover:bg-warm-100 dark:enabled:hover:bg-warm-800 disabled:cursor-default" :title="entry.path || entry.command" :disabled="!entry.path" @click="onTouched(entry)">
              <span class="w-3 shrink-0 text-center" :class="ACTION_STYLE[action].cls">{{ ACTION_STYLE[action].glyph }}</span>
              <span class="font-mono text-warm-700 dark:text-warm-300 truncate flex-1">{{ entry.path ? shortPath(entry.path) : entry.command }}</span>
              <span v-if="entry.diff" class="text-[9px] text-iolite shrink-0">{{ t("ws.diff") }}</span>
              <span class="text-[9px] font-mono text-warm-400 shrink-0">{{ entry.tool }}</span>
            </button>
          </div>
        </template>
      </div>
    </section>
  </aside>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, shallowRef, toRaw, watch } from "vue"

import { TOUCHED_ACTIONS, collectTouched, shortPath } from "@/components/session-v2/model/workspace/touchedFiles"
import { baseName } from "@/components/session-v2/model/workspace/languageOf"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import FileTreeView from "@/components/session-v2/workspace/FileTreeView.vue"
import { useEditorStore } from "@/stores/editor"
import { readLocalPref, writeLocalPref } from "@/utils/uiPrefs"

/**
 * Workspace files: the lazy tree of the session's working directory and
 * the files the agent touched. Touched is rescanned when a turn or a job
 * ends (not per streamed token), and only while its section is open.
 */
const emit = defineEmits(["collapse", "open-file", "open-diff"])

const ACTION_STYLE = {
  wrote: { glyph: "✎", cls: "text-iolite" },
  read: { glyph: "●", cls: "text-aquamarine" },
  errored: { glyph: "✕", cls: "text-coral" },
  exec: { glyph: "$", cls: "text-amber" },
}

const t = useV2T()
const session = useSessionV2()
const chat = session.chat
const editor = useEditorStore()
const treeEl = ref(null)
const root = computed(() => session.instance.value?.pwd || "")
const rootName = computed(() => baseName(root.value) || t("ws.files"))

const openKey = () => `kt.v2.ws.${session.instanceId.value}.touched`
const touchedOpen = ref(readLocalPref(openKey()) === "1")
const touched = shallowRef(collectTouched({}))
const touchedCount = computed(() => TOUCHED_ACTIONS.reduce((n, a) => n + touched.value[a].length, 0))

function rescanTouched() {
  if (touchedOpen.value) touched.value = collectTouched(toRaw(chat.messagesByTab))
}

function setTouchedOpen(open) {
  touchedOpen.value = open
  writeLocalPref(openKey(), open ? "1" : "0")
  rescanTouched()
}

function refreshAll() {
  treeEl.value?.refresh()
  rescanTouched()
  for (const path of editor.openFilePaths) editor.syncFromDisk(path)
}

function onTouched(entry) {
  if (!entry.path) return
  if (entry.diff) emit("open-diff", { path: entry.path, old: entry.diff.old, new: entry.diff.new })
  else emit("open-file", entry.path)
}

// A finished turn (in any conversation) or job is when the agent's file work lands.
let settleTimer = null
watch(
  () => [Object.values(chat.processingByTab || {}).some(Boolean), Object.keys(chat.runningJobs || {}).length, chat._instanceGeneration],
  ([processing, jobs, generation], previous) => {
    const [wasProcessing, prevJobs, prevGeneration] = previous || []
    if ((!processing && wasProcessing) || jobs < (prevJobs ?? jobs) || generation !== prevGeneration) {
      clearTimeout(settleTimer)
      settleTimer = setTimeout(() => {
        settleTimer = null
        refreshAll()
      }, 500)
    }
  },
)
onBeforeUnmount(() => clearTimeout(settleTimer))

rescanTouched()
</script>
