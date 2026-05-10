<template>
  <div class="flex items-center gap-2 min-w-0">
    <!-- Terrarium target (root vs creature) — only for terrarium instances -->
    <el-select v-if="isTerrarium" :model-value="selectedTarget" size="small" class="status-select target-select" :disabled="!activeInstanceId" @change="onPickTarget">
      <el-option v-for="target in targetOptions" :key="target.value" :label="target.label" :value="target.value" />
    </el-select>

    <!-- Current-model pill: click to open switcher popover -->
    <el-popover v-model:visible="popoverVisible" :width="560" placement="bottom-end" trigger="click" :hide-after="0" :disabled="!canPickModel" popper-class="model-switcher-popover">
      <template #reference>
        <button type="button" class="model-pill" :class="{ 'is-disabled': !canPickModel }" :disabled="!canPickModel">
          <span v-if="loading && !models.length" class="text-warm-400 text-[11px]">Loading models…</span>
          <template v-else>
            <span class="font-mono text-[11px] truncate max-w-[18rem]">{{ currentLabel || "No model" }}</span>
            <span v-if="currentVariationSummary" class="model-pill-variation text-[10px] text-warm-400 shrink-0">
              {{ currentVariationSummary }}
            </span>
            <el-icon class="shrink-0 text-warm-400"><ArrowDown /></el-icon>
          </template>
        </button>
      </template>

      <div class="flex flex-col gap-3 p-1 max-h-[70vh]">
        <!-- Search -->
        <div class="flex items-center gap-2">
          <el-input v-model="searchQuery" size="small" placeholder="Search model or provider…" clearable @keydown.esc="popoverVisible = false" />
          <el-button size="small" :loading="loading" @click="loadModels">Refresh</el-button>
        </div>

        <!-- Main three-pane layout: providers | models | variations -->
        <div class="flex gap-3 min-h-[18rem] max-h-[50vh]">
          <!-- Provider column -->
          <div class="flex flex-col w-32 shrink-0 overflow-y-auto border-r border-warm-100 dark:border-warm-800 pr-2">
            <div class="text-[10px] uppercase tracking-wide text-warm-400 mb-1">Provider</div>
            <button v-for="provider in providerOptions" :key="provider.name" type="button" class="provider-tab" :class="{ 'is-active': draftProvider === provider.name, 'is-unavailable': !provider.available }" @click="selectProvider(provider.name)">
              <span class="truncate">{{ provider.name }}</span>
              <span class="text-[9px] text-warm-400 shrink-0">{{ provider.count }}</span>
            </button>
          </div>

          <!-- Model column -->
          <div class="flex flex-col flex-1 min-w-0 overflow-y-auto pr-2">
            <div class="text-[10px] uppercase tracking-wide text-warm-400 mb-1">Model</div>
            <button v-for="preset in filteredPresets" :key="preset.name" type="button" class="model-row" :class="{ 'is-active': draftPreset === preset.name, 'is-unavailable': !preset.available }" @click="selectPreset(preset.name)">
              <div class="flex items-center gap-2 w-full">
                <span class="font-medium text-[12px] truncate">{{ preset.name }}</span>
                <span v-if="preset.is_default" class="text-[9px] px-1 rounded bg-iolite/20 text-iolite uppercase shrink-0"> default </span>
                <span v-if="hasVariations(preset)" class="text-[9px] text-warm-400 shrink-0"> {{ Object.keys(preset.variation_groups).length }} opts </span>
              </div>
              <div class="text-[10px] text-warm-400 font-mono truncate w-full">
                {{ preset.model }}
              </div>
            </button>
            <div v-if="!filteredPresets.length" class="text-warm-400 text-[11px] italic p-2 text-center">No matching models.</div>
          </div>

          <!-- Variation column -->
          <div class="flex flex-col w-48 shrink-0 overflow-y-auto">
            <div class="text-[10px] uppercase tracking-wide text-warm-400 mb-1">Variations</div>
            <div v-if="!draftPresetData || !hasVariations(draftPresetData)" class="text-warm-400 text-[11px] italic">No variation groups.</div>
            <template v-else>
              <div v-for="group in draftVariationGroups" :key="group.name" class="flex flex-col mb-3">
                <div class="text-[10px] text-warm-500 font-medium mb-1">{{ group.name }}</div>
                <div class="flex flex-wrap gap-1">
                  <button v-for="option in group.options" :key="option" type="button" class="variation-chip" :class="{ 'is-active': draftSelections[group.name] === option }" @click="toggleVariation(group.name, option)">
                    {{ option }}
                  </button>
                </div>
              </div>
            </template>
          </div>
        </div>

        <!-- Footer: selector preview + actions -->
        <div class="flex items-center gap-2 pt-2 border-t border-warm-100 dark:border-warm-800">
          <div class="flex-1 min-w-0">
            <div class="text-[10px] text-warm-400">Selector</div>
            <code class="font-mono text-[11px] text-warm-700 dark:text-warm-300 truncate block">
              {{ draftSelector || "—" }}
            </code>
          </div>
          <el-button size="small" @click="popoverVisible = false">Cancel</el-button>
          <el-button size="small" type="primary" :disabled="!draftSelector || draftSelector === currentModel" :loading="applying" @click="applySelection"> Switch </el-button>
        </div>
      </div>
    </el-popover>
  </div>
</template>

<script setup>
import { computed, ref, reactive, watch, onMounted, onUnmounted } from "vue"
import { ElMessage } from "element-plus"
import { ArrowDown } from "@element-plus/icons-vue"

import { useInstanceContext } from "@/components/chrome/instanceContext"
import { useChatStore } from "@/stores/chat"
import { useInstancesStore } from "@/stores/instances"
import { agentAPI, terrariumAPI, configAPI } from "@/utils/api"
import { onLayoutEvent, LAYOUT_EVENTS } from "@/utils/layoutEvents"

const props = defineProps({
  instanceId: { type: String, default: "" },
})

const route = useRoute()
const chat = useChatStore()
const instances = useInstancesStore()

const models = ref([])
const loading = ref(false)
const applying = ref(false)
const popoverVisible = ref(false)
const searchQuery = ref("")

const draftProvider = ref("")
const draftPreset = ref("")
const draftSelections = reactive({})

const availableModels = computed(() => models.value.filter((model) => model.available !== false))

const { instance: currentInstance } = useInstanceContext(props, route, instances)
const activeInstanceId = computed(() => currentInstance.value?.id || null)
const isTerrarium = computed(() => currentInstance.value?.type === "terrarium")
const terrariumTarget = computed(() => (isTerrarium.value ? chat.terrariumTarget : null))
const targetOptions = computed(() => {
  const inst = currentInstance.value
  if (inst?.type !== "terrarium") return []
  return [...(inst.has_root ? [{ value: "root", label: "root" }] : []), ...(inst.creatures || []).map((c) => ({ value: c.name, label: c.name }))]
})
const selectedTarget = computed(() => terrariumTarget.value || targetOptions.value[0]?.value || null)
const canPickModel = computed(() => !!activeInstanceId.value && (!isTerrarium.value || !!selectedTarget.value))

const currentModel = computed(() => {
  const inst = currentInstance.value
  // ``llm_name`` carries the canonical ``provider/name[@variations]`` —
  // prefer it over ``model`` (raw API id) so the pill and picker-draft
  // survive a page refresh with the full identifier intact.
  if (inst?.type === "terrarium") {
    const target = selectedTarget.value
    if (target === "root") {
      const fallback = inst.llm_name || inst.model || ""
      return terrariumTarget.value === target ? chat.sessionInfo.llmName || chat.sessionInfo.model || fallback : fallback
    }
    if (target) {
      const creature = inst.creatures?.find((c) => c.name === target)
      const fallback = creature?.llm_name || creature?.model || ""
      return terrariumTarget.value === target ? chat.sessionInfo.llmName || chat.sessionInfo.model || fallback : fallback
    }
    return ""
  }
  return chat.sessionInfo.llmName || chat.sessionInfo.model || inst?.llm_name || inst?.model || ""
})

const currentParsed = computed(() => parseSelector(currentModel.value))
const currentLabel = computed(() => {
  const { provider, name } = currentParsed.value
  // Always show ``provider/name`` when both are known — matches the
  // identifier the picker emits and the rich-CLI banner displays. Falls
  // back to the bare name for pre-refactor session data that still
  // stores just the model id.
  if (provider && name) return `${provider}/${name}`
  return name
})
const currentVariationSummary = computed(() => {
  const entries = Object.entries(currentParsed.value.selections)
  if (!entries.length) return ""
  return entries.map(([g, o]) => `${g}=${o}`).join(", ")
})

// Provider tabs: group available models by provider, keep availability info
const providerOptions = computed(() => {
  const query = searchQuery.value.trim().toLowerCase()
  const map = new Map()
  for (const model of models.value) {
    const provider = model.provider || model.login_provider || "unknown"
    if (query) {
      const hay = `${model.name} ${model.model} ${provider}`.toLowerCase()
      if (!hay.includes(query)) continue
    }
    if (!map.has(provider)) {
      map.set(provider, { name: provider, count: 0, available: false })
    }
    const entry = map.get(provider)
    entry.count += 1
    if (model.available) entry.available = true
  }
  return Array.from(map.values()).sort((a, b) => {
    if (a.available !== b.available) return a.available ? -1 : 1
    return a.name.localeCompare(b.name)
  })
})

const filteredPresets = computed(() => {
  const query = searchQuery.value.trim().toLowerCase()
  return models.value
    .filter((m) => (m.provider || m.login_provider) === draftProvider.value)
    .filter((m) => {
      if (!query) return true
      return `${m.name} ${m.model}`.toLowerCase().includes(query)
    })
    .sort((a, b) => {
      if (a.available !== b.available) return a.available ? -1 : 1
      return a.name.localeCompare(b.name)
    })
})

const draftPresetData = computed(() => filteredPresets.value.find((m) => m.name === draftPreset.value) || models.value.find((m) => m.name === draftPreset.value) || null)

const draftVariationGroups = computed(() => {
  const groups = draftPresetData.value?.variation_groups || {}
  return Object.entries(groups).map(([name, options]) => ({
    name,
    options: Object.keys(options || {}),
  }))
})

const draftSelector = computed(() => {
  if (!draftPreset.value) return ""
  // Under the (provider, name) hierarchy, bare names can be ambiguous
  // across providers (``gpt-5.4`` exists on codex, openai, openrouter,
  // and any custom backend the user added). Always emit ``provider/name``
  // so the backend resolver can pick the exact entry without guessing.
  const base = draftProvider.value ? `${draftProvider.value}/${draftPreset.value}` : draftPreset.value
  const entries = Object.entries(draftSelections)
    .filter(([, value]) => value)
    .sort(([a], [b]) => a.localeCompare(b))
  if (!entries.length) return base
  return `${base}@${entries.map(([g, o]) => `${g}=${o}`).join(",")}`
})

function hasVariations(preset) {
  return !!preset?.variation_groups && Object.keys(preset.variation_groups).length > 0
}

function parseSelector(value) {
  const raw = String(value || "")
  if (!raw) return { provider: "", name: "", selections: {} }
  const [base, selector] = raw.split("@", 2)
  let provider = ""
  let name = base.trim()
  if (name.includes("/")) {
    const slash = name.indexOf("/")
    provider = name.slice(0, slash).trim()
    name = name.slice(slash + 1).trim()
  }
  const selections = {}
  if (selector) {
    selector.split(",").forEach((part) => {
      const [group, option] = part.split("=", 2)
      if (group && option) selections[group.trim()] = option.trim()
    })
  }
  return { provider, name, selections }
}

function resetDraftFromCurrent() {
  const { provider, name, selections } = currentParsed.value
  // When the selector carried a ``provider/name`` prefix, look up by the
  // full (provider, name) pair. Otherwise fall back to the bare name (for
  // pre-refactor session data that still stores bare ids).
  const matched = (provider && models.value.find((m) => (m.provider || m.login_provider) === provider && m.name === name)) || models.value.find((m) => m.name === name) || models.value.find((m) => m.model === name) || models.value[0]
  if (!matched) {
    draftProvider.value = providerOptions.value[0]?.name || ""
    draftPreset.value = ""
    Object.keys(draftSelections).forEach((k) => delete draftSelections[k])
    return
  }
  draftProvider.value = matched.provider || matched.login_provider || ""
  draftPreset.value = matched.name
  Object.keys(draftSelections).forEach((k) => delete draftSelections[k])
  Object.entries(selections).forEach(([g, o]) => (draftSelections[g] = o))
}

function selectProvider(provider) {
  if (draftProvider.value === provider) return
  draftProvider.value = provider
  const first = filteredPresets.value[0]
  if (first) {
    draftPreset.value = first.name
  } else {
    draftPreset.value = ""
  }
  Object.keys(draftSelections).forEach((k) => delete draftSelections[k])
}

function selectPreset(name) {
  if (draftPreset.value === name) return
  draftPreset.value = name
  Object.keys(draftSelections).forEach((k) => delete draftSelections[k])
}

function toggleVariation(group, option) {
  if (draftSelections[group] === option) {
    delete draftSelections[group]
  } else {
    draftSelections[group] = option
  }
}

async function loadModels() {
  loading.value = true
  try {
    const data = await configAPI.getModels()
    models.value = Array.isArray(data) ? data : []
    resetDraftFromCurrent()
  } catch {
    models.value = []
  } finally {
    loading.value = false
  }
}

function onPickTarget(target) {
  if (!target || !isTerrarium.value) return
  if (chat.tabs.includes(target)) chat.setActiveTab(target)
  else chat.openTab(target)
}

async function applySelection() {
  const modelName = draftSelector.value
  const id = activeInstanceId.value
  if (!id || !modelName || modelName === currentModel.value) return
  applying.value = true
  try {
    // Unified routing — every session uses
    // ``/sessions/{sid}/creatures/{name}/model``. Solo sessions
    // resolve their lone creature automatically; multi-creature
    // sessions follow the user's active tab.
    const inst = currentInstance.value
    const sid = inst?.graph_id || id
    const target = selectedTarget.value || inst?.creatures?.[0]?.name
    if (!target) {
      ElMessage.error("Select a creature first")
      return
    }
    await terrariumAPI.switchCreatureModel(sid, target, modelName)
    await instances.fetchOne(id)
    if (chat.terrariumTarget === target || (inst?.creatures?.length || 0) <= 1) {
      chat.sessionInfo.llmName = modelName
      chat.sessionInfo.model = modelName
    }
    ElMessage.success(`Switched to ${modelName}`)
    popoverVisible.value = false
  } catch (err) {
    ElMessage.error(`Model switch failed: ${err?.message || err}`)
  } finally {
    applying.value = false
  }
}

watch(popoverVisible, (open) => {
  if (open) {
    if (models.value.length === 0) loadModels()
    else resetDraftFromCurrent()
  }
})

watch(currentModel, () => {
  if (!popoverVisible.value) resetDraftFromCurrent()
})

let _cleanup = null
onMounted(() => {
  loadModels()
  _cleanup = onLayoutEvent(LAYOUT_EVENTS.MODEL_CONFIG_OPEN, () => (popoverVisible.value = true))
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

.model-pill {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.25rem 0.6rem;
  min-height: 28px;
  border-radius: 6px;
  border: 1px solid rgba(120, 109, 98, 0.25);
  background: transparent;
  color: inherit;
  cursor: pointer;
  transition:
    border-color 0.1s ease,
    background 0.1s ease;
  min-width: 12rem;
  max-width: 24rem;
}
.model-pill:hover:not(.is-disabled) {
  border-color: rgba(120, 109, 98, 0.5);
  background: rgba(120, 109, 98, 0.06);
}
.model-pill.is-disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.provider-tab {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  padding: 0.35rem 0.5rem;
  margin-bottom: 2px;
  border-radius: 4px;
  border: none;
  background: transparent;
  text-align: left;
  font-size: 12px;
  color: inherit;
  cursor: pointer;
}
.provider-tab:hover {
  background: rgba(120, 109, 98, 0.08);
}
.provider-tab.is-active {
  background: rgba(90, 140, 200, 0.12);
  color: var(--el-color-primary, #5a8cc8);
  font-weight: 500;
}
.provider-tab.is-unavailable {
  opacity: 0.5;
}

.model-row {
  display: flex;
  flex-direction: column;
  padding: 0.4rem 0.5rem;
  margin-bottom: 2px;
  border-radius: 4px;
  border: 1px solid transparent;
  background: transparent;
  text-align: left;
  color: inherit;
  cursor: pointer;
}
.model-row:hover {
  background: rgba(120, 109, 98, 0.08);
}
.model-row.is-active {
  background: rgba(90, 140, 200, 0.12);
  border-color: rgba(90, 140, 200, 0.3);
}
.model-row.is-unavailable {
  opacity: 0.4;
}

.variation-chip {
  padding: 0.2rem 0.55rem;
  border-radius: 999px;
  border: 1px solid rgba(120, 109, 98, 0.3);
  background: transparent;
  font-size: 11px;
  color: inherit;
  cursor: pointer;
  transition: background 0.1s ease;
}
.variation-chip:hover {
  background: rgba(120, 109, 98, 0.1);
}
.variation-chip.is-active {
  background: rgba(90, 140, 200, 0.18);
  border-color: var(--el-color-primary, #5a8cc8);
  color: var(--el-color-primary, #5a8cc8);
  font-weight: 500;
}

.model-switcher-popover {
  padding: 0.75rem !important;
}
</style>
