<template>
  <div class="h-full flex bg-warm-50 dark:bg-warm-900 overflow-hidden">
    <div class="flex flex-col gap-1 py-2 px-1 border-r border-warm-200 dark:border-warm-700 shrink-0">
      <button v-for="tab in visibleTabs" :key="tab.id" class="relative w-8 h-8 flex items-center justify-center rounded text-warm-400 hover:text-warm-600 dark:hover:text-warm-300 transition-colors" :class="activeTab === tab.id ? 'bg-iolite/10 text-iolite' : ''" :title="tab.label" @click="activeTab = tab.id">
        <div :class="tab.icon" class="text-sm" />
        <span v-if="tab.id === 'jobs' && jobCount > 0" class="absolute -top-0.5 -right-0.5 w-3.5 h-3.5 rounded-full bg-amber text-white text-[8px] font-bold flex items-center justify-center">{{ jobCount > 9 ? "9+" : jobCount }}</span>
      </button>
    </div>

    <div class="flex-1 min-w-0 flex flex-col overflow-hidden">
      <div v-if="activeTab !== 'modules'" class="flex items-center gap-2 px-3 py-2 border-b border-warm-200 dark:border-warm-700 shrink-0">
        <span class="text-xs font-medium text-warm-500 dark:text-warm-400 flex-1">{{ activeLabel }}</span>
      </div>

      <!-- The "Modules" inner tab embeds the full ModulesPanel so
           the workspace preset (which uses status-tab) gets module
           access without adding a fourth screen panel. ModulesPanel
           manages its own header (type tabs, search, refresh) so we
           skip the wrapper header in this case. -->
      <ModulesPanel v-if="activeTab === 'modules'" :instance="instance" class="flex-1 min-h-0" />

      <div v-else class="flex-1 overflow-y-auto px-3 py-2 text-xs">
        <template v-if="activeTab === 'session'">
          <div class="flex flex-col gap-1.5">
            <div class="flex items-center gap-2">
              <span class="text-warm-400 w-16">{{ t("common.agent") }}</span>
              <span class="text-warm-600 dark:text-warm-400">{{ chat.sessionInfo.agentName || instance?.config_name || "--" }}</span>
            </div>
            <div class="flex items-center gap-2">
              <span class="text-warm-400 w-16">{{ t("common.model") }}</span>
              <span class="text-iolite font-mono text-[11px] break-all">{{ chat.modelDisplay || instance?.llm_name || instance?.model || "--" }}</span>
            </div>
            <div class="flex items-center gap-2">
              <span class="text-warm-400 w-16">{{ t("common.provider") }}</span>
              <span class="text-warm-600 dark:text-warm-400 text-[11px]">{{ currentModelProfile?.login_provider || instance?.provider || "--" }}</span>
            </div>
            <div class="flex items-center gap-2">
              <span class="text-warm-400 w-16">{{ t("common.session") }}</span>
              <span class="text-warm-600 dark:text-warm-400 font-mono text-[10px] truncate max-w-32">{{ chat.sessionInfo.sessionId || instance?.session_id || "--" }}</span>
            </div>
            <div v-if="instance?.status" class="flex items-center gap-2">
              <span class="text-warm-400 w-16">{{ t("common.status") }}</span>
              <span class="text-[10px] px-1.5 py-0.5 rounded" :class="instance.status === 'running' ? 'bg-aquamarine/10 text-aquamarine' : 'bg-warm-100 dark:bg-warm-800 text-warm-400'">{{ statusLabel(instance.status, instance.status) }}</span>
            </div>
          </div>
        </template>

        <template v-else-if="activeTab === 'tokens'">
          <div class="flex flex-col gap-1.5">
            <div class="flex items-center gap-2">
              <span class="text-warm-400 w-20">{{ t("status.promptIn") }}</span>
              <span class="text-warm-600 dark:text-warm-400 font-mono">{{ formatTokens(totalUsage.prompt) }}</span>
            </div>
            <div class="flex items-center gap-2">
              <span class="text-warm-400 w-20">{{ t("common.completion") }}</span>
              <span class="text-warm-600 dark:text-warm-400 font-mono">{{ formatTokens(totalUsage.completion) }}</span>
            </div>
            <div v-if="totalUsage.cached > 0" class="flex items-center gap-2">
              <span class="text-warm-400 w-20">{{ t("common.cached") }}</span>
              <span class="text-aquamarine font-mono">{{ formatTokens(totalUsage.cached) }}</span>
            </div>
            <div v-if="maxContext > 0" class="mt-1">
              <div class="flex items-center justify-between mb-1">
                <span class="text-warm-400">{{ t("common.context") }}</span>
                <span class="font-mono text-[10px]" :class="contextPct >= 80 ? 'text-coral' : contextPct >= 60 ? 'text-amber' : 'text-warm-500'">{{ formatTokens(totalUsage.lastPrompt) }} / {{ formatTokens(maxContext) }} ({{ contextPct }}%)</span>
              </div>
              <div class="relative w-full h-1.5 rounded-full bg-warm-100 dark:bg-warm-800 overflow-hidden">
                <div class="h-full rounded-full transition-all duration-300" :class="contextPct >= 80 ? 'bg-coral' : contextPct >= 60 ? 'bg-amber' : 'bg-aquamarine'" :style="{ width: Math.min(contextPct, 100) + '%' }" />
                <div v-if="compactThresholdPct > 0" class="absolute top-0 h-full w-0.5 bg-amber opacity-60" :style="{ left: compactThresholdPct + '%' }" :title="t('status.compactAt', { value: formatTokens(compactThreshold) })" />
              </div>
            </div>
          </div>
        </template>

        <template v-else-if="activeTab === 'jobs'">
          <div v-if="jobCount === 0" class="text-warm-400 py-6 text-center text-[11px]">{{ t("status.noRunningJobs") }}</div>
          <div v-else class="flex flex-col gap-1">
            <div v-for="(job, jobId) in chat.runningJobs" :key="jobId" class="flex items-center gap-2 px-2 py-1.5 rounded-md bg-amber/10 group">
              <span class="w-1.5 h-1.5 rounded-full bg-amber kohaku-pulse shrink-0" />
              <span class="font-mono text-[11px] text-amber truncate">{{ job.name }}</span>
              <span class="flex-1" />
              <span class="text-warm-400 font-mono text-[10px]">{{ chat.getJobElapsed(job) }}</span>
              <button class="text-warm-400 hover:text-coral transition-colors opacity-0 group-hover:opacity-100" :title="t('common.stopTask')" :aria-label="t('common.stopTask')" @click="stopTask(jobId)">
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

import ModulesPanel from "@/components/panels/modules/ModulesPanel.vue"
import { useChatStore } from "@/stores/chat"
import { useI18n } from "@/utils/i18n"
import { agentAPI, configAPI, terrariumAPI } from "@/utils/api"

const props = defineProps({
  instance: { type: Object, default: null },
  onOpenTab: { type: Function, default: () => {} },
})

const chat = useChatStore()
const { t, statusLabel } = useI18n()

const allTabs = computed(() => [
  { id: "session", label: t("common.session"), icon: "i-carbon-information" },
  { id: "tokens", label: t("status.tokenUsage"), icon: "i-carbon-meter" },
  { id: "jobs", label: t("status.runningJobs"), icon: "i-carbon-play-outline" },
  { id: "modules", label: "Modules", icon: "i-carbon-3d-mpr-toggle" },
])
const activeTab = ref("session")

const visibleTabs = computed(() => allTabs.value)
const activeLabel = computed(() => allTabs.value.find((tab) => tab.id === activeTab.value)?.label || "")

const selectedModel = ref("")
const availableModels = ref([])

onMounted(() => {
  loadModels()
})

watch(
  [() => props.instance?.llm_name, () => props.instance?.model, () => chat.modelDisplay],
  ([instanceIdent, instanceModel, active]) => {
    const best = active || instanceIdent || instanceModel || ""
    if (best && best !== selectedModel.value) {
      selectedModel.value = best
    }
  },
  { immediate: true },
)

const currentModelProfile = computed(() => {
  // Active ``selectedModel`` may be ``provider/name[@variations]`` —
  // strip the variation suffix and the optional provider prefix so we
  // can match the preset catalog entry precisely even when bare names
  // collide across providers.
  const raw = selectedModel.value || chat.modelDisplay || props.instance?.llm_name || props.instance?.model || ""
  const base = raw.split("@", 1)[0]
  const slash = base.indexOf("/")
  const wantProvider = slash >= 0 ? base.slice(0, slash) : ""
  const wantName = slash >= 0 ? base.slice(slash + 1) : base
  const entries = availableModels.value || []
  return entries.find((m) => m.name === wantName && (!wantProvider || (m.provider || m.login_provider) === wantProvider)) || entries.find((m) => m.name === wantName) || null
})

async function loadModels() {
  try {
    const models = await configAPI.getModels()
    availableModels.value = (models || []).filter((model) => model.available !== false)
  } catch {
    availableModels.value = []
  }
}

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

const jobCount = computed(() => Object.keys(chat.runningJobs).length)

function formatTokens(value) {
  if (!value) return "0"
  if (value >= 1000000) return (value / 1000000).toFixed(1) + "M"
  if (value >= 1000) return (value / 1000).toFixed(1) + "K"
  return String(value)
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
