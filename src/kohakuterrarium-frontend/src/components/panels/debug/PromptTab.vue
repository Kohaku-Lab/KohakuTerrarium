<template>
  <div class="h-full flex flex-col overflow-hidden">
    <div class="flex items-center gap-2 px-3 py-1 border-b border-warm-200 dark:border-warm-700 shrink-0 text-[10px]">
      <span class="text-warm-400">
        {{ lastLoaded ? `fetched ${lastLoaded}` : "—" }}
      </span>
      <span class="flex-1" />
      <button class="px-2 py-0.5 rounded bg-warm-100 dark:bg-warm-800 text-warm-600 dark:text-warm-300 hover:text-iolite" @click="load">
        <div class="i-carbon-renew inline-block text-[11px] mr-1" />
        Refresh
      </button>
      <button class="px-2 py-0.5 rounded bg-warm-100 dark:bg-warm-800 text-warm-600 dark:text-warm-300 hover:text-iolite" :disabled="!promptText" @click="copy">
        <div class="i-carbon-copy inline-block text-[11px] mr-1" />
        Copy
      </button>
      <button v-if="previousText" class="px-2 py-0.5 rounded transition-colors" :class="showDiff ? 'bg-iolite/15 text-iolite' : 'bg-warm-100 dark:bg-warm-800 text-warm-600 dark:text-warm-300 hover:text-iolite'" @click="showDiff = !showDiff">Diff</button>
    </div>

    <div class="flex-1 overflow-auto p-3 text-[11px] font-mono">
      <div v-if="loading" class="text-warm-400 text-center py-6">Loading...</div>
      <div v-else-if="error" class="text-coral">{{ error }}</div>
      <template v-else-if="showDiff && previousText">
        <div v-for="(line, i) in diffLines" :key="i" class="whitespace-pre-wrap break-words leading-tight" :class="diffClass(line.kind)">
          <span class="inline-block w-3 opacity-60">{{ diffSymbol(line.kind) }}</span>
          {{ line.text }}
        </div>
      </template>
      <pre v-else class="whitespace-pre-wrap break-words text-warm-700 dark:text-warm-300">{{ promptText }}</pre>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from "vue"

import { useChatStore } from "@/stores/chat"
import { terrariumAPI } from "@/utils/api"

const props = defineProps({
  instance: { type: Object, default: null },
})

const chat = useChatStore()

const promptText = ref("")
const previousText = ref("")
const loading = ref(false)
const error = ref("")
const showDiff = ref(false)
const lastLoaded = ref("")
// Per-creature panels resolve a target creature within the session.
// Solo sessions auto-target their only creature so the panel works
// without forcing a tab click.
const target = computed(() => {
  const creatures = props.instance?.creatures || []
  if (creatures.length === 0) return null
  if (creatures.length > 1) return chat.terrariumTarget
  return chat.terrariumTarget || creatures[0].name
})

async function load() {
  const sid = props.instance?.graph_id || props.instance?.id
  if (!sid) return
  if (!target.value) {
    error.value = "System prompt is only available for root/creature tabs."
    promptText.value = ""
    return
  }
  loading.value = true
  error.value = ""
  try {
    const data = await terrariumAPI.getSystemPrompt(sid, target.value)
    if (promptText.value) previousText.value = promptText.value
    promptText.value = data?.text || ""
    lastLoaded.value = new Date().toLocaleTimeString()
  } catch (err) {
    error.value = err?.response?.data?.detail || err?.message || String(err)
  } finally {
    loading.value = false
  }
}

function copy() {
  if (!promptText.value) return
  if (typeof navigator !== "undefined" && navigator.clipboard) {
    navigator.clipboard.writeText(promptText.value).catch(() => {})
  }
}

// Minimal line-based diff. Not a proper longest-common-subseq but
// good enough to visualize small changes in the system prompt.
const diffLines = computed(() => {
  if (!previousText.value || !promptText.value) return []
  const a = previousText.value.split("\n")
  const b = promptText.value.split("\n")
  const setA = new Set(a)
  const setB = new Set(b)
  const out = []
  const max = Math.max(a.length, b.length)
  for (let i = 0; i < max; i++) {
    const la = a[i]
    const lb = b[i]
    if (la === lb) {
      if (la !== undefined) out.push({ kind: "same", text: la })
      continue
    }
    if (la !== undefined && !setB.has(la)) out.push({ kind: "del", text: la })
    if (lb !== undefined && !setA.has(lb)) out.push({ kind: "add", text: lb })
  }
  return out
})

function diffClass(kind) {
  return (
    {
      add: "bg-aquamarine/10 text-aquamarine",
      del: "bg-coral/10 text-coral",
      same: "text-warm-600 dark:text-warm-400",
    }[kind] || ""
  )
}

function diffSymbol(kind) {
  return { add: "+", del: "-", same: " " }[kind] || " "
}

onMounted(load)
watch(() => [props.instance?.id, target.value], load)
</script>
