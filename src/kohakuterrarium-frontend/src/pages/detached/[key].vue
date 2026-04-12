<template>
  <div class="h-screen w-screen flex flex-col bg-warm-50 dark:bg-warm-950 overflow-hidden">
    <!-- Minimal top bar with panel label + reattach -->
    <div
      class="flex items-center gap-2 px-3 h-7 border-b border-warm-200 dark:border-warm-700 bg-white dark:bg-warm-900 text-xs shrink-0"
    >
      <div :class="panelIcon" class="text-[12px] text-warm-500" />
      <span class="font-medium text-warm-700 dark:text-warm-300">{{ panelLabel }}</span>
      <span class="text-warm-400">·</span>
      <span class="text-warm-500 truncate max-w-64">{{ instance?.config_name || "—" }}</span>
      <span class="flex-1" />
      <button
        class="px-2 py-0.5 rounded bg-warm-100 dark:bg-warm-800 text-warm-600 dark:text-warm-300 hover:text-iolite transition-colors"
        title="Close this window (the panel returns to the main window)"
        @click="onReattach"
      >
        <div class="i-carbon-chevron-right inline-block text-[11px] mr-1" />
        Reattach
      </button>
    </div>

    <!-- Single-panel body -->
    <div class="flex-1 min-h-0">
      <component :is="panel?.component" v-if="panel?.component" v-bind="resolvedProps" />
      <div v-else class="h-full flex items-center justify-center text-warm-400 text-xs">
        <div class="text-center">
          <div class="i-carbon-warning-alt text-2xl mb-2 mx-auto opacity-40" />
          Panel "{{ parsedKey.panelId }}" not registered.
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, provide, watch } from "vue"

import { useChatStore } from "@/stores/chat"
import { useInstancesStore } from "@/stores/instances"
import { useLayoutStore } from "@/stores/layout"

const route = useRoute()
const layout = useLayoutStore()
const instances = useInstancesStore()
const chat = useChatStore()

// Detached URL shape: /detached/<instanceId>--<panelId>
// The `--` separator keeps the routing simple (single param) while
// still carrying both pieces of information.
const parsedKey = computed(() => {
  const raw = String(route.params.key || "")
  const idx = raw.indexOf("--")
  if (idx < 0) return { instanceId: raw, panelId: "" }
  return { instanceId: raw.slice(0, idx), panelId: raw.slice(idx + 2) }
})

const panel = computed(() => layout.getPanel(parsedKey.value.panelId))
const panelLabel = computed(() => panel.value?.label || parsedKey.value.panelId)
const panelIcon = computed(() => {
  const map = {
    chat: "i-carbon-chat",
    canvas: "i-carbon-canvas",
    debug: "i-carbon-debug",
    activity: "i-carbon-pulse",
    state: "i-carbon-data-structured",
    creatures: "i-carbon-network-4",
    files: "i-carbon-folder",
    "monaco-editor": "i-carbon-code",
    settings: "i-carbon-settings",
  }
  return map[parsedKey.value.panelId] || "i-carbon-panel-expansion"
})

const instance = computed(() => instances.current)

// Runtime props map (mirrors what the main routes provide).
const panelProps = computed(() => ({
  chat: { instance: instance.value },
  "status-dashboard": { instance: instance.value },
  activity: { instance: instance.value },
  state: { instance: instance.value },
  creatures: { instance: instance.value },
  files: { root: instance.value?.pwd || "", onSelect: () => {} },
  settings: { instance: instance.value },
  debug: { instance: instance.value },
  "editor-status": { instance: instance.value },
}))
provide("panelProps", panelProps)

const resolvedProps = computed(() => panelProps.value[parsedKey.value.panelId] || {})

// Tab title reflects what's in the window.
watch(
  [panelLabel, instance],
  ([l, i]) => {
    if (typeof document !== "undefined") {
      document.title = `${l} · ${i?.config_name || "KT"}`
    }
  },
  { immediate: true },
)

async function loadInstance() {
  const { instanceId, panelId } = parsedKey.value
  if (!instanceId) return
  await instances.fetchOne(instanceId)
  if (instance.value) chat.initForInstance(instance.value)
  // Mark detached in the local layout store. Since detached windows
  // run their own pinia instance, the main window learns about this
  // via localStorage on its next reactive read or reload.
  if (panelId) layout.markDetached(panelId, instanceId)
}

function onReattach() {
  const { instanceId, panelId } = parsedKey.value
  if (panelId) layout.unmarkDetached(panelId, instanceId)
  if (typeof window !== "undefined") window.close()
}

onMounted(loadInstance)
onBeforeUnmount(() => {
  const { instanceId, panelId } = parsedKey.value
  if (panelId) layout.unmarkDetached(panelId, instanceId)
})
</script>
