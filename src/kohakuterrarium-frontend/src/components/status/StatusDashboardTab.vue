<template>
  <div class="h-full flex bg-warm-50 dark:bg-warm-900 overflow-hidden">
    <div class="flex flex-col gap-1 py-2 px-1 border-r border-warm-200 dark:border-warm-700 shrink-0">
      <button v-for="tab in visibleTabs" :key="tab.id" class="relative w-8 h-8 flex items-center justify-center rounded text-warm-400 hover:text-warm-600 dark:hover:text-warm-300 transition-colors" :class="[activeTab === tab.id ? 'bg-iolite/10 text-iolite' : '', tab.id === 'creatures' && isMulti && activeTab !== 'creatures' ? 'text-iolite/80' : '', tab.id === 'creatures' && graphGrew ? 'rail-glow' : '']" :title="tab.id === 'creatures' && graphGrew ? `${tab.label} · ${t('status.graphGrew')}` : tab.label" :data-testid="`status-tab-${tab.id}`" @click="activeTab = tab.id">
        <div :class="tab.icon" class="text-sm" />
        <span v-if="tab.id === 'jobs' && jobCount > 0" class="absolute -top-0.5 -right-0.5 w-3.5 h-3.5 rounded-full bg-amber text-white text-[8px] font-bold flex items-center justify-center">{{ jobCount > 9 ? "9+" : jobCount }}</span>
        <span v-if="tab.id === 'creatures' && creatureCount > 0" class="absolute -top-0.5 -right-0.5 w-3.5 h-3.5 rounded-full bg-iolite text-white text-[8px] font-bold flex items-center justify-center" data-testid="status-creature-count">{{ creatureCount > 9 ? "9+" : creatureCount }}</span>
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
              <span class="text-warm-600 dark:text-warm-400" :title="configRef">{{ configLabel }}</span>
            </div>
            <div class="flex items-center gap-2">
              <span class="text-warm-400 w-16">{{ t("common.model") }}</span>
              <span class="text-iolite font-mono text-[11px] break-all">{{ modelLabel }}</span>
            </div>
            <div class="flex items-center gap-2">
              <span class="text-warm-400 w-16">{{ t("common.provider") }}</span>
              <span class="text-warm-600 dark:text-warm-400 text-[11px]">{{ currentModelProfile?.login_provider || instance?.provider || "--" }}</span>
            </div>
            <div class="flex items-center gap-2">
              <span class="text-warm-400 w-16">{{ t("common.session") }}</span>
              <span class="text-warm-600 dark:text-warm-400 font-mono text-[10px] truncate max-w-32">{{ sessionIdLabel }}</span>
            </div>
            <div v-if="instance?.status" class="flex items-center gap-2">
              <span class="text-warm-400 w-16">{{ t("common.status") }}</span>
              <span class="text-[10px] px-1.5 py-0.5 rounded" :class="instance.status === 'running' ? 'bg-aquamarine/10 text-aquamarine' : 'bg-warm-100 dark:bg-warm-800 text-warm-400'">{{ statusLabel(instance.status, instance.status) }}</span>
            </div>
          </div>
        </template>

        <!-- Creatures: the graph's members with focus, lifecycle, and model
             controls, so a multi-creature session is operable from the
             default layout. -->
        <template v-else-if="activeTab === 'creatures'">
          <div v-if="creatureError" class="text-coral text-[11px] mb-2">{{ creatureError }}</div>
          <div v-if="creatures.length === 0" class="text-warm-400 py-6 text-center text-[11px]">{{ t("status.noCreatures") }}</div>
          <div v-else class="flex flex-col gap-1.5">
            <div v-for="c in creatures" :key="c.creature_id || c.name" class="rounded border border-warm-200 dark:border-warm-700 px-2 py-1.5 flex flex-col gap-1" :class="chat.activeTab === c.name ? 'bg-iolite/5 border-iolite/40' : ''" :data-testid="`status-creature-${c.name}`">
              <div class="flex items-center gap-2 min-w-0">
                <StatusDot :status="c.status" />
                <button type="button" class="font-medium text-warm-700 dark:text-warm-300 truncate flex-1 text-left hover:text-iolite transition-colors" :title="c.name" @click="chat.openTab(c.name)">{{ c.name }}</button>
                <span class="text-[10px] px-1.5 py-0.5 rounded shrink-0" :class="c.status === 'running' ? 'bg-aquamarine/10 text-aquamarine' : 'bg-warm-100 dark:bg-warm-800 text-warm-400'">{{ statusLabel(c.status, c.status) }}</span>
                <button v-if="c.status === 'running'" type="button" class="text-[10px] px-1.5 py-0.5 rounded border border-coral/30 text-coral hover:bg-coral/10 transition-colors disabled:opacity-50 shrink-0" :disabled="!!busy[c.name]" :data-testid="`status-stop-${c.name}`" @click="stopCreature(c)">{{ t("status.stopCreature") }}</button>
                <button v-else type="button" class="text-[10px] px-1.5 py-0.5 rounded border border-aquamarine/30 text-aquamarine hover:bg-aquamarine/10 transition-colors disabled:opacity-50 shrink-0" :disabled="!!busy[c.name]" :data-testid="`status-start-${c.name}`" @click="startCreature(c)">{{ t("status.startCreature") }}</button>
              </div>
              <div class="flex items-center gap-2">
                <span class="text-warm-400 w-10 shrink-0">{{ t("common.model") }}</span>
                <el-select :model-value="creatureModel(c)" size="small" class="flex-1 min-w-0" :placeholder="t('status.selectModel')" :title="t('status.creatureModel', { name: c.name })" @change="(m) => switchCreatureModel(c, m)">
                  <el-option v-for="model in availableModels" :key="`${model.provider || model.login_provider || ''}/${model.name}`" :label="`${model.provider || model.login_provider || ''}/${model.name}`" :value="`${model.provider || model.login_provider || ''}/${model.name}`" />
                </el-select>
              </div>
            </div>

            <template v-if="channels.length">
              <div class="text-[10px] uppercase tracking-wider text-warm-400 font-medium mt-2">{{ t("status.channels") }}</div>
              <div v-for="ch in channels" :key="ch.name" class="flex items-center gap-2 px-2 py-1 rounded cursor-pointer transition-colors hover:bg-warm-100 dark:hover:bg-warm-800" :class="chat.activeTab === `ch:${ch.name}` ? 'bg-taaffeite/10' : ''" @click="chat.openTab(`ch:${ch.name}`)">
                <span class="w-2 h-2 rounded-sm shrink-0" :class="ch.type === 'broadcast' ? 'bg-taaffeite' : 'bg-aquamarine'" />
                <span class="font-medium text-warm-700 dark:text-warm-300 truncate">{{ ch.name }}</span>
              </div>
            </template>
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
              <button class="text-warm-400 hover:text-coral transition-colors hover-only-action" :title="t('common.stopTask')" :aria-label="t('common.stopTask')" @click="stopTask(jobId)">
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
import { computed, onMounted, reactive, ref, watch } from "vue"

import StatusDot from "@/components/common/StatusDot.vue"
import ModulesPanel from "@/components/panels/modules/ModulesPanel.vue"
import { useChatStore } from "@/stores/chat"
import { useInstancesStore } from "@/stores/instances"
import { useI18n } from "@/utils/i18n"
import { configAPI, terrariumAPI } from "@/utils/api"

const props = defineProps({
  instance: { type: Object, default: null },
  onOpenTab: { type: Function, default: () => {} },
})

const chat = useChatStore()
const instances = useInstancesStore()
const { t, statusLabel } = useI18n()

const allTabs = computed(() => [
  { id: "session", label: t("common.session"), icon: "i-carbon-information" },
  { id: "creatures", label: t("common.creatures"), icon: "i-carbon-network-4" },
  { id: "tokens", label: t("status.tokenUsage"), icon: "i-carbon-meter" },
  { id: "jobs", label: t("status.runningJobs"), icon: "i-carbon-play-outline" },
  { id: "modules", label: "Modules", icon: "i-carbon-3d-mpr-toggle" },
])
const activeTab = ref("session")

// A solo session has nothing to walk: the Creatures tab, its count, and its
// glow exist only once the graph holds more than one creature.
const visibleTabs = computed(() => allTabs.value.filter((tab) => tab.id !== "creatures" || isMulti.value))
const activeLabel = computed(() => allTabs.value.find((tab) => tab.id === activeTab.value)?.label || "")

const selectedModel = ref("")
const availableModels = ref([])

const configLabel = computed(() => chat.activeCreatureInfo.configName || props.instance?.creature_config_name || props.instance?.creatures?.[0]?.config_name || chat.sessionInfo.agentName || props.instance?.config_name || props.instance?.creatures?.[0]?.name || "--")
const configRef = computed(() => chat.activeCreatureInfo.configRef || props.instance?.config_ref || props.instance?.creatures?.[0]?.config_ref || "")
const modelLabel = computed(() => chat.modelDisplay || props.instance?.llm_name || props.instance?.model || "--")
const sessionIdLabel = computed(() => chat.sessionInfo.sessionId || props.instance?.session_id || props.instance?.id || "--")

// ── Creatures ─────────────────────────────────────────────────
const creatures = computed(() => props.instance?.creatures || [])
const channels = computed(() => props.instance?.channels || [])
const creatureCount = computed(() => creatures.value.length)
const isMulti = computed(() => creatureCount.value > 1)
const graphSize = computed(() => creatureCount.value + channels.value.length)
// The rail icon glows once the graph grows past what the user last looked
// at, so a spawned creature or a new channel is never silent.
const lastSeenSize = ref(graphSize.value)
const graphGrew = computed(() => isMulti.value && activeTab.value !== "creatures" && graphSize.value > lastSeenSize.value)
watch(activeTab, (tab) => {
  if (tab === "creatures") lastSeenSize.value = graphSize.value
})
watch(graphSize, (size) => {
  if (activeTab.value === "creatures") lastSeenSize.value = size
})
// Losing the second creature removes the tab; never strand the rail on it.
watch(isMulti, (multi) => {
  if (!multi && activeTab.value === "creatures") activeTab.value = "session"
})

const busy = reactive({})
const creatureError = ref("")
const sid = computed(() => props.instance?.graph_id || props.instance?.id || "")

function creatureModel(c) {
  return c.llm_name || c.model || ""
}

async function _lifecycle(c, verb) {
  if (!sid.value || busy[c.name]) return
  busy[c.name] = true
  creatureError.value = ""
  try {
    if (verb === "start") await terrariumAPI.startCreature(sid.value, c.name)
    else await terrariumAPI.stopCreature(sid.value, c.name)
    await instances.fetchOne(sid.value)
  } catch (err) {
    creatureError.value = err?.response?.data?.detail || err?.message || String(err)
  } finally {
    busy[c.name] = false
  }
}

function startCreature(c) {
  return _lifecycle(c, "start")
}

function stopCreature(c) {
  return _lifecycle(c, "stop")
}

async function switchCreatureModel(c, modelId) {
  if (!sid.value || !modelId) return
  creatureError.value = ""
  try {
    await terrariumAPI.switchCreatureModel(sid.value, c.name, modelId)
    await instances.fetchOne(sid.value)
  } catch (err) {
    creatureError.value = err?.response?.data?.detail || t("status.modelSwitchError")
  }
}

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

const maxContext = computed(() => chat.activeModelInfo.maxContext || props.instance?.max_context || 0)

const contextPct = computed(() => {
  if (!maxContext.value || !totalUsage.value.lastPrompt) return 0
  return Math.round((totalUsage.value.lastPrompt / maxContext.value) * 100)
})

const compactThreshold = computed(() => chat.activeModelInfo.compactThreshold || props.instance?.compact_threshold || 0)

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
    const sessionId = chat._instanceGraphId || chat._instanceId
    const job = chat.runningJobs[jobId]
    // Route to the job's OWN creature, not whatever tab is active — a
    // background job started on tab A must be cancellable while the user
    // is looking at tab B.
    const tab = job?.tab || chat.activeTab || "root"
    await terrariumAPI.stopCreatureTask(sessionId, tab, jobId)
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

@keyframes rail-glow {
  0%,
  100% {
    box-shadow: 0 0 0 0 rgba(90, 79, 207, 0);
  }
  50% {
    box-shadow: 0 0 0 4px rgba(90, 79, 207, 0.35);
  }
}
.rail-glow {
  animation: rail-glow 1.6s ease-in-out infinite;
  color: rgb(90, 79, 207);
}
</style>
