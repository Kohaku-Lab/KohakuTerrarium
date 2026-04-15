<template>
  <div class="h-full flex flex-col bg-warm-50 dark:bg-warm-900 overflow-hidden">
    <!-- Panel header -->
    <div class="flex items-center gap-2 px-3 py-2 border-b border-warm-200 dark:border-warm-700 shrink-0">
      <div class="i-carbon-pulse text-sm text-warm-500" />
      <span class="text-xs font-medium text-warm-500 dark:text-warm-400 flex-1">Activity</span>
      <span v-if="jobCount > 0" class="text-[10px] font-mono px-1.5 py-0.5 rounded bg-amber/15 text-amber">{{ jobCount }}</span>
    </div>

    <!-- Context bar + model -->
    <div class="px-3 py-2 border-b border-warm-200/70 dark:border-warm-700/70 text-[11px] flex flex-col gap-1.5">
      <div class="flex items-center gap-2">
        <span class="font-mono text-iolite truncate">{{ model || "—" }}</span>
        <span class="flex-1" />
        <span v-if="chat.processing || jobCount > 0" class="flex items-center gap-1 text-amber">
          <span class="w-1.5 h-1.5 rounded-full bg-amber kohaku-pulse" />
          <span class="text-[10px]">{{ chat.processing ? "processing" : jobCount + " jobs" }}</span>
        </span>
      </div>
      <!-- Context usage bar -->
      <div v-if="maxContext > 0" class="flex items-center gap-2 text-[10px] text-warm-500">
        <span class="shrink-0 w-12">Context</span>
        <div class="relative flex-1 h-1.5 rounded-full bg-warm-100 dark:bg-warm-800 overflow-hidden">
          <div class="absolute left-0 top-0 h-full rounded-full transition-all duration-300" :class="contextPct >= 80 ? 'bg-coral' : contextPct >= 60 ? 'bg-amber' : 'bg-aquamarine'" :style="{ width: Math.min(contextPct, 100) + '%' }" />
        </div>
        <span class="font-mono shrink-0"> {{ formatTokens(totals.lastPrompt) }}/{{ formatTokens(maxContext) }} ({{ contextPct }}%) </span>
      </div>
    </div>

    <!-- Running jobs list -->
    <div class="flex-1 overflow-y-auto px-3 py-2 text-xs">
      <div v-if="jobCount === 0 && !chat.processing" class="text-warm-400 py-6 text-center">Idle</div>
      <div v-else class="flex flex-col gap-1.5">
        <div v-for="(job, jobId) in chat.runningJobs" :key="jobId" class="flex items-center gap-2 px-2 py-1.5 rounded-md bg-amber/10 group">
          <span class="w-1.5 h-1.5 rounded-full bg-amber kohaku-pulse shrink-0" />
          <span class="font-mono text-[11px] text-amber-shadow dark:text-amber-light truncate">{{ job.name }}</span>
          <span class="flex-1" />
          <span class="text-warm-400 font-mono text-[10px]">
            {{ chat.getJobElapsed(job) }}
          </span>
          <button class="text-warm-400 hover:text-coral transition-colors opacity-0 group-hover:opacity-100" title="Stop task" @click="stopJob(jobId, job.name)">
            <span class="i-carbon-close text-[10px]" />
          </button>
        </div>
      </div>
    </div>

    <!-- Session totals (pinned bottom) -->
    <div class="px-3 py-1.5 border-t border-warm-200 dark:border-warm-700 text-[10px] text-warm-500 flex items-center gap-3 shrink-0">
      <span class="text-warm-400 text-[9px]">Total</span>
      <span>
        in
        <span class="font-mono text-warm-600 dark:text-warm-400">{{ formatTokens(totals.prompt) }}</span>
      </span>
      <span>
        out
        <span class="font-mono text-warm-600 dark:text-warm-400">{{ formatTokens(totals.completion) }}</span>
      </span>
      <span v-if="totals.cached > 0">
        cache
        <span class="font-mono text-aquamarine">{{ formatTokens(totals.cached) }}</span>
      </span>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue"

import { useChatStore } from "@/stores/chat"
import { agentAPI, terrariumAPI } from "@/utils/api"

const props = defineProps({
  instance: { type: Object, default: null },
})

const chat = useChatStore()

const jobCount = computed(() => Object.keys(chat.runningJobs || {}).length)
const model = computed(() => chat.sessionInfo.model || props.instance?.model || "(no model - frontend)")
const maxContext = computed(() => chat.sessionInfo.maxContext || props.instance?.max_context || 0)

const totals = computed(() => {
  let prompt = 0
  let completion = 0
  let cached = 0
  let lastPrompt = 0
  for (const u of Object.values(chat.tokenUsage || {})) {
    prompt += u.prompt || 0
    completion += u.completion || 0
    cached += u.cached || 0
    if ((u.lastPrompt || 0) > lastPrompt) lastPrompt = u.lastPrompt
  }
  return { prompt, completion, cached, lastPrompt }
})

const contextPct = computed(() => {
  if (!maxContext.value) return 0
  return Math.round((totals.value.lastPrompt / maxContext.value) * 100)
})

function formatTokens(n) {
  if (!n) return "0"
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M"
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "k"
  return String(n)
}

async function stopJob(jobId, name) {
  const instanceId = props.instance?.id
  if (!instanceId) return
  try {
    if (props.instance?.type === "terrarium") {
      const target = chat.terrariumTarget
      if (!target) return
      await terrariumAPI.stopCreatureTask(instanceId, target, jobId)
    } else {
      await agentAPI.stopTask(instanceId, jobId)
    }
  } catch (err) {
    console.error("Failed to stop job", name, err)
  }
}
</script>
