<template>
  <div class="tool-options-panel h-full flex flex-col bg-warm-50 dark:bg-warm-900 overflow-hidden">
    <div class="flex items-center gap-2 px-3 py-2 border-b border-warm-200 dark:border-warm-700 shrink-0">
      <el-select v-model="selectedTool" size="small" placeholder="Tool" class="!w-44" :disabled="!availableTools.length">
        <el-option v-for="t in availableTools" :key="t.name" :value="t.name" :label="t.name" />
      </el-select>
      <span v-if="status" class="text-[11px] text-warm-400 ml-2 truncate">{{ status }}</span>
      <div class="flex-1" />
      <el-button size="small" :disabled="!isDirty || saving" type="primary" :loading="saving" @click="saveOptions">Save</el-button>
      <el-button size="small" :disabled="!hasOverrides || saving" plain @click="resetTool">Reset</el-button>
    </div>

    <div class="flex-1 overflow-y-auto p-4">
      <div v-if="!agentId" class="text-warm-400 text-sm italic">No active agent — open or create one to edit its tool options.</div>
      <div v-else-if="isTerrarium" class="text-warm-400 text-sm italic">Tool options are currently only available for standalone agent sessions.</div>
      <div v-else-if="loading" class="text-warm-400 text-sm italic">Loading tool options…</div>
      <div v-else-if="!availableTools.length" class="text-warm-400 text-sm italic">This agent has no provider-native tools with editable options.</div>
      <div v-else-if="!selectedTool" class="text-warm-400 text-sm italic">Pick a tool to edit.</div>
      <ToolOptionsForm v-else v-model="draftValues" :schema="schemaForCurrent" />
    </div>
  </div>
</template>

<script setup>
import { computed, ref, toRefs, watch } from "vue"

import ToolOptionsForm from "@/components/settings/ToolOptionsForm.vue"
import { validateNativeToolOptions } from "@/utils/nativeToolValidation"

/**
 * Per-instance editor for provider-native tool options.
 *
 * Stays scoped to the active agent so overrides live with the session
 * (persisted in the agent's scratchpad). Reads + writes the
 * ``/api/agents/{id}/native-tool-options`` endpoint exclusively;
 * does not touch any global backend / settings-page surface.
 *
 * GET  /api/agents/{id}/native-tool-options    → {tools: [{name, description, option_schema, values}]}
 * PUT  /api/agents/{id}/native-tool-options    body: {tool, values}; empty values clears the override.
 */
const props = defineProps({
  instance: { type: Object, default: null },
})
const { instance } = toRefs(props)

const tools = ref([])
const selectedTool = ref("")
const draftValues = ref({})
const loading = ref(false)
const saving = ref(false)
const status = ref("")

const agentId = computed(() => instance.value?.agent_id || instance.value?.id || "")
const isTerrarium = computed(() => instance.value?.type === "terrarium")

const availableTools = computed(() => {
  if (isTerrarium.value) return []
  return tools.value.filter((t) => Object.keys(t.option_schema || {}).length)
})

const currentTool = computed(() => tools.value.find((t) => t.name === selectedTool.value) || null)

const schemaForCurrent = computed(() => currentTool.value?.option_schema || {})

const persistedValues = computed(() => currentTool.value?.values || {})

const isDirty = computed(() => JSON.stringify(draftValues.value) !== JSON.stringify(persistedValues.value))

const hasOverrides = computed(() => Object.keys(persistedValues.value).length > 0)

async function loadTools() {
  if (!agentId.value) {
    tools.value = []
    return
  }
  loading.value = true
  try {
    const res = await fetch(`/api/agents/${encodeURIComponent(agentId.value)}/native-tool-options`)
    if (!res.ok) throw new Error(await res.text())
    const body = await res.json()
    tools.value = body.tools || []
  } catch (e) {
    tools.value = []
    status.value = `Error: ${e.message || e}`
  } finally {
    loading.value = false
  }
}

watch(
  agentId,
  () => {
    selectedTool.value = ""
    status.value = ""
    loadTools()
  },
  { immediate: true },
)

watch(
  availableTools,
  (list) => {
    if (!list.length) {
      selectedTool.value = ""
      draftValues.value = {}
      return
    }
    if (!list.find((t) => t.name === selectedTool.value)) {
      selectedTool.value = list[0].name
    }
  },
  { immediate: true },
)

watch(
  [persistedValues, selectedTool],
  () => {
    draftValues.value = { ...persistedValues.value }
    status.value = ""
  },
  { immediate: true },
)

async function saveOptions() {
  if (!agentId.value || !selectedTool.value) return
  saving.value = true
  status.value = ""
  try {
    const validatedValues = validateNativeToolOptions(selectedTool.value, draftValues.value, schemaForCurrent.value)
    const res = await fetch(`/api/agents/${encodeURIComponent(agentId.value)}/native-tool-options`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tool: selectedTool.value, values: validatedValues }),
    })
    if (!res.ok) throw new Error(await res.text())
    status.value = "Saved"
    await loadTools()
  } catch (e) {
    status.value = `Error: ${e.message || e}`
  } finally {
    saving.value = false
  }
}

async function resetTool() {
  if (!agentId.value || !selectedTool.value) return
  saving.value = true
  status.value = ""
  try {
    const res = await fetch(`/api/agents/${encodeURIComponent(agentId.value)}/native-tool-options`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tool: selectedTool.value, values: {} }),
    })
    if (!res.ok) throw new Error(await res.text())
    status.value = "Reset"
    await loadTools()
  } catch (e) {
    status.value = `Error: ${e.message || e}`
  } finally {
    saving.value = false
  }
}
</script>
