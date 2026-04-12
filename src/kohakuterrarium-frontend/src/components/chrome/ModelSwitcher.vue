<template>
  <el-dropdown trigger="click" @command="onPick" @visible-change="onVisibleChange">
    <button
      class="flex items-center gap-1 px-1 py-0 rounded transition-colors hover:text-warm-700 dark:hover:text-warm-300"
      :disabled="!instanceId"
      :title="currentModel"
    >
      <span class="i-carbon-chip text-[11px]" />
      <span class="truncate max-w-40 font-mono">{{ currentModel || "default" }}</span>
      <span class="i-carbon-chevron-down text-[9px] opacity-50" />
    </button>
    <template #dropdown>
      <el-dropdown-menu class="model-switcher-dropdown">
        <div v-if="loading" class="px-4 py-2 text-[11px] text-warm-400">Loading…</div>
        <el-dropdown-item
          v-for="m in models"
          v-else
          :key="m.name"
          :command="m.name"
          :disabled="m.name === currentModel"
        >
          <div class="flex items-center gap-2">
            <span class="font-mono text-[11px]">{{ m.name }}</span>
            <span v-if="m.login_provider" class="text-[9px] text-warm-400">{{
              m.login_provider
            }}</span>
          </div>
        </el-dropdown-item>
        <div v-if="!loading && models.length === 0" class="px-4 py-2 text-[11px] text-warm-400">
          No models available
        </div>
      </el-dropdown-menu>
    </template>
  </el-dropdown>

  <!-- Model config dialog (opened by gear button in StatusBar) -->
  <el-dialog
    v-model="configDialogVisible"
    title="Model Configuration"
    width="500px"
    :close-on-click-modal="true"
  >
    <div class="flex flex-col gap-2">
      <p class="text-xs text-warm-400">
        JSON profile for
        <strong class="text-warm-600 dark:text-warm-300">{{
          currentModel || "current model"
        }}</strong>
      </p>
      <textarea
        v-model="configJson"
        class="w-full h-48 bg-warm-50 dark:bg-warm-800 border border-warm-200 dark:border-warm-700 rounded p-2 font-mono text-xs resize-y"
        spellcheck="false"
      />
      <p v-if="configJsonError" class="text-coral text-xs">
        {{ configJsonError }}
      </p>
    </div>
    <template #footer>
      <el-button size="small" @click="configDialogVisible = false">Cancel</el-button>
      <el-button size="small" type="primary" @click="saveModelConfig">Save</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, ref, onMounted, onUnmounted } from "vue"
import { ElMessage } from "element-plus"

import { useChatStore } from "@/stores/chat"
import { useInstancesStore } from "@/stores/instances"
import { agentAPI, terrariumAPI, configAPI } from "@/utils/api"
import { onLayoutEvent, LAYOUT_EVENTS } from "@/utils/layoutEvents"

const chat = useChatStore()
const instances = useInstancesStore()

const models = ref([])
const loading = ref(false)

// Config dialog state
const configDialogVisible = ref(false)
const configJson = ref("")
const configJsonError = ref("")

const instanceId = computed(() => instances.current?.id || null)
const currentModel = computed(() => chat.sessionInfo.model || instances.current?.model || "")

async function loadModels() {
  loading.value = true
  try {
    const data = await configAPI.getModels()
    models.value = Array.isArray(data) ? data : []
  } catch (err) {
    models.value = []
  } finally {
    loading.value = false
  }
}

function onVisibleChange(open) {
  if (open && models.value.length === 0) loadModels()
}

async function onPick(modelName) {
  const id = instanceId.value
  if (!id || !modelName || modelName === currentModel.value) return
  try {
    const inst = instances.current
    if (inst?.type === "terrarium") {
      const tab = chat.activeTab || "root"
      await terrariumAPI.switchCreatureModel(id, tab, modelName)
    } else {
      await agentAPI.switchModel(id, modelName)
    }
    chat.sessionInfo.model = modelName
    ElMessage.success(`Switched to ${modelName}`)
  } catch (err) {
    ElMessage.error(`Model switch failed: ${err?.message || err}`)
  }
}

/** Open model config dialog with the current profile's JSON */
function openModelConfig() {
  configJsonError.value = ""
  if (models.value.length === 0) loadModels()
  const modelName = currentModel.value
  const fullProfile = models.value.find((m) => m.name === modelName)
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
    : { model: modelName, extra_body: {} }
  configJson.value = JSON.stringify(profile, null, 2)
  configDialogVisible.value = true
}

function saveModelConfig() {
  configJsonError.value = ""
  try {
    JSON.parse(configJson.value)
    configDialogVisible.value = false
    ElMessage.success("Config saved")
    // TODO: send updated config to backend when API supports it
  } catch (e) {
    configJsonError.value = "Invalid JSON: " + e.message
  }
}

// Listen for gear button event from StatusBar
let _cleanup = null
onMounted(() => {
  _cleanup = onLayoutEvent(LAYOUT_EVENTS.MODEL_CONFIG_OPEN, () => openModelConfig())
})
onUnmounted(() => {
  if (_cleanup) _cleanup()
})
</script>

<style>
/* Constrain the model dropdown so it scrolls rather than pushing the layout */
.model-switcher-dropdown {
  max-height: 360px;
  overflow-y: auto;
}
</style>
