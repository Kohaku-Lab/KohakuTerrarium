<template>
  <div class="h-full flex flex-col min-h-0" data-test="v2-side-drive">
    <div v-if="!record" class="flex-1 flex items-center justify-center text-xs text-warm-400">{{ store.loading ? t("side.drive.loading") : t("side.drive.missing") }}</div>
    <DriveDetail v-else :record="record" :detail="store.selectedId === driveId ? store.detail : null" :deliveries="store.deliveries[driveId] || []" :flags="store.deliveryFlags[driveId]" :allowed-actions="record.allowed_actions || []" :replaying="actions.replaying.value" :pending-proposal="store.pendingProposals[driveId] || null" @transition="actions.onTransition" @wake="actions.onWake" @assign="actions.onAssign" @unassign="actions.onUnassign" @transfer-owner="actions.onTransferOwner" @report-progress="actions.onProgress" @propose-terminal="actions.onProposeTerminal" @verify-terminal="actions.onVerifyTerminal" @replay="actions.onReplay" />
  </div>
</template>

<script setup>
import { computed, watch } from "vue"

import DriveDetail from "@/components/drives/DriveDetail.vue"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { useSessionDrives } from "@/components/session-v2/model/useSessionDrives"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import { useDriveActions } from "@/composables/useDriveActions"

/** One drive's detail beside the chat (`payload.driveId`): readiness, progress, deliveries, and its actions. The dock keeps the store live. */
const props = defineProps({ payload: { type: Object, default: () => ({}) } })

const ctx = useSessionV2()
const t = useV2T()
const { store, ensureLoaded } = useSessionDrives(ctx)
const actions = useDriveActions(store, { sessionId: ctx.sessionId })

const driveId = computed(() => props.payload.driveId || null)
const record = computed(() => (driveId.value ? store.records[driveId.value] || null : null))

// The store's selection drives which record the actions act on; a fresh load clears it, so select after loading.
watch(
  driveId,
  async (id) => {
    await ensureLoaded()
    if (id && store.selectedId !== id) store.select(id)
  },
  { immediate: true },
)
</script>
