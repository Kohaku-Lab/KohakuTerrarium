<template>
  <div class="workspace-shell h-full w-full flex flex-col overflow-hidden">
    <!-- Edit mode banner -->
    <EditModeBanner />

    <!-- Top header: instance info + preset dropdown + Ctrl+K + stop -->
    <AppHeader v-if="showHeader" @stop="$emit('stop')" />

    <!-- Save-as-new-preset modal -->
    <SavePresetModal v-model="saveModalOpen" @saved="onSaved" />

    <!-- Main content area: the split tree fills all remaining space -->
    <div class="flex-1 relative min-h-0">
      <div class="absolute inset-0">
        <LayoutNode
          v-if="treeRoot"
          :key="layout.activePresetId || 'none'"
          :node="treeRoot"
          :instance-id="instanceId"
        />
        <div v-else class="h-full w-full flex items-center justify-center text-warm-400 text-sm">
          No layout preset active. Pick one from the dropdown above.
        </div>
      </div>
    </div>

    <!-- Status bar (always at bottom, outside the tree) -->
    <div class="shrink-0">
      <StatusBar />
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue"

import AppHeader from "@/components/chrome/AppHeader.vue"
import StatusBar from "@/components/chrome/StatusBar.vue"
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

const showHeader = computed(() => {
  const id = layout.activePresetId || ""
  return !id.startsWith("legacy-")
})

const treeRoot = computed(() => {
  const p = layout.activePreset
  if (!p) return null
  return p.tree || null
})

const saveModalOpen = ref(false)
let unsubSaveAs = () => {}

function onSaved() {
  if (layout.editMode) layout.exitEditMode()
}

onMounted(() => {
  unsubSaveAs = onLayoutEvent(LAYOUT_EVENTS.SAVE_AS_REQUESTED, () => {
    saveModalOpen.value = true
  })
})

onUnmounted(() => {
  unsubSaveAs()
})
</script>
