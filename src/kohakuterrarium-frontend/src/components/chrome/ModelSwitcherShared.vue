<template>
  <div class="flex items-center gap-2 min-w-0">
    <!-- Terrarium target (root vs creature) — only for terrarium instances -->
    <el-select v-if="isTerrarium" :model-value="selectedTarget" size="small" class="status-select target-select" :disabled="!activeInstanceId" @change="onPickTarget">
      <el-option v-for="target in targetOptions" :key="target.value" :label="target.label" :value="target.value" />
    </el-select>

    <!-- Current-model pill: click to open switcher drawer.  Drawer
         (not popover) for both mobile and desktop — popper.js
         positions teleported popovers using layout-coord math that
         CSS ``zoom`` on ``<html>`` inflates relative to the visual
         viewport, which pushed the popover off-screen at zoom > 1.
         Drawer positions relative to the viewport itself. -->
    <button type="button" class="model-pill" :class="{ 'is-disabled': !canPickModel }" :disabled="!canPickModel" @click="popoverVisible = !popoverVisible">
      <span v-if="loading && !models.length" class="text-warm-400 text-[11px]">Loading models…</span>
      <template v-else>
        <span class="font-mono text-[11px] truncate max-w-[18rem]">{{ currentLabel || "No model" }}</span>
        <span v-if="currentVariationSummary" class="model-pill-variation text-[10px] text-warm-400 shrink-0">
          {{ currentVariationSummary }}
        </span>
        <el-icon class="shrink-0 text-warm-400"><ArrowDown /></el-icon>
      </template>
    </button>
    <el-drawer v-model="popoverVisible" :direction="drawerDirection" :with-header="false" :size="drawerSize" :modal="true" :close-on-click-modal="true" :destroy-on-close="false" append-to-body class="model-switcher-drawer">
      <div class="model-switcher-body flex flex-col gap-3 p-3 h-full overflow-hidden">
        <!-- Search -->
        <div class="flex items-center gap-2">
          <el-input v-model="searchQuery" size="small" placeholder="Search model or provider…" clearable @keydown.esc="popoverVisible = false" />
          <el-button size="small" :title="showCatalog ? `Refresh models from ${draftProvider}` : 'Refresh saved presets'" :loading="refreshing || catalogBusy" @click="refreshInventory">Refresh</el-button>
        </div>

        <!-- Provider rail | model list | variations side by side when the drawer is wide; stacked when narrow. -->
        <div class="model-switcher-panes min-h-0 flex-1">
          <div class="model-switcher-providers">
            <div :id="`${uid}-providers-label`" class="model-switcher-heading">Provider</div>
            <div role="tablist" :aria-labelledby="`${uid}-providers-label`" class="provider-tabs scrollbar-none" data-test="model-provider-tabs" @keydown="moveTabFocus">
              <button v-for="(provider, index) in providerOptions" :key="provider.name" type="button" role="tab" :aria-selected="draftProvider === provider.name" :aria-controls="`${uid}-list`" :tabindex="providerTabIndex(provider.name, index)" :title="provider.name" class="provider-tab" :class="{ 'is-active': draftProvider === provider.name, 'is-unavailable': !provider.available }" @click="selectProvider(provider.name)">
                <span class="truncate">{{ provider.name }}</span>
                <span v-if="!provider.discoverable" class="provider-tab-count">{{ provider.count }}</span>
              </button>
            </div>
          </div>

          <div class="model-switcher-list">
            <div v-if="catalogBackend" role="tablist" aria-label="Model source" class="source-tabs" data-test="model-source-tabs" @keydown="moveTabFocus">
              <button type="button" role="tab" :aria-selected="!showSaved" :aria-controls="`${uid}-list`" :tabindex="showSaved ? -1 : 0" title="Provider models" class="source-tab" :class="{ 'is-active': !showSaved }" @click="showSaved = false">Provider models</button>
              <button type="button" role="tab" :aria-selected="showSaved" :aria-controls="`${uid}-list`" :tabindex="showSaved ? 0 : -1" title="Saved presets" class="source-tab" :class="{ 'is-active': showSaved }" @click="showSaved = true">Saved presets</button>
            </div>
            <div v-else class="model-switcher-heading">Saved presets</div>
            <div :id="`${uid}-list`" :key="[draftProvider, showCatalog].join('/')" :role="catalogBackend ? 'tabpanel' : 'region'" :aria-label="showCatalog ? 'Provider models' : 'Saved presets'" class="min-h-0 flex-1 overflow-y-auto">
              <div v-if="providersError" class="text-warm-500 text-[11px] p-2" role="status">{{ providersError }}</div>
              <div v-if="inventoryError" class="text-coral text-[11px] p-2" role="alert">Could not load saved presets: {{ inventoryError }}</div>
              <template v-if="showCatalog">
                <div v-if="!catalogBackend.available && filteredCatalog.length" class="text-warm-500 text-[11px] p-2">Configure this provider in Settings to switch models.</div>
                <template v-for="row in filteredCatalog.slice(0, catalogLimit)" :key="row.id">
                  <button type="button" class="model-row" :class="{ 'is-active': selectedId === row.id }" @click="selectedId = row.id">
                    <span class="font-medium text-[12px] truncate">{{ row.preset?.model && row.preset.model !== row.id ? row.preset.name : row.name || row.id }}</span>
                    <span v-if="row.preset?.model && row.preset.model !== row.id" class="text-[10px] text-warm-400 font-mono truncate">{{ row.preset.model }}</span>
                    <span v-else-if="row.name && row.name !== row.id" class="text-[10px] text-warm-400 font-mono truncate">{{ row.id }}</span>
                  </button>
                  <div v-if="selectedId === row.id && row.preset_choices?.length" class="px-2 py-1.5 flex items-center flex-wrap gap-1">
                    <span class="text-[11px] text-warm-500">Choose a saved preset</span>
                    <button v-for="name in row.preset_choices" :key="name" type="button" class="variation-chip" @click="selectSavedAlias(name)">{{ name }}</button>
                  </div>
                </template>
                <button v-if="filteredCatalog.length > catalogLimit" type="button" class="variation-chip" @click="catalogLimit += 50">Show more models ({{ filteredCatalog.length - catalogLimit }})</button>
                <div v-if="catalogBusy || catalogError || !filteredCatalog.length" class="text-warm-500 text-[11px] p-2" role="status">
                  {{ catalogBusy ? "Loading provider models…" : catalogError || "No matching provider models." }}
                </div>
              </template>
              <template v-else>
                <button v-for="preset in filteredPresets" :key="preset.name" type="button" class="model-row" :class="{ 'is-active': draftPreset === preset.name, 'is-unavailable': !preset.available }" @click="selectPreset(preset.name)">
                  <div class="flex items-center gap-2 w-full">
                    <span class="font-medium text-[12px] truncate">{{ preset.name }}</span>
                    <span v-if="preset.is_default" class="text-[9px] px-1 rounded bg-iolite/20 text-iolite uppercase shrink-0"> default </span>
                    <span v-if="hasVariations(preset)" class="text-[9px] text-warm-400 shrink-0"> {{ Object.keys(preset.variation_groups).length }} opts </span>
                  </div>
                  <div class="text-[10px] text-warm-400 font-mono truncate w-full">{{ preset.model }}<span v-if="preset.provider_missing"> · Provider missing</span></div>
                </button>
                <div v-if="!filteredPresets.length && !inventoryError" class="text-warm-400 text-[11px] italic p-2 text-center">No matching presets.</div>
              </template>
            </div>
          </div>

          <div v-if="draftPresetData && hasVariations(draftPresetData)" class="model-switcher-variations" data-test="model-variations">
            <div class="model-switcher-heading">Variations</div>
            <div v-for="group in draftVariationGroups" :key="group.name" role="group" :aria-label="group.name" class="flex flex-col min-w-0 mb-3">
              <div class="variation-group-title" :title="group.name">{{ group.name }}</div>
              <div class="flex flex-wrap gap-1 min-w-0">
                <button v-for="option in group.options" :key="option" type="button" class="variation-chip" :class="{ 'is-active': draftSelections[group.name] === option }" :aria-pressed="draftSelections[group.name] === option" :title="option" @click="toggleVariation(group.name, option)">{{ option }}</button>
              </div>
            </div>
          </div>
        </div>

        <div v-if="draftPresetData && !contextClash" class="flex flex-col gap-1 pt-2 border-t border-warm-100 dark:border-warm-800" data-test="model-context">
          <div class="flex items-center gap-1.5 flex-wrap">
            <span class="text-[10px] uppercase tracking-wide text-warm-400 mr-1">Context</span>
            <el-input :model-value="contextInput" size="small" class="context-input" clearable :placeholder="contextPlaceholder ? `Preset default (${contextPlaceholder})` : 'Preset default'" aria-label="Context length" data-test="model-context-input" @update:model-value="contextDraft.edit" />
            <button v-for="count in contextSuggestions" :key="count" type="button" class="variation-chip" :class="{ 'is-active': contextPinned && contextValue === count }" :aria-pressed="contextPinned && contextValue === count" :data-context-pick="count" @click="contextDraft.pick(count)">{{ count === contextWindow ? `${formatContext(count)} (model)` : formatContext(count) }}</button>
          </div>
          <div class="text-[10px]" data-test="model-context-note">
            <span v-if="contextInvalid" class="text-coral" role="alert">Type a length such as 500000, 500k or 1.5m.</span>
            <span v-else-if="contextExceeds" class="text-amber" role="status" data-test="model-context-warning">Above the model's known window ({{ formatContext(contextWindow) }}). The switch is allowed; the provider can reject requests that are too long.</span>
            <span v-else-if="contextWindow" class="text-warm-400">Model window: {{ formatContext(contextWindow) }}.</span>
            <span v-if="!contextInvalid && !contextPinned" class="text-warm-400"> Preset default; type a length to set it for this session.</span>
          </div>
        </div>

        <!-- Footer: selector preview + actions -->
        <div class="flex flex-col sm:flex-row sm:items-center gap-2 pt-2 border-t border-warm-100 dark:border-warm-800">
          <div class="flex-1 min-w-0">
            <div class="text-[10px] text-warm-400">Selector</div>
            <code class="font-mono text-[11px] text-warm-700 dark:text-warm-300 break-all sm:truncate block">
              {{ draftSelector || "—" }}
            </code>
          </div>
          <div class="flex items-center gap-2 justify-end shrink-0">
            <el-button size="small" @click="popoverVisible = false">Cancel</el-button>
            <el-button size="small" type="primary" :disabled="!canPickModel || applying || !draftSelector || isCurrent(draftSelector) || contextInvalid || draftPresetData?.provider_missing || (showCatalog && !catalogBackend.available)" :loading="applying" @click="applySelection">{{ needsSave ? "Save & switch" : "Switch" }}</el-button>
          </div>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, ref, reactive, toValue, useId, watch, onMounted, onUnmounted } from "vue"
import { ElButton, ElDrawer, ElIcon, ElInput, ElMessage, ElOption, ElSelect } from "element-plus"
import { ArrowDown } from "@element-plus/icons-vue"

import { useContextDraft } from "@/composables/useContextDraft"
import { useDensity } from "@/composables/useDensity"
import { formatContext, takeContext, withContext } from "@/utils/contextLength"

import { useModelSwitcherContext } from "./modelSwitcherContext"
import { moveTabFocus } from "./tabFocus"
import { useSwitcherCatalog } from "./useSwitcherCatalog"

// The one shared model picker. Every host-specific concern (which instance,
// how a target/switch is dispatched, the host-keyed inventory, the open/host
// subscriptions) is read from the narrow context, so the Dashboard, the
// status bar, the settings tab and the VS Code webview render the same
// template, search and variation logic. Element Plus widgets are imported
// explicitly (not globally registered) so the same component resolves in the
// VS Code webview build, which installs no component resolver.
const ctx = useModelSwitcherContext()

const { models, initialLoading: loading, refreshing, error: inventoryError, ensureLoaded: loadModels, revalidateIfStale, refresh: refreshModels } = ctx.inventory

const isTerrarium = ctx.isTerrarium
const targetOptions = ctx.targetOptions
const selectedTarget = ctx.selectedTarget
const currentModel = ctx.currentModel
const activeInstanceId = computed(() => ctx.instance.value?.id || null)
const canPickModel = computed(() => !!activeInstanceId.value && !!selectedTarget.value)

const uid = `model-switcher-${useId()}`
const applying = ref(false)
const popoverVisible = ref(false)
const searchQuery = ref("")

// Mobile → bottom drawer (full-bleed sheet).  Desktop → right-side
// drawer (sized to fit common widths without dominating the screen).
// Drawer position is viewport-relative, so it stays correct under
// CSS ``zoom`` on ``<html>`` regardless of zoom level.
const { isCompact } = useDensity()

const drawerDirection = computed(() => (isCompact.value ? "btt" : "rtl"))
const drawerSize = computed(() => (isCompact.value ? "85%" : "min(640px, 90vw)"))

const draftProvider = ref("")
const draftPreset = ref("")
const draftSelections = reactive({})
const catalog = useSwitcherCatalog(ctx.inventory.catalog, popoverVisible, draftProvider, models)
const catalogLimit = ref(50)
watch([draftProvider, searchQuery, popoverVisible], () => {
  catalogLimit.value = 50
})
const { backend: catalogBackend, visible: showCatalog, showSaved, selectedId, busy: catalogBusy, error: catalogError, providersError } = catalog
const filteredCatalog = computed(() => {
  const q = searchQuery.value.toLowerCase().trim()
  return catalog.rows.value.filter((row) => [row.name, row.id, row.preset?.name, row.preset?.model, ...(row.preset_choices || []), draftProvider.value].some((s) => s?.toLowerCase().includes(q)))
})
const needsSave = computed(() => showCatalog.value && catalog.selected.value && !catalog.selected.value.has_preset)
async function selectSavedAlias(name) {
  searchQuery.value = ""
  showSaved.value = true
  selectPreset(name)
  if (!draftPresetData.value) {
    await refreshModels()
    if (draftPreset.value === name && !draftPresetData.value) ElMessage.error("Saved preset unavailable. Refresh the provider list.")
  }
  syncDraftChoices()
}

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
  const contextGroup = !!currentEntry.value?.variation_groups && "context" in currentEntry.value.variation_groups
  return entries.map(([g, o]) => (g === "context" && !contextGroup && formatContext(o) ? `context=${formatContext(o)}` : `${g}=${o}`)).join(", ")
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
  for (const { name, available } of catalog.providers.value) {
    map.set(name, { name, available, discoverable: true })
  }
  return Array.from(map.values()).sort((a, b) => {
    if (a.available !== b.available) return a.available ? -1 : 1
    return a.name.localeCompare(b.name)
  })
})

// The selected provider tab takes keyboard focus; the first tab does when a search hides it.
function providerTabIndex(name, index) {
  const shown = providerOptions.value.some((p) => p.name === draftProvider.value)
  return (shown ? name === draftProvider.value : index === 0) ? 0 : -1
}

const filteredPresets = computed(() => {
  const query = searchQuery.value.trim().toLowerCase()
  return models.value
    .filter((m) => (m.provider || m.login_provider) === draftProvider.value)
    .filter((m) => {
      if (!query) return true
      return `${m.name} ${m.model} ${draftProvider.value}`.toLowerCase().includes(query)
    })
    .sort((a, b) => {
      if (a.available !== b.available) return a.available ? -1 : 1
      return a.name.localeCompare(b.name)
    })
})

// Look up the draft preset's full record so the variation panel can
// render its option groups.  ALWAYS match on (provider, name) — under
// the (provider, name) hierarchy the same preset name (e.g. ``gpt-5.4``)
// exists on multiple providers, and a name-only fallback would pull
// variation metadata from whichever provider's row happens to come
// first in ``models.value``.  When the active provider has no match
// (in-flight filter / search edge cases), return null and skip the
// variation panel instead of silently displaying the wrong provider's
// data.
const draftPresetData = computed(() => {
  if (showCatalog.value) {
    const row = catalog.selected.value
    if (!row?.preset_choices?.length) return row?.preset
    const current = currentPreset.value
    return current && providerOf(current) === draftProvider.value && current.model === row.id && row.preset_choices.includes(current.name) ? current : null
  }
  const provider = draftProvider.value
  const name = draftPreset.value
  if (!name) return null
  return filteredPresets.value.find((m) => m.name === name) || models.value.find((m) => m.name === name && (m.provider || m.login_provider) === provider) || null
})

const draftVariationGroups = computed(() => {
  const groups = draftPresetData.value?.variation_groups || {}
  return Object.entries(groups).map(([name, options]) => ({
    name,
    options: Object.keys(options || {}),
  }))
})

// Under the (provider, name) hierarchy, bare names can be ambiguous
// across providers (``gpt-5.4`` exists on codex, openai, openrouter,
// and any custom backend the user added). Always emit ``provider/name``
// so the backend resolver can pick the exact entry without guessing.
function joinSelector(provider, name, selections) {
  if (!name) return ""
  const base = provider ? `${provider}/${name}` : name
  const entries = Object.entries(selections)
    .filter(([, value]) => value)
    .sort(([a], [b]) => a.localeCompare(b))
  if (!entries.length) return base
  return `${base}@${entries.map(([g, o]) => `${g}=${o}`).join(",")}`
}

const currentEntry = computed(() => {
  const { provider, name } = currentParsed.value
  return models.value.find((m) => m.name === name && (!provider || (m.provider || m.login_provider) === provider)) || null
})
const currentContextOverride = computed(() => takeContext(currentParsed.value.selections, currentEntry.value?.variation_groups).context)
// The current model in the form ``draftSelector`` builds, so an untouched draft reads as unchanged.
const currentNormalized = computed(() => {
  const { provider, name, selections } = currentParsed.value
  return withContext(joinSelector(provider, name, takeContext(selections, currentEntry.value?.variation_groups).selections), currentContextOverride.value)
})
const isCurrent = (selector) => selector === currentModel.value || selector === currentNormalized.value

const sameAsCurrent = computed(() => {
  const draft = draftPresetData.value
  const { provider, name } = currentParsed.value
  return !!draft && draft.name === name && (!provider || provider === draftProvider.value)
})
// A preset with its own ``context`` variation group keeps ``context=`` as that group's choice.
const contextClash = computed(() => !!draftPresetData.value?.variation_groups && "context" in draftPresetData.value.variation_groups)
const draftDefaultContext = computed(() => {
  const draft = draftPresetData.value
  if (!draft) return 0
  const live = toValue(ctx.currentMaxContext)
  if (sameAsCurrent.value && live && !currentContextOverride.value) return live
  return draft.max_context || 0
})
const draftContextWindow = computed(() => {
  const own = draftPresetData.value?.context_window || (showCatalog.value && draftPresetData.value?.model === selectedId.value ? catalog.selected.value?.context_window : null)
  if (own) return own
  return (sameAsCurrent.value && toValue(ctx.currentContextWindow)) || null
})
const contextDraft = useContextDraft(draftDefaultContext, draftContextWindow)
const { text: contextInput, pinned: contextPinned, value: contextValue, invalid: contextInvalid, exceeds: contextExceeds, window: contextWindow, suggestions: contextSuggestions, placeholder: contextPlaceholder } = contextDraft

const draftSelector = computed(() => {
  const name = draftPresetData.value?.name
  const selector = joinSelector(draftProvider.value, name, draftSelections)
  return contextClash.value ? selector : withContext(selector, contextDraft.override.value)
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

const providerOf = (preset) => preset.provider || preset.login_provider || ""

// The saved preset the current selector names: by ``provider/name``, then
// by bare name or model id for session data that stores bare ids.
const currentPreset = computed(() => {
  const { provider, name } = currentParsed.value
  if (!name) return null
  if (provider) return models.value.find((m) => providerOf(m) === provider && m.name === name) || null
  return models.value.find((m) => m.name === name) || models.value.find((m) => m.model === name) || null
})

function isCurrentPreset(entry) {
  const current = currentPreset.value
  return !!entry && !!current && entry.name === current.name && entry.model === current.model && (providerOf(entry) || draftProvider.value) === providerOf(current)
}

// The drafted entry's variations: the current model's choices and context when it is the current preset, none otherwise.
function syncDraftChoices() {
  Object.keys(draftSelections).forEach((k) => delete draftSelections[k])
  const entry = draftPresetData.value
  if (!isCurrentPreset(entry)) return false
  const picks = takeContext(currentParsed.value.selections, entry.variation_groups)
  Object.entries(picks.selections).forEach(([g, o]) => (draftSelections[g] = o))
  contextDraft.start(picks.context)
  return true
}

function resetDraftFromCurrent() {
  const matched = currentPreset.value || (!currentParsed.value.provider && models.value[0])
  draftProvider.value = matched ? providerOf(matched) : currentParsed.value.provider || providerOptions.value[0]?.name || ""
  draftPreset.value = matched?.name || ""
  if (!syncDraftChoices()) contextDraft.start("")
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

// Picking a catalog row starts from its defaults; the reset to no row while a list reloads keeps the draft.
watch(
  selectedId,
  (id) => {
    if (id) syncDraftChoices()
  },
  { flush: "sync" },
)
watch(showSaved, syncDraftChoices, { flush: "sync" })

// A freshly listed catalog marks the row that is the current model, with the current choices.
watch(catalog.rows, (rows) => {
  const entry = currentPreset.value
  if (!showCatalog.value || selectedId.value || !entry || providerOf(entry) !== draftProvider.value) return
  const row = rows.find((r) => (r.preset_choices?.length ? r.id === entry.model && r.preset_choices.includes(entry.name) : r.preset?.model === entry.model && r.preset?.name === entry.name))
  if (!row) return
  selectedId.value = row.id
  syncDraftChoices()
})

function toggleVariation(group, option) {
  if (draftSelections[group] === option) {
    delete draftSelections[group]
  } else {
    draftSelections[group] = option
  }
}

function reconcileDraft() {
  if (showCatalog.value) return
  if (!draftPreset.value) {
    resetDraftFromCurrent()
    return
  }
  const preset = models.value.find((model) => model.name === draftPreset.value && (model.provider || model.login_provider || "") === draftProvider.value)
  if (!preset) {
    resetDraftFromCurrent()
    return
  }
  const groups = preset.variation_groups || {}
  for (const [group, option] of Object.entries(draftSelections)) {
    if (!groups[group]?.[option]) delete draftSelections[group]
  }
}

async function refreshInventory() {
  if (showCatalog.value) {
    catalog.refresh()
    return
  }
  if (providersError.value) catalog.refresh()
  await refreshModels()
  reconcileDraft()
}

function onPickTarget(target) {
  if (!target || !isTerrarium.value) return
  ctx.selectTarget(target)
}

async function applySelection() {
  const modelName = draftSelector.value
  if (!canPickModel.value || applying.value || !modelName || isCurrent(modelName) || contextInvalid.value || draftPresetData.value?.provider_missing || (showCatalog.value && !catalogBackend.value.available)) return
  const target = selectedTarget.value
  const wasTerrarium = isTerrarium.value
  applying.value = true
  try {
    if (needsSave.value) {
      const owner = selectionVersion
      await ctx.inventory.catalog.saveProfile({ ...catalog.selected.value.preset, create_only: true })
      if (owner !== selectionVersion) return
      await refreshModels()
      if (owner !== selectionVersion) return
    }
    // The host context owns the route/session/target resolution and state
    // update; it returns the canonical ``provider/name[@variations]`` the
    // backend accepted so the pill matches what ``/model`` would show.
    const canonical = await ctx.switchModel({ target, selector: modelName })
    const label = wasTerrarium ? `Switched ${target} to ${canonical}` : `Switched to ${canonical}`
    ElMessage.success(label)
    popoverVisible.value = false
  } catch (err) {
    ElMessage.error(`Model switch failed: ${err?.response?.data?.detail || err?.message || err}`)
  } finally {
    applying.value = false
  }
}

let selectionVersion = 0
watch([activeInstanceId, selectedTarget, popoverVisible, draftProvider, showSaved, selectedId, draftSelector], () => selectionVersion++, { flush: "sync" })
watch(popoverVisible, (open) => {
  if (!open) return
  resetDraftFromCurrent()
  revalidateIfStale().then(() => reconcileDraft())
})

watch(currentModel, () => {
  if (!popoverVisible.value) resetDraftFromCurrent()
})

let _stopHostChange = null
let _stopOpen = null
onMounted(() => {
  loadModels().then(() => reconcileDraft())
  _stopHostChange = ctx.onHostChange(() => {
    popoverVisible.value = false
    resetDraftFromCurrent()
  })
  _stopOpen = ctx.onOpenRequest(() => (popoverVisible.value = true))
})
onUnmounted(() => {
  selectionVersion++
  if (_stopHostChange) _stopHostChange()
  if (_stopOpen) _stopOpen()
})
</script>

<style>
.status-select {
  --el-input-bg-color: transparent;
  --el-fill-color-blank: transparent;
  --el-border-color: color-mix(in srgb, var(--kt-color-warm-500, #8a8480) 25%, transparent);
  --el-border-color-hover: color-mix(in srgb, var(--kt-color-warm-500, #8a8480) 40%, transparent);
  --el-text-color-regular: currentColor;
}

.target-select {
  width: 7rem;
}
@media (min-width: 768px) {
  .target-select {
    width: 8.5rem;
  }
}

.model-pill {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.25rem 0.6rem;
  min-height: 32px;
  border-radius: 6px;
  border: 1px solid color-mix(in srgb, var(--kt-color-warm-500, #8a8480) 25%, transparent);
  background: transparent;
  color: inherit;
  cursor: pointer;
  transition:
    border-color 0.1s ease,
    background 0.1s ease;
  min-width: 7rem;
  max-width: 24rem;
}
@media (min-width: 768px) {
  .model-pill {
    min-width: 12rem;
  }
}
.model-pill:hover:not(.is-disabled) {
  border-color: color-mix(in srgb, var(--kt-color-warm-500, #8a8480) 50%, transparent);
  background: color-mix(in srgb, var(--kt-color-warm-500, #8a8480) 6%, transparent);
}
.model-pill.is-disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.model-switcher-body {
  container-type: inline-size;
}

.model-switcher-heading {
  margin-bottom: 0.25rem;
  font-size: 10px;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  white-space: nowrap;
  color: #a09a92;
}

/* Narrow drawer: provider tab strip, model list, then variations, stacked. */
.model-switcher-panes {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.model-switcher-providers {
  min-width: 0;
  flex-shrink: 0;
}
.model-switcher-providers .model-switcher-heading {
  display: none;
}
.model-switcher-list {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-width: 0;
  min-height: 8rem;
}
.model-switcher-variations {
  display: flex;
  flex: 0 0 auto;
  flex-direction: column;
  min-width: 0;
  max-height: 30vh;
  overflow-y: auto;
  padding-top: 0.5rem;
  border-top: 1px solid #efece7;
}
html.dark .model-switcher-variations {
  border-top-color: #3a3632;
}

/* Wide drawer: provider rail | model list | variation column. */
@container (min-width: 31.5rem) {
  .model-switcher-panes {
    flex-direction: row;
  }
  .model-switcher-providers {
    display: flex;
    flex-direction: column;
    width: 8rem;
    overflow-y: auto;
  }
  .model-switcher-providers .model-switcher-heading {
    display: block;
  }
  .model-switcher-list {
    flex: 1 1 0;
    min-width: 13rem;
    min-height: 0;
  }
  .model-switcher-variations {
    flex: 0 1 12rem;
    min-width: 9rem;
    max-height: none;
    padding: 0 0 0 0.75rem;
    border-top: 0;
    border-left: 1px solid #efece7;
  }
  html.dark .model-switcher-variations {
    border-left-color: #3a3632;
  }
}

/* Provider tabs: an underlined strip when narrow, a rail with an accent bar when wide. */
.provider-tabs {
  display: flex;
  gap: 0.25rem;
  overflow-x: auto;
  border-bottom: 1px solid #e0dbd4;
}
html.dark .provider-tabs {
  border-bottom-color: #3a3632;
}
.provider-tab {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  flex-shrink: 0;
  min-width: 0;
  height: 1.875rem;
  padding: 0 0.5rem;
  border: 0;
  border-radius: 4px 4px 0 0;
  background: transparent;
  font-size: 12px;
  line-height: 1;
  white-space: nowrap;
  text-align: left;
  color: #6a645f;
  cursor: pointer;
}
.provider-tab::after {
  content: "";
  position: absolute;
  left: 0.25rem;
  right: 0.25rem;
  bottom: 0;
  height: 2px;
  border-radius: 2px;
  background: transparent;
}
.provider-tab:hover {
  background: rgba(160, 154, 146, 0.12);
  color: #3a3632;
}
.provider-tab.is-active {
  color: #5a4fcf;
  font-weight: 600;
}
.provider-tab.is-active::after {
  background: #5a4fcf;
}
.provider-tab.is-unavailable:not(.is-active) {
  color: #a09a92;
}
.provider-tab-count {
  flex-shrink: 0;
  font-size: 10px;
  font-weight: 400;
  font-variant-numeric: tabular-nums;
  color: #a09a92;
}
html.dark .provider-tab {
  color: #c5bfb7;
}
html.dark .provider-tab:hover {
  background: rgba(160, 154, 146, 0.14);
  color: #efece7;
}
html.dark .provider-tab.is-active {
  color: #ddd0f0;
}
html.dark .provider-tab.is-active::after {
  background: #9d94e8;
}
html.dark .provider-tab.is-unavailable:not(.is-active) {
  color: #6a645f;
}
@container (min-width: 31.5rem) {
  .provider-tabs {
    flex-direction: column;
    gap: 1px;
    overflow: visible;
    border-bottom: 0;
  }
  .provider-tab {
    width: 100%;
    height: 1.75rem;
    padding: 0 0.5rem 0 0.625rem;
    border-radius: 4px;
  }
  .provider-tab::after {
    top: 0.375rem;
    bottom: 0.375rem;
    left: 0;
    right: auto;
    width: 2px;
    height: auto;
  }
  .provider-tab.is-active {
    background: rgba(90, 79, 207, 0.09);
  }
  html.dark .provider-tab.is-active {
    background: rgba(157, 148, 232, 0.14);
  }
}

/* Model source: a compact segmented control. */
.source-tabs {
  display: inline-grid;
  grid-auto-flow: column;
  grid-auto-columns: 1fr;
  align-self: flex-start;
  flex-shrink: 0;
  max-width: 100%;
  gap: 2px;
  margin-bottom: 0.5rem;
  padding: 2px;
  border: 1px solid #c5bfb7;
  border-radius: 6px;
}
html.dark .source-tabs {
  border-color: #4a4540;
}
.source-tab {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  height: 1.5rem;
  padding: 0 0.625rem;
  border: 0;
  border-radius: 4px;
  background: transparent;
  font-size: 11px;
  line-height: 1;
  white-space: nowrap;
  color: #6a645f;
  cursor: pointer;
}
.source-tab:hover {
  color: #3a3632;
}
.source-tab.is-active {
  background: #fff;
  color: #3a3632;
  font-weight: 600;
  box-shadow:
    0 1px 2px rgba(26, 24, 22, 0.12),
    0 0 0 1px rgba(26, 24, 22, 0.04);
}
html.dark .source-tab {
  color: #a09a92;
}
html.dark .source-tab:hover {
  color: #e0dbd4;
}
html.dark .source-tab.is-active {
  background: #4a4540;
  color: #efece7;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.4);
}

.provider-tab:focus-visible,
.source-tab:focus-visible,
.model-row:focus-visible,
.variation-chip:focus-visible {
  outline: 2px solid #5a4fcf;
  outline-offset: -2px;
}
html.dark .provider-tab:focus-visible,
html.dark .source-tab:focus-visible,
html.dark .model-row:focus-visible,
html.dark .variation-chip:focus-visible {
  outline-color: #9d94e8;
}

.model-row {
  display: flex;
  flex-direction: column;
  width: 100%;
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
  background: color-mix(in srgb, var(--kt-color-warm-500, #8a8480) 8%, transparent);
}
.model-row.is-active {
  background: color-mix(in srgb, var(--kt-color-iolite, #5a4fcf) 12%, transparent);
  border-color: color-mix(in srgb, var(--kt-color-iolite, #5a4fcf) 30%, transparent);
}
.model-row.is-unavailable {
  opacity: 0.4;
}

.model-switcher-body .context-input {
  width: 11rem;
}

.variation-group-title {
  margin-bottom: 0.25rem;
  overflow: hidden;
  font-size: 10px;
  font-weight: 500;
  white-space: nowrap;
  text-overflow: ellipsis;
  color: #8a8480;
}

.variation-chip {
  max-width: 100%;
  overflow: hidden;
  padding: 0.2rem 0.5rem;
  border-radius: 4px;
  border: 1px solid color-mix(in srgb, var(--kt-color-warm-500, #8a8480) 30%, transparent);
  background: transparent;
  font-size: 11px;
  line-height: 1.3;
  white-space: nowrap;
  text-overflow: ellipsis;
  color: inherit;
  cursor: pointer;
  transition: background 0.1s ease;
}
.variation-chip:hover {
  background: color-mix(in srgb, var(--kt-color-warm-500, #8a8480) 10%, transparent);
}
.variation-chip.is-active {
  background: color-mix(in srgb, var(--kt-color-iolite, #5a4fcf) 14%, transparent);
  border-color: var(--el-color-primary, var(--kt-color-iolite, #5a4fcf));
  color: var(--el-color-primary, var(--kt-color-iolite, #5a4fcf));
  font-weight: 500;
}
html.dark .variation-chip.is-active {
  background: rgba(157, 148, 232, 0.16);
  border-color: #9d94e8;
  color: #ddd0f0;
}

/* Tap target bump on touch devices. */
@media (pointer: coarse) {
  .variation-chip {
    padding: 0.4rem 0.8rem;
    font-size: 13px;
  }
  .provider-tab,
  .source-tab {
    height: 2.25rem;
    font-size: 13px;
  }
  .model-row {
    padding: 0.6rem 0.5rem;
  }
}
</style>
