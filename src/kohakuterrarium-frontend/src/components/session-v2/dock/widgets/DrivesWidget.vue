<template>
  <div class="h-full min-h-0 flex flex-col" data-test="v2-widget-drives">
    <div class="shrink-0 px-3 py-1.5"><DriveCountBadges :counts="store.counts" /></div>
    <div class="flex-1 min-h-0 overflow-y-auto">
      <div v-if="store.error && !store.order.length" class="px-3 py-1 text-[11px] text-coral">{{ store.error }}</div>
      <div v-if="!store.order.length" class="px-3 py-6 text-center text-xs text-warm-400">{{ t("widget.drives.empty") }}</div>
      <DriveSummaryRow v-for="r in rows" :key="r.drive_id" :record="r" :selected="isOpen(r)" :flags="store.deliveryFlags[r.drive_id]" @select="open" />
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue"

import DriveCountBadges from "@/components/drives/DriveCountBadges.vue"
import DriveSummaryRow from "@/components/drives/DriveSummaryRow.vue"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { useSessionDrives } from "@/components/session-v2/model/useSessionDrives"
import { useV2T } from "@/components/session-v2/model/v2Strings"

/** The session's drives with status and assignee; a row opens its detail in the side view. The dock keeps the store live. */
const props = defineProps({ mode: { type: String, default: "widget" } })

const ctx = useSessionV2()
const t = useV2T()
const { store } = useSessionDrives(ctx)

const rows = computed(() => (props.mode === "side" ? store.list : store.list.slice(0, 50)))

function isOpen(r) {
  return ctx.side.value?.kind === "drive" && ctx.side.value.payload?.driveId === r.drive_id
}

function open(driveId) {
  ctx.closeWidget()
  ctx.openSide("drive", { driveId })
}
</script>
