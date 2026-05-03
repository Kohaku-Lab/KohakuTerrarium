<template>
  <div v-if="instance" class="h-full overflow-hidden">
    <WorkspaceShell :instance-id="target" @stop="confirmStop = true" />
    <ConfirmStopDialog v-if="confirmStop" :instance="instance" @close="confirmStop = false" @stopped="onStopped" />
  </div>
  <div v-else class="h-full flex flex-col items-center justify-center text-secondary text-sm gap-2">
    <span class="i-carbon-warning-alt text-2xl text-warm-400" />
    <div>{{ loading ? "Loading…" : `Instance ${target} not found.` }}</div>
    <button v-if="!loading" class="text-xs text-iolite hover:underline" @click="loadInstance">Retry</button>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, provide, ref, watch } from "vue"

import WorkspaceShell from "@/components/layout/WorkspaceShell.vue"
import ConfirmStopDialog from "@/components/shell/tabs/ConfirmStopDialog.vue"
import { useArtifactDetector } from "@/composables/useArtifactDetector"
import { createVisibilityInterval } from "@/composables/useVisibilityInterval"
import { provideScope } from "@/composables/useScope"
import { useChatStore } from "@/stores/chat"
import { useEditorStore } from "@/stores/editor"
import { useInstancesStore } from "@/stores/instances"
import { useLayoutStore } from "@/stores/layout"
import { useTabsStore } from "@/stores/tabs"

const props = defineProps({ tab: { type: Object, required: true } })

const target = computed(() => props.tab.target)
const instances = useInstancesStore()

// Provide the scope for every per-instance store before resolving any
// of them. ChatPanel + descendants will inject this scope and land on
// the per-attach Pinia store rather than the legacy singleton — so two
// attach tabs for two creatures of the same config (e.g. two
// ``general`` instances) don't share state, WS, or messages.
provideScope(target.value)

// Pass scope EXPLICITLY here. Vue 3's ``inject()`` does not see the
// caller's own ``provide()`` — it only walks ancestor provides. So
// ``useChatStore()`` (which would call ``injectScope()`` internally)
// would return the default singleton in this very component, while
// every descendant correctly resolves to the scoped store. The result
// would be ``initForInstance`` populating the default store while
// ChatPanel / StatusBar / AppHeader / ModelSwitcher read an empty
// scoped one. Passing the scope in by hand keeps writes and reads on
// the same store.
const chat = useChatStore(target.value)
const editor = useEditorStore(target.value)
const layout = useLayoutStore(target.value)
const tabsStore = useTabsStore()

const isActiveAttachTab = computed(() => tabsStore.activeId === props.tab.id)

// Per-scope artifact scanning. App.vue keeps its own (default-scope)
// detector for the v1 page-routed flow; this one feeds the scoped
// canvas store from the scoped chat store. Without it, image_url
// parts from agent-side image generation never reach the macro
// shell's Canvas panel because the global detector reads the empty
// default chat store. Pass active state so returning to this macro tab
// forces a catch-up scan for artifacts that arrived while hidden.
useArtifactDetector(target.value, { active: isActiveAttachTab })

const loadedInstance = ref(null)
const loading = ref(true)
const confirmStop = ref(false)
let refreshTimer = null

const instance = computed(() => {
  const id = target.value
  if (!id) return null
  if (loadedInstance.value?.id === id) return loadedInstance.value
  if (instances.current?.id === id) return instances.current
  return instances.list.find((it) => it.id === id) || null
})

// Provide panelProps for the existing WorkspaceShell zones, mirroring
// pages/instances/[id].vue exactly (chat panel + status + files + …).
const panelProps = computed(() => ({
  chat: { instance: instance.value },
  "status-dashboard": { instance: instance.value, onOpenTab: handleOpenTab },
  activity: { instance: instance.value },
  state: { instance: instance.value },
  creatures: { instance: instance.value },
  files: {
    root: instance.value?.pwd || "",
    onSelect: (path) => editor.openFile(path),
  },
  "file-tree": {
    root: instance.value?.pwd || "",
    onSelect: (path) => editor.openFile(path),
  },
  settings: { instance: instance.value },
  modules: { instance: instance.value },
  debug: { instance: instance.value },
  terminal: { instance: instance.value },
  "status-tab": { instance: instance.value, onOpenTab: handleOpenTab },
}))
provide("panelProps", panelProps)

async function loadInstance() {
  const id = target.value
  if (!id) return
  loading.value = true
  try {
    // If chat is bound to a different surface, clear it before init
    // (matches pages/instances/[id].vue; ChatPanel handles _instanceId
    // as the canonical field — we don't rename it).
    if (chat._instanceId && chat._instanceId !== id) {
      chat.resetForRouteSwitch()
    }
    const loaded = await instances.fetchOne(id)
    if (!loaded) {
      loadedInstance.value = null
      return
    }
    loadedInstance.value = loaded
    // ``initialTab`` is only honoured on a *fresh* switch into this
    // chat instance. On remount (e.g. user toggles to the graph
    // editor tab and back) or on the 5 s poll, ``chat._instanceId``
    // already matches and we pass null so the user's current sub-tab
    // selection is preserved instead of getting snapped back.
    const isFreshSwitch = chat._instanceId !== id
    const initialTab = isFreshSwitch ? props.tab.initialTab || null : null
    chat.initForInstance(loaded, { initialTab })
    applyPreset()
  } finally {
    loading.value = false
  }
}

function applyPreset() {
  const id = target.value
  if (!id) return
  layout.loadInstanceOverrides(id)
  const remembered = layout.getInstancePresetId(id)
  if (remembered && layout.allPresets[remembered]) {
    layout.switchPreset(remembered)
    return
  }
  const fallback = instance.value?.type === "terrarium" ? "multi-creature" : "chat-focus"
  layout.switchPreset(fallback)
}

watch(
  () => layout.activePresetId,
  (id) => {
    const instId = target.value
    if (id && instId && !id.startsWith("legacy-")) {
      layout.rememberInstancePreset(instId, id)
    }
  },
)

function handleOpenTab(tabKey) {
  chat.openTab(tabKey)
}

watch(target, () => loadInstance())

onMounted(async () => {
  await loadInstance()
  refreshTimer = createVisibilityInterval(() => {
    loadInstance().catch((err) => console.error("Instance refresh failed:", err))
  }, 5000)
  refreshTimer.start()
})

onUnmounted(() => {
  if (refreshTimer) {
    refreshTimer.stop()
    refreshTimer = null
  }
})

async function onStopped() {
  confirmStop.value = false
  // Close the attach + inspector tabs for this target.
  await tabsStore.detach(target.value)
}
</script>
