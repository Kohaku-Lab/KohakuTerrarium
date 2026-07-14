<template>
  <div class="h-full min-h-0 flex flex-col overflow-hidden">
    <div class="px-4 py-2 shrink-0 flex items-center gap-2 text-[12px] text-warm-500">
      <span class="i-carbon-flow" />
      <span>{{ detail.live ? "Live Drives" : "Persisted Drives" }}</span>
      <DriveCountBadges :counts="store.counts" />
      <span class="ml-auto text-[11px] text-warm-400">read-only</span>
    </div>
    <div class="flex-1 min-h-0 flex">
      <div class="saved-list-pane" :class="{ 'w-full': !store.selected }">
        <div v-if="store.loading" class="text-warm-400 text-xs py-6 text-center">Loading…</div>
        <div v-else-if="store.list.length === 0" class="text-warm-400 text-xs py-8 text-center px-3">This session recorded no Drives.</div>
        <div v-else class="flex flex-col">
          <DriveSummaryRow v-for="r in store.list" :key="r.drive_id" :record="r" :selected="store.selectedId === r.drive_id" :flags="store.deliveryFlags[r.drive_id]" @select="store.select" />
        </div>
      </div>
      <div v-if="store.selected" class="saved-detail-pane">
        <DriveDetail :record="store.selected" :detail="store.detail" :deliveries="store.deliveries[store.selectedId] || []" :flags="store.deliveryFlags[store.selectedId]" :allowed-actions="[]" readonly closable @close="store.selectedId = null" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { watch } from "vue"

import DriveCountBadges from "@/components/drives/DriveCountBadges.vue"
import DriveDetail from "@/components/drives/DriveDetail.vue"
import DriveSummaryRow from "@/components/drives/DriveSummaryRow.vue"
import { useSessionDetailStore } from "@/stores/sessionDetail"
import { useDrivesStore } from "@/stores/drives"

// Read-only viewer: no polling/WS, selection stays read-only. A live
// session (inspector) reads the live ``/sessions/{sid}/drives`` route so
// running rows actually show — its saved sidecar read returns [] under
// the writer lock; a saved session reads the persisted sidecar. A distinct
// scope keeps it isolated from any live workspace showing the same name.
const detail = useSessionDetailStore()
const store = useDrivesStore(`${detail.live ? "live" : "saved"}:${detail.name || "session"}`)

watch(
  () => detail.name,
  (name) => {
    if (!name) return
    if (detail.live) store.load(name)
    else store.loadSaved(name)
  },
  { immediate: true },
)
</script>

<style scoped>
.saved-list-pane {
  width: 20rem;
  flex-shrink: 0;
  overflow-y: auto;
  border-right: 1px solid rgba(120, 109, 98, 0.18);
}
.saved-detail-pane {
  flex: 1 1 0;
  min-width: 0;
}
</style>
