<template>
  <div v-if="isCompact" class="kt-v2-canvas h-full flex flex-col min-h-0" data-test="v2-debug-tab">
    <PhonePage v-if="selectedRow && view !== 'prompt'" :title="selectedRow.kind" :back-label="t('phone.back')" :scroll="false" test-id="v2-debug-detail" @back="selectedKey = null">
      <EventDetail :row="selectedRow" class="flex-1 min-h-0" />
    </PhonePage>
    <template v-else>
      <div class="kt-v2-line shrink-0 flex flex-col gap-2 px-3 py-2 border-b">
        <nav class="flex gap-1 overflow-x-auto scrollbar-none" role="tablist">
          <button v-for="v in VIEWS" :key="v.id" role="tab" :aria-selected="view === v.id" class="h-9 px-3 shrink-0 rounded-lg text-sm flex items-center gap-1.5" :class="view === v.id ? 'bg-iolite/12 text-iolite dark:text-iolite-light font-medium' : 'text-warm-600 dark:text-warm-300 active:bg-warm-200/60 dark:active:bg-warm-800'" :data-test="`v2-debug-view-${v.id}`" @click="setView(v.id)"><span :class="v.icon" />{{ t(`debug.${v.id}`) }}</button>
        </nav>
        <div class="flex items-center gap-1">
          <template v-if="view !== 'prompt'">
            <el-input v-model="query" class="flex-1 min-w-0" clearable :placeholder="t('debug.filter')" data-test="v2-debug-filter" />
            <el-select v-model="kind" class="!w-28 shrink-0" :placeholder="t('debug.allKinds')" clearable>
              <el-option v-for="k in kinds" :key="k" :label="k" :value="k" />
            </el-select>
            <button class="w-10 h-10 shrink-0 rounded-lg flex items-center justify-center" :class="paused ? 'bg-amber/15 text-amber' : 'text-warm-500'" :aria-label="paused ? t('debug.resume') : t('debug.pause')" data-test="v2-debug-pause" @click="togglePause"><span :class="paused ? 'i-carbon-play' : 'i-carbon-pause'" /></button>
          </template>
          <span v-else class="flex-1 min-w-0 truncate text-xs text-warm-500">{{ promptTarget }}</span>
          <button class="w-10 h-10 shrink-0 rounded-lg flex items-center justify-center text-warm-500" :aria-label="t('debug.download')" data-test="v2-debug-download" @click="download"><span class="i-carbon-download" /></button>
        </div>
        <div v-if="view !== 'prompt'" class="flex items-center gap-2 text-[11px] text-warm-400">
          <span class="font-mono">{{ t("debug.count", { shown: filtered.length, total: sourceRows.length }) }}</span>
          <span v-if="view === 'logs'" class="flex items-center gap-1"><span class="w-1.5 h-1.5 rounded-full" :class="logs.connected.value ? 'bg-aquamarine' : 'bg-warm-400'" />{{ logs.connected.value ? t("debug.connected") : t("debug.connecting") }}</span>
          <button v-if="view === 'logs'" class="ml-auto h-8 px-2 rounded text-warm-500" @click="logs.clear()">{{ t("debug.clear") }}</button>
          <span v-if="view === 'trace'" class="truncate">{{ t("debug.traceScope", { name: chat.activeTab ? tabLabel(chat.activeTab, chat._rootSourceName) : "—" }) }}</span>
        </div>
      </div>
      <div v-if="view === 'prompt'" class="flex-1 min-h-0">
        <div v-if="!promptTarget" class="h-full flex items-center justify-center text-xs text-warm-400">{{ t("debug.promptUnavailable") }}</div>
        <EventDetail v-else mode="prompt" :prompt-text="promptFor === promptTarget ? promptText : ''" :previous-text="promptFor === promptTarget ? previousPrompt : ''" :prompt-target="promptTarget" :prompt-fetched-at="promptFetchedAt" :prompt-loading="promptLoading" :prompt-error="promptError" @refresh="loadPrompt" />
      </div>
      <div v-else class="kt-v2-panel flex-1 min-h-0">
        <EventList :rows="filtered" :selected-key="selectedKey" :tail="view === 'logs'" :empty-label="sourceRows.length ? t('debug.empty') : t(`debug.none.${view}`)" @select="selectedKey = $event" />
      </div>
    </template>
  </div>

  <div v-else class="kt-v2-canvas h-full flex flex-col min-h-0" data-test="v2-debug-tab">
    <div class="kt-v2-line h-11 shrink-0 flex items-center gap-2 px-4 border-b">
      <nav class="flex items-center rounded-lg bg-warm-200/60 dark:bg-warm-800/70 p-0.5" role="tablist">
        <button v-for="v in VIEWS" :key="v.id" role="tab" :aria-selected="view === v.id" class="h-7 px-3 rounded-md text-xs flex items-center gap-1.5" :class="view === v.id ? 'bg-white dark:bg-warm-950 text-warm-800 dark:text-warm-100 shadow-sm' : 'text-warm-500 hover:text-warm-800 dark:hover:text-warm-200'" :data-test="`v2-debug-view-${v.id}`" @click="setView(v.id)"><span :class="v.icon" />{{ t(`debug.${v.id}`) }}</button>
      </nav>
      <template v-if="view !== 'prompt'">
        <el-input v-model="query" size="small" class="!w-64" clearable :placeholder="t('debug.filter')" data-test="v2-debug-filter" />
        <el-select v-model="kind" size="small" class="!w-36" :placeholder="t('debug.allKinds')" clearable>
          <el-option v-for="k in kinds" :key="k" :label="k" :value="k" />
        </el-select>
        <span class="text-[11px] font-mono text-warm-400">{{ t("debug.count", { shown: filtered.length, total: sourceRows.length }) }}</span>
        <span v-if="view === 'logs'" class="flex items-center gap-1 text-[11px] text-warm-400"> <span class="w-1.5 h-1.5 rounded-full" :class="logs.connected.value ? 'bg-aquamarine' : 'bg-warm-400'" />{{ logs.connected.value ? t("debug.connected") : t("debug.connecting") }} </span>
        <span v-if="view === 'trace'" class="text-[11px] text-warm-500 truncate">{{ t("debug.traceScope", { name: chat.activeTab ? tabLabel(chat.activeTab, chat._rootSourceName) : "—" }) }}</span>
      </template>
      <span class="flex-1" />
      <template v-if="view !== 'prompt'">
        <button class="h-7 px-2 rounded-md text-xs flex items-center gap-1" :class="paused ? 'bg-amber/15 text-amber' : 'text-warm-500 hover:text-warm-800 dark:hover:text-warm-200'" data-test="v2-debug-pause" @click="togglePause"><span :class="paused ? 'i-carbon-play' : 'i-carbon-pause'" />{{ paused ? t("debug.resume") : t("debug.pause") }}</button>
        <button v-if="view === 'logs'" class="h-7 px-2 rounded-md text-xs text-warm-500 hover:text-warm-800 dark:hover:text-warm-200" @click="logs.clear()">{{ t("debug.clear") }}</button>
      </template>
      <button class="h-7 px-2 rounded-md text-xs flex items-center gap-1 text-warm-500 hover:text-warm-800 dark:hover:text-warm-200" data-test="v2-debug-download" @click="download"><span class="i-carbon-download" />{{ t("debug.download") }}</button>
    </div>

    <div v-if="view === 'prompt'" class="flex-1 min-h-0">
      <div v-if="!promptTarget" class="h-full flex items-center justify-center text-xs text-warm-400">{{ t("debug.promptUnavailable") }}</div>
      <EventDetail v-else mode="prompt" :prompt-text="promptFor === promptTarget ? promptText : ''" :previous-text="promptFor === promptTarget ? previousPrompt : ''" :prompt-target="promptTarget" :prompt-fetched-at="promptFetchedAt" :prompt-loading="promptLoading" :prompt-error="promptError" @refresh="loadPrompt" />
    </div>
    <div v-else class="flex-1 min-h-0 flex">
      <div class="kt-v2-panel kt-v2-edge flex-1 min-w-0 border-r">
        <EventList :rows="filtered" :selected-key="selectedKey" :tail="view === 'logs'" :empty-label="sourceRows.length ? t('debug.empty') : t(`debug.none.${view}`)" @select="selectedKey = $event" />
      </div>
      <div class="kt-v2-canvas w-[44%] min-w-80 shrink-0">
        <EventDetail :row="selectedRow" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, shallowRef, watch } from "vue"

import EventDetail from "@/components/session-v2/debug/EventDetail.vue"
import EventList from "@/components/session-v2/debug/EventList.vue"
import { eventRows, filterRows, logRows, rowKinds, rowPayload, safeJson, traceRows } from "@/components/session-v2/model/debug/debugModel"
import { tabLabel } from "@/components/session-v2/model/creatureKeys"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import { focusedCreature } from "@/components/session-v2/model/widgets/widgetData"
import PhonePage from "@/components/session-v2/phone/PhonePage.vue"
import { useDensity } from "@/composables/useDensity"
import { useLogStream } from "@/composables/useLogStream"
import { terrariumAPI } from "@/utils/api"

/**
 * The v2 Debug tab: Events (every conversation's messages), Logs (the
 * server log stream), Prompt (the focused agent's system prompt) and
 * Trace (the focused conversation's tool calls), each a windowed list
 * beside a detail pane. Rows are built only for the view on screen. Phones
 * show the list full width and a row's detail on its own page.
 */
const VIEWS = [
  { id: "events", icon: "i-carbon-event" },
  { id: "logs", icon: "i-carbon-catalog" },
  { id: "prompt", icon: "i-carbon-document" },
  { id: "trace", icon: "i-carbon-flow-connection" },
]

const ctx = useSessionV2()
const t = useV2T()
const chat = ctx.chat
const { isCompact } = useDensity()
const logs = useLogStream({ autoConnect: false })

const view = ref("events")
const query = ref("")
const kind = ref("")
const selectedKey = ref(null)
const paused = ref(false)
const frozen = shallowRef(null)

const active = computed(() => ctx.tab.value === "debug")

// The log socket is open only while the Logs view is on screen.
watch(
  () => active.value && view.value === "logs",
  (on) => (on ? logs.connect() : logs.disconnect()),
  { immediate: true },
)

const liveRows = computed(() => {
  if (!active.value) return []
  if (view.value === "events") return eventRows(chat.messagesByTab, chat._rootSourceName)
  if (view.value === "logs") return logRows(logs.lines.value)
  if (view.value === "trace") return traceRows(chat.messagesByTab?.[chat.activeTab] || [])
  return []
})
const sourceRows = computed(() => frozen.value || liveRows.value)
const filtered = computed(() => filterRows(sourceRows.value, { query: query.value, kind: kind.value }))
const kinds = computed(() => rowKinds(sourceRows.value))
const selectedRow = computed(() => (selectedKey.value ? sourceRows.value.find((r) => r.key === selectedKey.value) || null : null))

function setView(id) {
  view.value = id
  kind.value = ""
  selectedKey.value = null
  paused.value = false
  frozen.value = null
}

function togglePause() {
  paused.value = !paused.value
  frozen.value = paused.value ? liveRows.value.slice() : null
}

// ── Prompt ──
const promptText = ref("")
const previousPrompt = ref("")
const promptLoading = ref(false)
const promptError = ref("")
const promptFetchedAt = ref("")
// The creature whose prompt `promptText` holds; the pane shows it only while it is still the target.
const promptFor = ref("")
const promptTarget = computed(() => focusedCreature(ctx.instance.value, chat.activeTab, chat._rootSourceName) || "")
let promptSeq = 0

async function loadPrompt() {
  const target = promptTarget.value
  const sid = ctx.sessionId.value
  if (!target || !sid) return
  const mine = ++promptSeq
  promptLoading.value = true
  promptError.value = ""
  try {
    const data = await terrariumAPI.getSystemPrompt(sid, target)
    if (mine !== promptSeq) return
    previousPrompt.value = promptFor.value === target ? promptText.value : ""
    promptText.value = data?.text || ""
    promptFor.value = target
    promptFetchedAt.value = new Date().toLocaleTimeString()
  } catch (err) {
    if (mine === promptSeq) promptError.value = err?.response?.data?.detail || err?.message || String(err)
  } finally {
    if (mine === promptSeq) promptLoading.value = false
  }
}

watch(
  () => [view.value === "prompt" && active.value, promptTarget.value],
  ([shown, target], previous) => {
    // A target change invalidates any fetch still in flight for the old one.
    if (previous && previous[1] !== target) {
      promptSeq += 1
      promptLoading.value = false
    }
    if (shown && target && promptFor.value !== target) loadPrompt()
  },
  { immediate: true },
)

function download() {
  const stamp = new Date().toISOString().replace(/[:.]/g, "-")
  const isPrompt = view.value === "prompt"
  const body = isPrompt ? (promptFor.value === promptTarget.value ? promptText.value : "") : safeJson(filtered.value.map(rowPayload))
  const blob = new Blob([body], { type: isPrompt ? "text/plain" : "application/json" })
  const url = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = url
  a.download = `kt-${view.value}-${stamp}.${isPrompt ? "txt" : "json"}`
  a.click()
  setTimeout(() => URL.revokeObjectURL(url), 0)
}
</script>
