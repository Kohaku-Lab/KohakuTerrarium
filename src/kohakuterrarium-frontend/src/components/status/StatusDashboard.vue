<template>
  <div class="h-full overflow-y-auto bg-white dark:bg-warm-900">
    <div class="flex flex-col gap-3 p-3 text-xs">
      <template v-if="instance?.type !== 'terrarium'">
        <div class="rounded-lg border border-warm-200 dark:border-warm-700 p-4">
          <div class="flex items-center gap-2 mb-3">
            <StatusDot :status="instance?.status" />
            <span class="font-semibold text-warm-700 dark:text-warm-300 text-sm">
              {{ instance?.config_name }}
            </span>
          </div>
          <div class="flex flex-col gap-1.5 text-warm-500">
            <div class="flex items-center gap-2">
              <span class="text-warm-400 w-12">{{ t("common.name") }}</span>
              <span class="text-warm-600 dark:text-warm-400">
                {{ instance?.creatures?.[0]?.name || instance?.config_name }}
              </span>
            </div>
            <div class="flex items-center gap-2">
              <span class="text-warm-400 w-12">{{ t("common.model") }}</span>
              <span class="text-warm-600 dark:text-warm-400 font-mono text-[11px] break-all">
                {{ chat.modelDisplay || instance?.llm_name || instance?.model || "default" }}
              </span>
            </div>
          </div>
        </div>
      </template>

      <div class="border-t border-warm-200 dark:border-warm-700" />

      <div v-if="instance?.type === 'terrarium'" class="rounded-lg border border-warm-200 dark:border-warm-700 p-3">
        <div class="section-label">{{ t("common.target") }}</div>
        <div class="flex flex-col gap-1.5">
          <div class="flex items-center gap-2">
            <span class="text-warm-400 w-16">{{ t("common.type") }}</span>
            <span class="text-warm-600 dark:text-warm-400 capitalize">{{ currentTargetKindLabel }}</span>
          </div>
          <div class="flex items-center gap-2">
            <span class="text-warm-400 w-16">{{ t("common.name") }}</span>
            <span class="text-warm-600 dark:text-warm-400 font-mono text-[11px] break-all">{{ currentTargetLabel }}</span>
          </div>
          <div v-if="canSwitchTargetModel" class="flex items-start gap-2">
            <span class="text-warm-400 w-16 pt-1">{{ t("common.model") }}</span>
            <div class="flex-1 min-w-0 flex flex-col gap-2">
              <span class="text-iolite font-mono text-[11px] break-all">{{ sessionModel || instance?.llm_name || instance?.model || "--" }}</span>
              <div class="flex items-center gap-2">
                <el-select v-model="selectedModel" size="small" class="flex-1 min-w-0" :placeholder="t('status.selectModel')" :loading="modelsLoading" @change="handleModelSwitch">
                  <el-option v-for="model in availableModels" :key="`${model.provider || model.login_provider || ''}/${model.name}`" :label="`${model.provider || model.login_provider || ''}/${model.name}`" :value="`${model.provider || model.login_provider || ''}/${model.name}`" />
                </el-select>
              </div>
              <div v-if="modelSwitchError" class="text-coral text-[10px]">{{ modelSwitchError }}</div>
            </div>
          </div>
          <div v-else class="flex items-center gap-2">
            <span class="text-warm-400 w-16">{{ t("common.model") }}</span>
            <span class="text-warm-500 dark:text-warm-400 text-[11px]">{{ t("status.modelSwitchHint") }}</span>
          </div>
        </div>
      </div>

      <div class="rounded-lg border border-warm-200 dark:border-warm-700 p-3">
        <div class="section-label">{{ t("common.session") }}</div>
        <div class="flex flex-col gap-1.5">
          <div class="flex items-center gap-2">
            <span class="text-warm-400 w-16">{{ t("common.agent") }}</span>
            <span class="text-warm-600 dark:text-warm-400">
              {{ chat.sessionInfo.agentName || instance?.config_name || instance?.creatures?.[0]?.name || "--" }}
            </span>
          </div>
          <div class="flex items-center gap-2">
            <span class="text-warm-400 w-16">{{ t("common.model") }}</span>
            <span class="text-iolite font-mono text-[11px] break-all">
              {{ chat.modelDisplay || instance?.llm_name || instance?.model || "--" }}
            </span>
          </div>
          <div class="flex items-center gap-2">
            <span class="text-warm-400 w-16">{{ t("common.provider") }}</span>
            <span class="text-warm-600 dark:text-warm-400 text-[11px]">
              {{ currentModelProfile?.login_provider || instance?.provider || "--" }}
            </span>
          </div>
          <div class="flex items-center gap-2">
            <span class="text-warm-400 w-16">{{ t("common.session") }}</span>
            <span class="text-warm-600 dark:text-warm-400 font-mono text-[10px] truncate max-w-32">
              {{ chat.sessionInfo.sessionId || instance?.session_id || instance?.id || "--" }}
            </span>
          </div>
        </div>
      </div>

      <div class="rounded-lg border border-warm-200 dark:border-warm-700 p-3">
        <div class="section-label">{{ t("status.tokenUsage") }}</div>
        <div class="flex flex-col gap-1.5">
          <div class="flex items-center gap-2">
            <span class="text-warm-400 w-20">{{ t("status.promptIn") }}</span>
            <span class="text-warm-600 dark:text-warm-400 font-mono">
              {{ formatTokens(totalUsage.prompt) }}
            </span>
          </div>
          <div class="flex items-center gap-2">
            <span class="text-warm-400 w-20">{{ t("common.completion") }}</span>
            <span class="text-warm-600 dark:text-warm-400 font-mono">
              {{ formatTokens(totalUsage.completion) }}
            </span>
          </div>
          <div v-if="totalUsage.cached > 0" class="flex items-center gap-2">
            <span class="text-warm-400 w-20">{{ t("common.cached") }}</span>
            <span class="text-aquamarine font-mono">
              {{ formatTokens(totalUsage.cached) }}
            </span>
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
      </div>

      <div class="rounded-lg border border-warm-200 dark:border-warm-700 p-3">
        <div class="section-label">{{ t("status.runningJobs") }}</div>
        <div v-if="Object.keys(chat.runningJobs).length === 0" class="text-warm-400 py-2 text-center">{{ t("status.noRunningJobs") }}</div>
        <div v-else class="flex flex-col gap-1.5">
          <div v-for="(job, jobId) in chat.runningJobs" :key="jobId" class="flex items-center gap-2 px-2 py-1.5 rounded-md bg-amber/10 group">
            <span class="w-1.5 h-1.5 rounded-full bg-amber kohaku-pulse shrink-0" />
            <span class="font-mono text-[11px] text-amber-shadow dark:text-amber-light truncate">
              {{ job.name }}
            </span>
            <span class="flex-1" />
            <span class="text-warm-400 font-mono text-[10px]">
              {{ chat.getJobElapsed(job) }}
            </span>
            <button class="text-warm-400 hover:text-coral transition-colors opacity-0 group-hover:opacity-100" :title="t('common.stopTask')" :aria-label="t('common.stopTask')" @click="stopTask(jobId, job.name)">
              <span class="i-carbon-close text-[10px]" />
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import StatusDot from "@/components/common/StatusDot.vue"
import { useChatStore } from "@/stores/chat"
import { useI18n } from "@/utils/i18n"
import { agentAPI, configAPI, terrariumAPI } from "@/utils/api"

const props = defineProps({
  instance: { type: Object, default: null },
  onOpenTab: { type: Function, default: () => {} },
})

const chat = useChatStore()
const { t } = useI18n()

const selectedModel = ref("")
const modelsLoading = ref(false)
const modelSwitchError = ref("")
const availableModels = ref([])

onMounted(() => {
  loadModels()
})

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

const currentTarget = computed(() => {
  if (props.instance?.type !== "terrarium") return null
  return chat.terrariumTarget
})

const currentTargetKindLabel = computed(() => {
  const target = currentTarget.value
  if (target === "root") return t("status.targetKind.root")
  if (target?.startsWith("ch:")) return t("status.targetKind.channel")
  if (target) return t("status.targetKind.creature")
  return t("status.targetKind.session")
})

const currentTargetLabel = computed(() => {
  const target = currentTarget.value
  if (target === "root") return props.instance?.config_name || t("common.rootAgent")
  if (target?.startsWith("ch:")) return target.slice(3)
  if (target) return target
  return t("status.noTargetSelected")
})

const canSwitchTargetModel = computed(() => {
  if (props.instance?.type !== "terrarium") return !!props.instance?.id
  const target = currentTarget.value
  return !!props.instance?.id && !!target && !target.startsWith("ch:")
})

const sessionModel = computed(() => {
  // Prefer the canonical ``provider/name[@variations]`` identifier so
  // the dashboard matches the ModelSwitcher pill and ``/model`` output.
  const activeIdentifier = chat.modelDisplay
  if (props.instance?.type !== "terrarium") {
    return activeIdentifier || props.instance?.llm_name || props.instance?.model || ""
  }
  if (currentTarget.value === "root") {
    return activeIdentifier || props.instance?.llm_name || props.instance?.model || ""
  }
  if (currentTarget.value) {
    const creature = props.instance.creatures?.find((creature) => creature.name === currentTarget.value)
    return activeIdentifier || creature?.llm_name || creature?.model || ""
  }
  return ""
})

const currentModelProfile = computed(() => {
  // The active identifier can be bare ``name``, ``provider/name``, or
  // ``provider/name@group=option,...`` — parse the latter two so we
  // can match the preset catalog entry precisely.
  const raw = selectedModel.value || sessionModel.value || props.instance?.llm_name || props.instance?.model || ""
  const base = raw.split("@", 1)[0]
  const slash = base.indexOf("/")
  const provider = slash >= 0 ? base.slice(0, slash) : ""
  const name = slash >= 0 ? base.slice(slash + 1) : base
  const entries = availableModels.value || []
  return entries.find((m) => m.name === name && (!provider || (m.provider || m.login_provider) === provider)) || entries.find((m) => m.name === name) || null
})

watch(
  [() => props.instance, sessionModel],
  ([instanceValue, activeModel]) => {
    const best = activeModel || instanceValue?.model || ""
    if (best !== selectedModel.value) {
      selectedModel.value = best
    }
  },
  { immediate: true },
)

function formatTokens(value) {
  if (!value) return "0"
  if (value >= 1000000) return (value / 1000000).toFixed(1) + "M"
  if (value >= 1000) return (value / 1000).toFixed(1) + "K"
  return String(value)
}

async function stopTask(jobId, jobName) {
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

async function loadModels() {
  modelsLoading.value = true
  try {
    const models = await configAPI.getModels()
    availableModels.value = (models || []).filter((model) => model.available !== false)
  } catch {
    availableModels.value = []
  } finally {
    modelsLoading.value = false
  }
}

async function handleModelSwitch(modelId) {
  if (!props.instance?.id) return
  modelSwitchError.value = ""
  try {
    if (props.instance.type === "terrarium") {
      const target = currentTarget.value
      if (!target) {
        modelSwitchError.value = t("status.modelSwitchHint")
        return
      }
      await terrariumAPI.switchCreatureModel(props.instance.id, target, modelId)
    } else {
      await agentAPI.switchModel(props.instance.id, modelId)
    }
  } catch (err) {
    modelSwitchError.value = err.response?.data?.detail || t("status.modelSwitchError")
    selectedModel.value = sessionModel.value || ""
  }
}
</script>

<style scoped>
.section-label {
  @apply text-warm-400 mb-1.5 uppercase tracking-wider text-[10px] font-medium;
}

:deep(.el-select) {
  --el-fill-color-blank: var(--color-surface);
  --el-border-color: var(--color-border);
  --el-text-color-regular: var(--color-text);
}
</style>
