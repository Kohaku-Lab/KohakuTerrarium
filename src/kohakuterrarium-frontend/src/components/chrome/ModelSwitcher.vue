<template>
  <div v-if="isTerrarium" class="flex items-center gap-2 min-w-0">
    <el-select :model-value="selectedTarget" size="small" class="status-select target-select" :disabled="!instanceId" @change="onPickTarget">
      <el-option v-for="target in targetOptions" :key="target.value" :label="target.label" :value="target.value" />
    </el-select>
    <el-select :model-value="currentModel" size="small" class="status-select model-select" :disabled="!canPickModel" :loading="loading" placeholder="Search model" filterable default-first-option :reserve-keyword="false" @visible-change="onVisibleChange" @change="onPick">
      <el-option v-for="m in availableModels" :key="m.name" :label="modelLabel(m)" :value="m.name" :disabled="m.name === currentModel" />
    </el-select>
  </div>
  <el-select v-else :model-value="currentModel" size="small" class="status-select model-select" :disabled="!instanceId" :loading="loading" placeholder="Search model" filterable default-first-option :reserve-keyword="false" @visible-change="onVisibleChange" @change="onPick">
    <el-option v-for="m in availableModels" :key="m.name" :label="modelLabel(m)" :value="m.name" :disabled="m.name === currentModel" />
  </el-select>

  <!-- Model config dialog (opened by gear button in StatusBar) -->
  <el-dialog v-model="configDialogVisible" title="Model Configuration" width="500px" :close-on-click-modal="true">
    <div class="flex flex-col gap-2">
      <p class="text-xs text-warm-400">
        JSON profile for
        <strong class="text-warm-600 dark:text-warm-300">{{ currentModel || "current model" }}</strong>
      </p>
      <textarea v-model="configJson" class="w-full h-48 bg-warm-50 dark:bg-warm-800 border border-warm-200 dark:border-warm-700 rounded p-2 font-mono text-xs resize-y" spellcheck="false" />
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

const route = useRoute()
const chat = useChatStore()
const instances = useInstancesStore()

const models = ref([])
const loading = ref(false)
const availableModels = computed(() => models.value.filter((model) => model.available !== false))

// Config dialog state
const configDialogVisible = ref(false)
const configJson = ref("")
const configJsonError = ref("")

const currentInstance = computed(() => {
  const id = String(route.params.id || "")
  if (!id) return instances.current
  if (instances.current?.id === id) return instances.current
  return instances.list.find((item) => item.id === id) || null
})
const instanceId = computed(() => currentInstance.value?.id || null)
const isTerrarium = computed(() => currentInstance.value?.type === "terrarium")
const terrariumTarget = computed(() => (isTerrarium.value ? chat.terrariumTarget : null))
const targetOptions = computed(() => {
  const inst = currentInstance.value
  if (inst?.type !== "terrarium") return []
  return [...(inst.has_root ? [{ value: "root", label: "root" }] : []), ...(inst.creatures || []).map((c) => ({ value: c.name, label: c.name }))]
})
const selectedTarget = computed(() => terrariumTarget.value || targetOptions.value[0]?.value || null)
const canPickModel = computed(() => !!instanceId.value && (!isTerrarium.value || !!selectedTarget.value))
const currentModel = computed(() => {
  const inst = currentInstance.value
  if (inst?.type === "terrarium") {
    const target = selectedTarget.value
    if (target === "root") return terrariumTarget.value === target ? chat.sessionInfo.llmName || chat.sessionInfo.model || inst.model || "" : inst.model || ""
    if (target) {
      const creature = inst.creatures?.find((c) => c.name === target)
      return terrariumTarget.value === target ? chat.sessionInfo.llmName || chat.sessionInfo.model || creature?.model || "" : creature?.model || ""
    }
    return ""
  }
  return chat.sessionInfo.llmName || chat.sessionInfo.model || inst?.model || ""
})

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

function modelLabel(model) {
  const provider = model.backend || model.login_provider || model.provider || ""
  return provider ? `${model.name} · ${provider}` : model.name
}

function onPickTarget(target) {
  if (!target || !isTerrarium.value) return
  if (chat.tabs.includes(target)) chat.setActiveTab(target)
  else chat.openTab(target)
}

async function onPick(modelName) {
  const id = instanceId.value
  if (!id || !modelName || modelName === currentModel.value) return
  try {
    const inst = currentInstance.value
    if (inst?.type === "terrarium") {
      const target = selectedTarget.value
      if (!target) {
        ElMessage.error("Select a root or creature first")
        return
      }
      await terrariumAPI.switchCreatureModel(id, target, modelName)
      await instances.fetchOne(id)
      if (terrariumTarget.value === target) {
        chat.sessionInfo.llmName = modelName
        chat.sessionInfo.model = modelName
      }
    } else {
      await agentAPI.switchModel(id, modelName)
      chat.sessionInfo.llmName = modelName
      chat.sessionInfo.model = modelName
    }
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
  const fullProfile = availableModels.value.find((m) => m.name === modelName) || models.value.find((m) => m.name === modelName)
  const profile = fullProfile
    ? {
        model: fullProfile.model,
        provider: fullProfile.provider,
        backend: fullProfile.backend || "",
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
.status-select {
  --el-input-bg-color: transparent;
  --el-fill-color-blank: transparent;
  --el-border-color: rgba(120, 109, 98, 0.25);
  --el-border-color-hover: rgba(120, 109, 98, 0.4);
  --el-text-color-regular: currentColor;
}

.target-select {
  width: 8.5rem;
}

.model-select {
  width: 12rem;
}
</style>
