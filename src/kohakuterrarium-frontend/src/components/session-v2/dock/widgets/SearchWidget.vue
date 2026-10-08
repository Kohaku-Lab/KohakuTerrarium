<template>
  <div class="h-full min-h-0 flex flex-col" data-test="v2-widget-search">
    <form class="shrink-0 flex flex-col gap-1.5 px-3 py-2" @submit.prevent="run">
      <div class="flex items-center gap-1.5 rounded-md border kt-v2-line px-2 focus-within:border-iolite">
        <span class="i-carbon-search text-warm-400 text-xs" />
        <input ref="inputEl" v-model="query" :placeholder="t('widget.search.placeholder')" class="flex-1 min-w-0 bg-transparent py-1 text-[12px] text-warm-800 dark:text-warm-100 focus:outline-none" />
        <span v-if="loading" class="i-carbon-circle-dash animate-spin text-warm-400 text-xs" />
      </div>
      <div class="flex items-center gap-1">
        <button v-for="m in MODES" :key="m" type="button" class="h-5 px-2 rounded-full text-[10px]" :class="mode_ === m ? 'bg-iolite/15 text-iolite' : 'text-warm-500 hover:bg-warm-100 dark:hover:bg-warm-800'" @click="setMode(m)">{{ t(`widget.search.mode.${m}`) }}</button>
      </div>
    </form>
    <div v-if="error" class="px-3 py-1 text-[11px] text-coral">{{ error }}</div>
    <div class="flex-1 min-h-0 overflow-y-auto pb-2">
      <div v-if="!searched" class="px-3 py-4 text-center text-xs text-warm-400">{{ t("widget.search.hint") }}</div>
      <div v-else-if="!results.length && !loading" class="px-3 py-4 text-center text-xs text-warm-400">{{ t("widget.search.empty") }}</div>
      <button v-for="(r, i) in results" :key="i" class="w-full text-left px-3 py-2 hover:bg-warm-100 dark:hover:bg-warm-800/60" @click="open(r)">
        <div class="flex items-center gap-1.5 text-[10px] text-warm-400">
          <span class="i-carbon-bot" /><span class="font-medium text-warm-600 dark:text-warm-300">{{ r.agent || "—" }}</span>
          <span v-if="r.block_type">· {{ r.block_type }}</span>
          <span class="flex-1" />
          <span v-if="r.score != null" class="font-mono">{{ formatScore(r.score) }}</span>
        </div>
        <div class="mt-0.5 text-[12px] text-warm-700 dark:text-warm-200 break-words" :class="mode === 'side' ? 'line-clamp-6' : 'line-clamp-3'">{{ r.content }}</div>
      </button>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from "vue"

import { tabKeyFor } from "@/components/session-v2/model/creatureKeys"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import { sessionAPI } from "@/utils/api"

/** Session memory search; a result opens its creature's conversation. */
defineProps({ mode: { type: String, default: "widget" } })

const MODES = ["auto", "fts", "semantic"]
const ctx = useSessionV2()
const t = useV2T()
const query = ref("")
const mode_ = ref("auto")
const results = ref([])
const loading = ref(false)
const error = ref("")
const searched = ref(false)
const inputEl = ref(null)
let gen = 0

function sessionName() {
  return ctx.chat.sessionInfo?.sessionId || ctx.instance.value?.session_id || ctx.instance.value?.id
}

function formatScore(score) {
  return typeof score === "number" ? score.toFixed(2) : String(score)
}

function setMode(m) {
  mode_.value = m
  if (searched.value) run()
}

async function run() {
  const q = query.value.trim()
  const name = sessionName()
  if (!q || !name) return
  const mine = ++gen
  loading.value = true
  error.value = ""
  searched.value = true
  try {
    const data = await sessionAPI.searchMemory(name, { q, mode: mode_.value, k: 20 })
    if (mine === gen) results.value = data?.results || []
  } catch (err) {
    if (mine === gen) {
      error.value = err?.response?.data?.detail || err?.message || String(err)
      results.value = []
    }
  } finally {
    if (mine === gen) loading.value = false
  }
}

function open(r) {
  if (!r.agent) return
  const inst = ctx.instance.value
  const rootName = ctx.chat._rootSourceName
  const known = (inst?.creatures || []).some((c) => c.name === r.agent)
  const tab = known ? tabKeyFor(r.agent, rootName) : inst?.has_root ? "root" : ""
  if (!tab) return
  ctx.chat.openTab(tab)
  ctx.setTab("chat")
  ctx.closeWidget()
}

onMounted(() => inputEl.value?.focus())
</script>
