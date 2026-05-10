<template>
  <div class="h-full flex flex-col overflow-hidden relative">
    <!-- Active panel — KeepAlive preserves state across tab switches
         so e.g. terminal connections and chat scroll position survive.
         The wrapper is `flex flex-col` so panels using `flex-1` to
         claim their parent's height work the same as panels using
         `h-full`. -->
    <div class="flex-1 min-h-0 flex flex-col overflow-hidden">
      <KeepAlive>
        <component :is="activePanel.component" v-if="activePanel?.component" :key="activePanel.id" v-bind="activePanelProps" />
      </KeepAlive>
      <div v-if="!activePanel?.component" class="h-full w-full flex items-center justify-center text-warm-400 text-sm">
        <template v-if="!panels.length">No panels in this preset.</template>
        <template v-else>Loading panel…</template>
      </div>
    </div>

    <CompactTabBar v-if="panels.length > 1" :panels="panels" :active-id="activePanelId" @select="setActive" />
  </div>
</template>

<script setup>
import { computed, inject, watch } from "vue"

import CompactTabBar from "./CompactTabBar.vue"
import { useLayoutStore } from "@/stores/layout"
import { presetLeafPanelIds } from "@/utils/presetTree"

const props = defineProps({
  instanceId: { type: String, default: null },
})

// AttachTab calls provideScope() so descendants resolve their scoped
// store by inject. We're a descendant; useLayoutStore() picks up the
// scope automatically.
const layout = useLayoutStore()

// AttachTab provides the panel-runtime-props map (per-panel kwargs to
// pass through). We re-use the exact same map as the desktop shell so
// the panel sees identical props.
const injectedProps = inject("panelProps", null)

// Inline tabs come from the active preset's leaves in reading order
// (this is what the user has chosen as their workspace). Dedupe in
// case a preset re-uses a panel id (rare but possible for user
// presets).
const presetPanels = computed(() => {
  const preset = layout.activePreset
  if (!preset) return []
  const seen = new Set()
  const out = []
  for (const id of presetLeafPanelIds(preset)) {
    if (seen.has(id)) continue
    seen.add(id)
    const panel = layout.getPanel(id)
    if (!panel) continue
    out.push(panel)
  }
  return out
})

// Drop-up overflow contains every other registered panel — so
// nothing the user might want is gated behind preset choice. Order
// is: preset leaves first (so the bar matches the desktop layout),
// then everything else in registration order.
const panels = computed(() => {
  const presetIds = new Set(presetPanels.value.map((p) => p.id))
  const extras = layout.panelList.filter((p) => p && p.id && !presetIds.has(p.id))
  return [...presetPanels.value, ...extras]
})

const activePanelId = computed(() => {
  const stored = layout.compactActivePanelId
  if (stored && panels.value.some((p) => p.id === stored)) return stored
  return panels.value[0]?.id ?? null
})

const activePanel = computed(() => panels.value.find((p) => p.id === activePanelId.value) || null)

const activePanelProps = computed(() => {
  const map = injectedProps && typeof injectedProps === "object" && "value" in injectedProps ? injectedProps.value : injectedProps
  if (!map || !activePanel.value) return {}
  return map[activePanel.value.id] || {}
})

function setActive(id) {
  layout.setCompactActivePanel(id)
}

// If the active panel disappears (preset switched), fall through to
// the first available leaf and persist that as the new selection.
watch(
  () => panels.value.map((p) => p.id),
  (ids) => {
    if (!ids.length) return
    const stored = layout.compactActivePanelId
    if (stored && ids.includes(stored)) return
    layout.setCompactActivePanel(ids[0])
  },
  { immediate: true },
)
</script>
