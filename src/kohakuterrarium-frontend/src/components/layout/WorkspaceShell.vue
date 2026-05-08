<template>
  <div class="workspace-shell h-full w-full flex flex-col overflow-hidden">
    <!-- Edit mode banner -->
    <EditModeBanner />

    <!-- Top header: instance info + preset dropdown + Ctrl+K + stop -->
    <AppHeader v-if="showHeader" :instance-id="instanceId" @stop="$emit('stop')" />

    <!-- Save-as-new-preset modal -->
    <SavePresetModal v-model="saveModalOpen" @saved="onSaved" />

    <!-- Jump-to-multi-creature hint: shown when the active preset
         doesn't surface the creatures panel but the graph has new
         creatures or channels worth seeing. Dismissable until the
         next graph mutation. -->
    <button v-if="showJumpHint" class="shrink-0 flex items-center gap-2 px-3 py-1.5 text-xs bg-iolite/10 hover:bg-iolite/20 border-b border-iolite/30 text-iolite text-left transition-colors" @click="jumpToMultiCreature">
      <span class="i-carbon-network-4 shrink-0" />
      <span class="flex-1">
        This graph now has <strong>{{ instance?.creatures?.length || 0 }}</strong> creature(s) and <strong>{{ instance?.channels?.length || 0 }}</strong> channel(s). Switch to the Multi-creature preset to see them.
      </span>
      <span class="i-carbon-arrow-right shrink-0" />
      <span class="i-carbon-close shrink-0 ml-1 hover:text-coral" :title="'Dismiss'" @click.stop="dismissHint" />
    </button>

    <!-- Main content area: the split tree fills all remaining space -->
    <div class="flex-1 relative min-h-0">
      <div class="absolute inset-0">
        <LayoutNode v-if="treeRoot" :key="layout.activePresetId || 'none'" :node="treeRoot" :instance-id="instanceId" />
        <div v-else class="h-full w-full flex items-center justify-center text-warm-400 text-sm">Loading layout…</div>
      </div>
    </div>

    <!-- Status bar (always at bottom, outside the tree) -->
    <div class="shrink-0">
      <StatusBar :instance-id="instanceId" />
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from "vue"

import AppHeader from "@/components/chrome/AppHeader.vue"
import StatusBar from "@/components/chrome/StatusBar.vue"
import { useInstancesStore } from "@/stores/instances"
import { useLayoutStore } from "@/stores/layout"
import { LAYOUT_EVENTS, onLayoutEvent } from "@/utils/layoutEvents"
import EditModeBanner from "./EditModeBanner.vue"
import LayoutNode from "./LayoutNode.vue"
import SavePresetModal from "./SavePresetModal.vue"

const props = defineProps({
  instanceId: { type: String, default: null },
})

defineEmits(["stop"])

const layout = useLayoutStore()
const instances = useInstancesStore()

const instance = computed(() => {
  if (!props.instanceId) return null
  if (instances.current?.id === props.instanceId) return instances.current
  return instances.list.find((i) => i.id === props.instanceId) || null
})

const showHeader = computed(() => {
  const id = layout.activePresetId || ""
  return !id.startsWith("legacy-")
})

const treeRoot = computed(() => {
  const p = layout.activePreset
  if (!p) return null
  return p.tree || null
})

// Walk a preset tree looking for a leaf with the given panel id.
function _treeContainsPanel(node, panelId) {
  if (!node) return false
  if (node.type === "leaf") return node.panelId === panelId
  for (const child of node.children || []) {
    if (_treeContainsPanel(child, panelId)) return true
  }
  return false
}

const activePresetHasCreaturesPanel = computed(() => {
  const p = layout.activePreset
  if (!p) return false
  return _treeContainsPanel(p.tree, "creatures")
})

// Snapshot the topology size that was already acknowledged via the
// hint dismiss action. The hint re-arms whenever the graph grows
// past the dismissed snapshot.
const dismissedSize = ref({ creatures: 0, channels: 0 })

const currentSize = computed(() => ({
  creatures: instance.value?.creatures?.length || 0,
  channels: instance.value?.channels?.length || 0,
}))

const showJumpHint = computed(() => {
  if (activePresetHasCreaturesPanel.value) return false
  const cur = currentSize.value
  // Only nudge when the graph is interesting AND has grown since
  // the user dismissed last time.
  if (cur.creatures < 2 && cur.channels < 1) return false
  if (cur.creatures <= dismissedSize.value.creatures && cur.channels <= dismissedSize.value.channels) return false
  return true
})

function jumpToMultiCreature() {
  layout.switchPreset("multi-creature")
}

function dismissHint() {
  dismissedSize.value = { ...currentSize.value }
}

// Self-heal: if no preset is active when this shell mounts (or the
// stored id no longer resolves), pick a sensible default rather than
// stranding the user on the "No layout preset active" empty state.
function ensurePresetActive() {
  if (layout.activePreset) return
  const inst = instance.value
  const fallback = inst && (inst.creatures?.length || 0) > 1 ? "multi-creature" : "chat-focus"
  layout.switchPreset(fallback)
  if (!layout.activePreset) {
    // builtin lookup failed for some reason — try chat-focus as a last resort
    layout.switchPreset("chat-focus")
  }
}

watch(
  () => [props.instanceId, instance.value?.id],
  () => ensurePresetActive(),
  { immediate: true },
)

watch(
  () => layout.activePresetId,
  () => ensurePresetActive(),
)

const saveModalOpen = ref(false)
let unsubSaveAs = () => {}

function onSaved() {
  if (layout.editMode) layout.exitEditMode()
}

onMounted(() => {
  ensurePresetActive()
  unsubSaveAs = onLayoutEvent(LAYOUT_EVENTS.SAVE_AS_REQUESTED, () => {
    saveModalOpen.value = true
  })
})

onUnmounted(() => {
  unsubSaveAs()
})
</script>
