<template>
  <div
    v-if="isVisible && slots.length > 0"
    class="zone h-full w-full overflow-hidden"
    :data-zone-id="zoneId"
  >
    <!-- Single slot: render panel directly, no splitter. -->
    <template v-if="slots.length === 1">
      <ZoneSlot :slot-info="slots[0]" :instance-id="instanceId" :panel-props="panelProps" />
    </template>
    <!-- Multiple slots: stack vertically with Splitpanes by default,
         honoring a per-slot size if provided. -->
    <template v-else>
      <Splitpanes horizontal class="zone__split" :dbl-click-splitter="false">
        <Pane
          v-for="(slot, idx) in slots"
          :key="slot.panelId + ':' + idx"
          :size="slotSize(slot, idx)"
        >
          <ZoneSlot :slot-info="slot" :instance-id="instanceId" :panel-props="panelProps" />
        </Pane>
      </Splitpanes>
    </template>
  </div>
  <!-- Edit-mode empty placeholder (Phase 5 will enable). -->
  <div
    v-else-if="showEmpty"
    class="zone zone--empty flex items-center justify-center text-xs text-warm-400 h-full"
  >
    <span>no panel · {{ zoneId }}</span>
  </div>
</template>

<script setup>
import { Pane, Splitpanes } from "splitpanes"
import "splitpanes/dist/splitpanes.css"

import { computed, inject } from "vue"

import { useLayoutStore } from "@/stores/layout"
import ZoneSlot from "./ZoneSlot.vue"

const props = defineProps({
  zoneId: { type: String, required: true },
  instanceId: { type: String, default: null },
  /** When true, empty zones render a placeholder. Phase 5 flips this on. */
  showEmpty: { type: Boolean, default: false },
})

const layout = useLayoutStore()
// Shared panel props map injected by WorkspaceShell's parent route.
// Shape: `{ [panelId]: Record<string, any> }`.
const panelProps = inject("panelProps", () => ({}), true)

const preset = computed(() => layout.effectivePreset(props.instanceId))

const zoneMeta = computed(() => preset.value?.zones?.[props.zoneId] || {})

const isVisible = computed(() => zoneMeta.value.visible !== false)

const slots = computed(() => layout.slotsForZone(props.zoneId, props.instanceId))

function slotSize(slot, idx) {
  if (typeof slot.size === "number") return slot.size
  // Equal distribution fallback.
  return 100 / slots.value.length
}
</script>

<style scoped>
.zone__split {
  height: 100%;
  width: 100%;
}
</style>
