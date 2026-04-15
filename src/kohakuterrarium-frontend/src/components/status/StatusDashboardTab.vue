<template>
  <div class="h-full flex bg-warm-50 dark:bg-warm-900 overflow-hidden">
    <!-- Vertical tab rail -->
    <div class="flex flex-col gap-1 py-2 px-1 border-r border-warm-200 dark:border-warm-700 shrink-0">
      <button v-for="t in visibleTabs" :key="t.id" class="relative w-8 h-8 flex items-center justify-center rounded text-warm-400 hover:text-warm-600 dark:hover:text-warm-300 transition-colors" :class="activeTab === t.id ? 'bg-iolite/10 text-iolite' : ''" :title="t.label" @click="activeTab = t.id">
        <div :class="t.icon" class="text-sm" />
        <span v-if="t.id === 'jobs' && jobCount > 0" class="absolute -top-0.5 -right-0.5 w-3.5 h-3.5 rounded-full bg-amber text-white text-[8px] font-bold flex items-center justify-center">{{ jobCount > 9 ? "9+" : jobCount }}</span>
      </button>
    </div>

    <!-- Tab body -->
    <div class="flex-1 min-w-0 flex flex-col overflow-hidden">
      <div class="flex items-center gap-2 px-3 py-2 border-b border-warm-200 dark:border-warm-700 shrink-0">
        <span class="text-xs font-medium text-warm-500 dark:text-warm-400 flex-1">{{ activeLabel }}</span>
      </div>

      <div class="flex-1 overflow-y-auto px-3 py-2 text-xs">
        <!-- Session tab -->
        <template v-if="activeTab === 'session'">
          <div class="flex flex-col gap-1.5">
            <div class="flex items-center gap-2">
              <span class="text-warm-400 w-16">Agent</span>
              <span class="text-warm-600 dark:text-warm-400">{{ chat.sessionInfo.agentName || instance?.config_name || "--" }}</span>
            </div>
            <div class="flex items-center gap-2">
              <span class="text-warm-400 w-16">Model</span>
              <span class="text-iolite font-mono text-[11px]">{{ chat.sessionInfo.model || instance?.model || "--" }}</span>
            </div>
            <div class="flex items-center gap-2">
              <span class="text-warm-400 w-16">Provider</span>
              <span class="text-warm-600 dark:text-warm-400 text-[11px]">{{ currentModelProfile?.login_provider || instance?.provider || "--" }}</span>
            </div>
            <div class="flex items-center gap-2">
              <span class="text-warm-400 w-16">Session</span>
              <span class="text-warm-600 dark:text-warm-400 font-mono text-[10px] truncate max-w-32">{{ chat.sessionInfo.sessionId || instance?.session_id || "--" }}</span>
            </div>
            <div v-if="instance?.status" class="flex items-center gap-2">
              <span class="text-warm-400 w-16">Status</span>
              <span class="text-[10px] px-1.5 py-0.5 rounded" :class="instance.status === 'running' ? 'bg-aquamarine/10 text-aquamarine' : 'bg-warm-100 dark:bg-warm-800 text-warm-400'">{{ instance.status }}</span>
            </div>
          </div>
        </template>

        <!-- Token Usage tab -->
        <template v-else-if="activeTab === 'tokens'">
          <div class="flex flex-col gap-1.5">
            <div class="flex items-center gap-2">
              <span class="text-warm-400 w-20">Prompt in</span>
              <span class="text-warm-600 dark:text-warm-400 font-mono">{{ formatTokens(totalUsage.prompt) }}</span>
            </div>
            <div class="flex items-center gap-2">
              <span class="text-warm-400 w-20">Completion</span>
              <span class="text-warm-600 dark:text-warm-400 font-mono">{{ formatTokens(totalUsage.completion) }}</span>
            </div>
            <div v-if="totalUsage.cached > 0" class="flex items-center gap-2">
              <span class="text-warm-400 w-20">Cached</span>
              <span class="text-aquamarine font-mono">{{ formatTokens(totalUsage.cached) }}</span>
            </div>
            <!-- Context usage bar -->
            <div v-if="maxContext > 0" class="mt-1">
              <div class="flex items-center justify-between mb-1">
                <span class="text-warm-400">Context</span>
                <span class="font-mono text-[10px]" :class="contextPct >= 80 ? 'text-coral' : contextPct >= 60 ? 'text-amber' : 'text-warm-500'">{{ formatTokens(totalUsage.lastPrompt) }} / {{ formatTokens(maxContext) }} ({{ contextPct }}%)</span>
              </div>
              <div class="relative w-full h-1.5 rounded-full bg-warm-100 dark:bg-warm-800 overflow-hidden">
                <div class="h-full rounded-full transition-all duration-300" :class="contextPct >= 80 ? 'bg-coral' : contextPct >= 60 ? 'bg-amber' : 'bg-aquamarine'" :style="{ width: Math.min(contextPct, 100) + '%' }" />
                <div v-if="compactThresholdPct > 0" class="absolute top-0 h-full w-0.5 bg-amber opacity-60" :style="{ left: compactThresholdPct + '%' }" :title="'Compact at ' + formatTokens(compactThreshold)" />
              </div>
            </div>
          </div>
        </template>

        <!-- Running Jobs tab -->
        <template v-else-if="activeTab === 'jobs'">
          <div v-if="jobCount === 0" class="text-warm-400 py-6 text-center text-[11px]">No running jobs</div>
          <div v-else class="flex flex-col gap-1">
            <div v-for="(job, jobId) in chat.runningJobs" :key="jobId" class="flex items-center gap-2 px-2 py-1.5 rounded-md bg-amber/10 group">
              <span class="w-1.5 h-1.5 rounded-full bg-amber kohaku-pulse shrink-0" />
              <span class="font-mono text-[11px] text-amber truncate">{{ job.name }}</span>
              <span class="flex-1" />
              <span class="text-warm-400 font-mono text-[10px]">{{ chat.getJobElapsed(job) }}</span>
              <button class="text-warm-400 hover:text-coral transition-colors opacity-0 group-hover:opacity-100" title="Stop task" @click="stopTask(jobId)">
                <span class="i-carbon-close text-[10px]" />
              </button>
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from "vue"

import StatusDot from "@/components/common/StatusDot.vue"
import { useChatStore } from "@/stores/chat"
import { configAPI, agentAPI, terrariumAPI } from "@/utils/api"

const props = defineProps({
  instance: { type: Object, default: null },
  onOpenTab: { type: Function, default: () => {} },
})

const chat = useChatStore()

const allTabs = [
  { id: "session", label: "Session", icon: "i-carbon-information" },
  { id: "tokens", label: "Token Usage", icon: "i-carbon-meter" },
  { id: "jobs", label: "Running Jobs", icon: "i-carbon-play-outline" },
]
const activeTab = ref("session")

const visibleTabs = computed(() => allTabs)
const activeLabel = computed(() => allTabs.find((t) => t.id === activeTab.value)?.label || "")

// ── Model info ───────────────────────────────────────────────────
const selectedModel = ref("")
const availableModels = ref([])

onMounted(() => {
  loadModels()
})

watch(
  [() => props.instance?.model, () => chat.sessionInfo.model],
  ([instanceModel, sessionModel]) => {
    const best = sessionModel || instanceModel || ""
    if (best && best !== selectedModel.value) {
      selectedModel.value = best
    }
  },
  { immediate: true },
)

const currentModelProfile = computed(() => {
  const modelName = selectedModel.value || chat.sessionInfo.model || props.instance?.model || ""
  return availableModels.value.find((m) => m.name === modelName) || null
})

async function loadModels() {
  try {
    const models = await configAPI.getModels()
    availableModels.value = (models || []).filter((m) => m.available !== false)
  } catch {
    availableModels.value = []
  }
}

// ── Token usage ──────────────────────────────────────────────────
const totalUsage = computed(() => {
  let prompt = 0
  let completion = 0
  let cached = 0
  let lastPrompt = 0
  for (const usage of Object.values(chat.tokenUsage)) {
    prompt += usage.prompt || 0
    completion += usage.completion || 0
    cached += usage.cached || 0
    if ((usage.lastPrompt || 0) > lastPrompt) lastPrompt = usage.lastPrompt || 0
  }
  return { prompt, completion, cached, lastPrompt }
})

const maxContext = computed(() => chat.sessionInfo.maxContext || props.instance?.max_context || 0)

const contextPct = computed(() => {
  if (!maxContext.value || !totalUsage.value.lastPrompt) return 0
  return Math.round((totalUsage.value.lastPrompt / maxContext.value) * 100)
})

const compactThreshold = computed(() => chat.sessionInfo.compactThreshold || props.instance?.compact_threshold || 0)

const compactThresholdPct = computed(() => {
  if (!maxContext.value || !compactThreshold.value) return 0
  return Math.min(100, Math.round((compactThreshold.value / maxContext.value) * 100))
})

// ── Jobs ─────────────────────────────────────────────────────────
const jobCount = computed(() => Object.keys(chat.runningJobs).length)

// ── Helpers ──────────────────────────────────────────────────────
function formatTokens(n) {
  if (!n) return "0"
  if (n >= 1000000) return (n / 1000000).toFixed(1) + "M"
  if (n >= 1000) return (n / 1000).toFixed(1) + "K"
  return String(n)
}

async function stopTask(jobId) {
  try {
    const tab = chat.activeTab
    if (chat._instanceType === "terrarium") {
      await terrariumAPI.stopCreatureTask(chat._instanceId, tab || "root", jobId)
    } else {
      await agentAPI.stopTask(chat._instanceId, jobId)
    }
    const job = chat.runningJobs[jobId]
    if (job) job.cancelling = true
  } catch (err) {
    console.error("Failed to stop task:", err)
  }
}
</script>

<style scoped>
.section-label {
  @apply text-warm-400 mb-1.5 uppercase tracking-wider text-[10px] font-medium;
}
</style>
