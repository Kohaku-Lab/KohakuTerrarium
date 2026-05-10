<template>
  <div v-if="instance" class="h-full overflow-hidden">
    <CompactWorkspaceShell v-if="isCompact" :instance-id="target" />
    <WorkspaceShell v-else :instance-id="target" @stop="confirmStop = true" />
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
import CompactWorkspaceShell from "@/components/shell/CompactWorkspaceShell.vue"
import ConfirmStopDialog from "@/components/shell/tabs/ConfirmStopDialog.vue"
import { useArtifactDetector } from "@/composables/useArtifactDetector"
import { useDensity } from "@/composables/useDensity"
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
const { isCompact } = useDensity()

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

// Lenient instance lookup: a tab opened pre-upgrade was keyed by
// ``creature_id``, but after the graph grows past one member the
// canonical instance handle is the ``graph_id``. Match by either
// identity, or by membership in the graph's creature roster, so
// the tab keeps resolving to its session without forcing the user
// to re-open from the dashboard.
function _matchesInstance(inst, id) {
  if (!inst || !id) return false
  if (inst.id === id) return true
  if (inst.graph_id === id) return true
  return (inst.creatures || []).some((c) => c.creature_id === id || c.agent_id === id || c.name === id)
}

const instance = computed(() => {
  const id = target.value
  if (!id) return null
  if (_matchesInstance(loadedInstance.value, id)) return loadedInstance.value
  if (_matchesInstance(instances.current, id)) return instances.current
  return instances.list.find((it) => _matchesInstance(it, id)) || null
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
    const loaded = await instances.fetchOne(id)
    if (!loaded) {
      loadedInstance.value = null
      return
    }
    loadedInstance.value = loaded
    // Reset chat only when bound to a *truly different* instance
    // (e.g. the session viewer left ``chat._instanceId`` pointing at
    // ``session:<name>``, or the user opened a fresh tab for a
    // different session). Skip the reset when the bound id is just
    // a different handle for the same graph (creature_id ↔ graph_id
    // after a solo→multi upgrade) — otherwise every 5 s poll would
    // wipe the chat state and yank the user out of their tab.
    const sameInstance = chat._instanceId === id || chat._instanceId === loaded.id || chat._instanceId === loaded.graph_id
    if (chat._instanceId && !sameInstance) {
      chat.resetForRouteSwitch()
    }
    // ``initialTab`` is only honoured on a *fresh* switch into this
    // chat instance. On remount (e.g. user toggles to the graph
    // editor tab and back) or on the 5 s poll, ``chat._instanceId``
    // already matches and we pass null so the user's current sub-tab
    // selection is preserved instead of getting snapped back.
    const isFreshSwitch = !sameInstance
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
