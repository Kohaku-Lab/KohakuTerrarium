<template>
  <div class="h-full overflow-y-auto bg-white dark:bg-warm-900">
    <div class="flex flex-col gap-3 p-3 text-xs">
      <!-- ── Overview Section ── -->
      <template v-if="instance?.type === 'terrarium'">
        <!-- Creature list -->
        <div>
          <div class="section-label">Creatures</div>
          <div class="flex flex-col gap-1 max-h-48 overflow-y-auto">
            <div
              v-for="c in instance.creatures"
              :key="c.name"
              class="flex items-center gap-2 px-2.5 py-2 rounded-lg cursor-pointer transition-colors hover:bg-warm-100 dark:hover:bg-warm-800"
              @click="onOpenTab(c.name)"
            >
              <StatusDot :status="c.status" />
              <span class="font-medium text-warm-700 dark:text-warm-300">{{ c.name }}</span>
              <span class="flex-1" />
              <span
                class="text-[10px] px-1.5 py-0.5 rounded"
                :class="c.status === 'running'
                  ? 'bg-aquamarine/10 text-aquamarine'
                  : 'bg-warm-100 dark:bg-warm-800 text-warm-400'"
              >{{ c.status }}</span>
            </div>
          </div>
        </div>

        <!-- Channel list -->
        <div>
          <div class="section-label">Channels</div>
          <div class="flex flex-col gap-1 max-h-48 overflow-y-auto">
            <div
              v-for="ch in instance.channels"
              :key="ch.name"
              class="flex items-center gap-2 px-2.5 py-2 rounded-lg cursor-pointer transition-colors hover:bg-warm-100 dark:hover:bg-warm-800"
              @click="onOpenTab('ch:' + ch.name)"
            >
              <span
                class="w-2 h-2 rounded-sm shrink-0"
                :class="ch.type === 'broadcast' ? 'bg-taaffeite' : 'bg-aquamarine'"
              />
              <span class="font-medium text-warm-700 dark:text-warm-300">{{ ch.name }}</span>
              <span class="flex-1" />
              <GemBadge
                v-if="ch.message_count"
                :gem="ch.type === 'broadcast' ? 'taaffeite' : 'aquamarine'"
              >{{ ch.message_count }}</GemBadge>
              <span
                class="text-[10px] px-1.5 py-0.5 rounded bg-warm-100 dark:bg-warm-800 text-warm-400"
              >{{ ch.type }}</span>
            </div>
          </div>
        </div>
      </template>

      <!-- Standalone creature: agent info card -->
      <template v-else>
        <div class="rounded-lg border border-warm-200 dark:border-warm-700 p-4">
          <div class="flex items-center gap-2 mb-3">
            <StatusDot :status="instance?.status" />
            <span class="font-semibold text-warm-700 dark:text-warm-300 text-sm">
              {{ instance?.config_name }}
            </span>
          </div>
          <div class="flex flex-col gap-1.5 text-warm-500">
            <div class="flex items-center gap-2">
              <span class="text-warm-400 w-12">Name</span>
              <span class="text-warm-600 dark:text-warm-400">
                {{ instance?.creatures?.[0]?.name || instance?.config_name }}
              </span>
            </div>
            <div class="flex items-center gap-2">
              <span class="text-warm-400 w-12">Model</span>
              <span class="text-warm-600 dark:text-warm-400 font-mono text-[11px]">
                {{ chat.sessionInfo.model || 'default' }}
              </span>
            </div>
          </div>
        </div>
      </template>

      <!-- ── Divider ── -->
      <div class="border-t border-warm-200 dark:border-warm-700" />

      <!-- ── Status Section ── -->
      <!-- Session info card -->
      <div class="rounded-lg border border-warm-200 dark:border-warm-700 p-3">
        <div class="section-label">Session</div>
        <div class="flex flex-col gap-1.5">
          <div class="flex items-center gap-2">
            <span class="text-warm-400 w-16">Agent</span>
            <span class="text-warm-600 dark:text-warm-400">
              {{ chat.sessionInfo.agentName || instance?.config_name || '--' }}
            </span>
          </div>
          <div class="flex items-center gap-2">
            <span class="text-warm-400 w-16">Model</span>
            <span class="text-iolite font-mono text-[11px]">
              {{ chat.sessionInfo.model || instance?.model || '--' }}
            </span>
          </div>
          <div class="flex items-center gap-2">
            <span class="text-warm-400 w-16">Provider</span>
            <span class="text-warm-600 dark:text-warm-400 text-[11px]">
              {{ currentModelProfile?.login_provider || instance?.provider || '--' }}
            </span>
          </div>
          <div class="flex items-center gap-2">
            <span class="text-warm-400 w-16">Session</span>
            <span class="text-warm-600 dark:text-warm-400 font-mono text-[10px] truncate max-w-32">
              {{ chat.sessionInfo.sessionId || instance?.session_id || '--' }}
            </span>
          </div>
        </div>
      </div>

      <!-- Token usage -->
      <div class="rounded-lg border border-warm-200 dark:border-warm-700 p-3">
        <div class="section-label">Token Usage</div>
        <div class="flex flex-col gap-1.5">
          <div class="flex items-center gap-2">
            <span class="text-warm-400 w-20">Prompt in</span>
            <span class="text-warm-600 dark:text-warm-400 font-mono">
              {{ formatTokens(totalUsage.prompt) }}
            </span>
          </div>
          <div class="flex items-center gap-2">
            <span class="text-warm-400 w-20">Completion</span>
            <span class="text-warm-600 dark:text-warm-400 font-mono">
              {{ formatTokens(totalUsage.completion) }}
            </span>
          </div>
          <div v-if="totalUsage.cached > 0" class="flex items-center gap-2">
            <span class="text-warm-400 w-20">Cached</span>
            <span class="text-aquamarine font-mono">
              {{ formatTokens(totalUsage.cached) }}
            </span>
          </div>
          <!-- Context usage bar (max_context as total, compact threshold as marker) -->
          <div v-if="maxContext > 0" class="mt-1">
            <div class="flex items-center justify-between mb-1">
              <span class="text-warm-400">Context</span>
              <span
                class="font-mono text-[10px]"
                :class="contextPct >= 80 ? 'text-coral' : contextPct >= 60 ? 'text-amber' : 'text-warm-500'"
              >{{ formatTokens(totalUsage.lastPrompt) }} / {{ formatTokens(maxContext) }} ({{ contextPct }}%)</span>
            </div>
            <div class="relative w-full h-1.5 rounded-full bg-warm-100 dark:bg-warm-800 overflow-hidden">
              <!-- Usage fill -->
              <div
                class="h-full rounded-full transition-all duration-300"
                :class="contextPct >= 80 ? 'bg-coral' : contextPct >= 60 ? 'bg-amber' : 'bg-aquamarine'"
                :style="{ width: Math.min(contextPct, 100) + '%' }"
              />
              <!-- Compact threshold marker line -->
              <div
                v-if="compactThresholdPct > 0"
                class="absolute top-0 h-full w-0.5 bg-amber opacity-60"
                :style="{ left: compactThresholdPct + '%' }"
                :title="'Compact at ' + formatTokens(compactThreshold)"
              />
            </div>
          </div>
        </div>
      </div>

      <!-- Running jobs -->
      <div class="rounded-lg border border-warm-200 dark:border-warm-700 p-3">
        <div class="section-label">Running Jobs</div>
        <div v-if="Object.keys(chat.runningJobs).length === 0" class="text-warm-400 py-2 text-center">
          No running jobs
        </div>
        <div v-else class="flex flex-col gap-1.5">
          <div
            v-for="(job, jobId) in chat.runningJobs"
            :key="jobId"
            class="flex items-center gap-2 px-2 py-1.5 rounded-md bg-amber/10 group"
          >
            <span class="w-1.5 h-1.5 rounded-full bg-amber kohaku-pulse shrink-0" />
            <span class="font-mono text-[11px] text-amber-shadow dark:text-amber-light truncate">
              {{ job.name }}
            </span>
            <span class="flex-1" />
            <span class="text-warm-400 font-mono text-[10px]">
              {{ chat.getJobElapsed(job) }}
            </span>
            <button
              class="text-warm-400 hover:text-coral transition-colors opacity-0 group-hover:opacity-100"
              title="Stop task"
              @click="stopTask(jobId, job.name)"
            >
              <span class="i-carbon-close text-[10px]" />
            </button>
          </div>
        </div>
      </div>

      <!-- Model switching is in the StatusBar at the bottom. -->
    </div>

  </div>
</template>

<script setup>
import { Setting as SettingIcon } from "@element-plus/icons-vue";

import StatusDot from "@/components/common/StatusDot.vue";
import GemBadge from "@/components/common/GemBadge.vue";
import { useChatStore } from "@/stores/chat";
import { useStatusStore } from "@/stores/status";
import { configAPI, agentAPI, terrariumAPI } from "@/utils/api";

const props = defineProps({
  instance: { type: Object, default: null },
  onOpenTab: { type: Function, default: () => {} },
});

const chat = useChatStore();
const status = useStatusStore();

const selectedModel = ref("");
const modelsLoading = ref(false);
const modelSwitchError = ref("");

/** @type {import('vue').Ref<{id: string, name: string}[]>} */
const availableModels = ref([]);

// Model config dialog state
const configDialogVisible = ref(false);
const configJson = ref("");
const configJsonError = ref("");

onMounted(() => {
  loadModels();
});

// Init model from instance data (available immediately) or session info (from WS)
watch(
  [() => props.instance?.model, () => chat.sessionInfo.model],
  ([instanceModel, sessionModel]) => {
    const best = sessionModel || instanceModel || "";
    if (best && best !== selectedModel.value) {
      selectedModel.value = best;
    }
  },
  { immediate: true },
);


const totalUsage = computed(() => {
  let prompt = 0;
  let completion = 0;
  let cached = 0;
  let lastPrompt = 0;
  for (const usage of Object.values(chat.tokenUsage)) {
    prompt += usage.prompt || 0;
    completion += usage.completion || 0;
    cached += usage.cached || 0;
    // Context = last call's prompt_tokens (not accumulated sum)
    if ((usage.lastPrompt || 0) > lastPrompt) lastPrompt = usage.lastPrompt || 0;
  }
  return { prompt, completion, cached, lastPrompt };
});

const maxContext = computed(
  () => chat.sessionInfo.maxContext || props.instance?.max_context || 0
);

const contextPct = computed(() => {
  if (!maxContext.value || !totalUsage.value.lastPrompt) return 0;
  return Math.round((totalUsage.value.lastPrompt / maxContext.value) * 100);
});

const compactThreshold = computed(
  () => chat.sessionInfo.compactThreshold || props.instance?.compact_threshold || 0
);

const compactThresholdPct = computed(() => {
  if (!maxContext.value || !compactThreshold.value) return 0;
  return Math.min(100, Math.round((compactThreshold.value / maxContext.value) * 100));
});

const currentModelProfile = computed(() => {
  const modelName = selectedModel.value || chat.sessionInfo.model || instance?.model || "";
  return availableModels.value.find((m) => m.name === modelName) || null;
});

function formatTokens(n) {
  if (!n) return "0";
  if (n >= 1000000) return (n / 1000000).toFixed(1) + "M";
  if (n >= 1000) return (n / 1000).toFixed(1) + "K";
  return String(n);
}

function formatElapsed(startedAt) {
  const elapsed = Math.floor((Date.now() - startedAt) / 1000);
  if (elapsed >= 60) return Math.floor(elapsed / 60) + "m " + (elapsed % 60) + "s";
  return elapsed + "s";
}

async function stopTask(jobId, jobName) {
  try {
    const tab = chat.activeTab;
    if (chat._instanceType === "terrarium") {
      await terrariumAPI.stopCreatureTask(chat._instanceId, tab || "root", jobId);
    } else {
      await agentAPI.stopTask(chat._instanceId, jobId);
    }
    // Mark as cancelling for visual feedback — backend event will remove it
    const job = chat.runningJobs[jobId];
    if (job) job.cancelling = true;
  } catch (err) {
    console.error("Failed to stop task:", err);
  }
}

async function loadModels() {
  modelsLoading.value = true;
  try {
    const models = await configAPI.getModels();
    availableModels.value = (models || []).filter((m) => m.available !== false);
  } catch {
    availableModels.value = [];
  } finally {
    modelsLoading.value = false;
  }
}

async function handleModelSwitch(modelId) {
  if (!props.instance?.id) return;
  modelSwitchError.value = "";
  try {
    if (props.instance.type === "terrarium") {
      const target = chat.activeTab || "root";
      await terrariumAPI.switchCreatureModel(props.instance.id, target, modelId);
    } else {
      await agentAPI.switchModel(props.instance.id, modelId);
    }
    // Backend sends session_info event via WS which updates chat.sessionInfo
  } catch (err) {
    modelSwitchError.value = err.response?.data?.detail || "Failed to switch model";
    selectedModel.value = chat.sessionInfo.model || "";
  }
}

/** Open the model config dialog with the selected model's full profile */
function openModelConfig() {
  configJsonError.value = "";
  // Find the full profile for the currently selected model
  const modelName = selectedModel.value || chat.sessionInfo.model || "";
  const fullProfile = availableModels.value.find((m) => m.name === modelName);
  const profile = fullProfile
    ? {
        model: fullProfile.model,
        provider: fullProfile.provider,
        max_context: fullProfile.max_context || 0,
        max_output: fullProfile.max_output || 0,
        temperature: fullProfile.temperature,
        reasoning_effort: fullProfile.reasoning_effort || "",
        extra_body: fullProfile.extra_body || {},
        base_url: fullProfile.base_url || "",
      }
    : { model: modelName, extra_body: {} };
  configJson.value = JSON.stringify(profile, null, 2);
  configDialogVisible.value = true;
}

/** Validate and save model config (currently logs to console) */
function saveModelConfig() {
  configJsonError.value = "";
  try {
    const parsed = JSON.parse(configJson.value);
    console.log("[StatusDashboard] Model config saved:", parsed);
    configDialogVisible.value = false;
  } catch (e) {
    configJsonError.value = "Invalid JSON: " + e.message;
  }
}
</script>

<style scoped>
.section-label {
  @apply text-warm-400 mb-1.5 uppercase tracking-wider text-[10px] font-medium;
}

/* Element Plus select warm toning */
:deep(.el-select) {
  --el-fill-color-blank: var(--color-surface);
  --el-border-color: var(--color-border);
  --el-text-color-regular: var(--color-text);
}

/* Model config gear button - iolite accent */
.model-config-btn {
  --el-button-hover-bg-color: rgba(90, 79, 207, 0.1);
  --el-button-hover-border-color: #5A4FCF;
  --el-button-hover-text-color: #5A4FCF;
  color: var(--color-text-muted);
}

/* Model config dialog */
:deep(.model-config-dialog .el-dialog__header) {
  padding: 16px 20px 12px;
  border-bottom: 1px solid var(--el-border-color);
}

:deep(.model-config-dialog .el-dialog__title) {
  font-weight: 600;
}

:deep(.model-config-dialog .el-dialog__body) {
  padding: 16px 20px;
}

:deep(.model-config-dialog .el-dialog__footer) {
  padding: 12px 20px 16px;
  border-top: 1px solid var(--el-border-color);
}

:deep(.config-textarea .el-textarea__inner) {
  font-family: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.5;
  resize: vertical;
}

/* Save button iolite accent */
.save-btn {
  --el-button-bg-color: #5A4FCF;
  --el-button-border-color: #5A4FCF;
  --el-button-hover-bg-color: #4A3FBF;
  --el-button-hover-border-color: #4A3FBF;
}
</style>
