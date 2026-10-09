<template>
  <StatusSection id="drives" :title="t('status.drives')" icon="i-carbon-task" :count="rows.length" :empty="!rows.length" :empty-label="t('status.none.drives')">
    <div v-if="isCompact">
      <button v-for="row in rows" :key="row.drive_id" type="button" class="kt-v2-line border-x-0 border-t-0 w-full min-h-14 flex items-center gap-3 px-4 py-2 border-b last:border-b-0 text-left active:bg-warm-200/60 dark:active:bg-warm-800" :data-test="`status-drive-${row.drive_id}`" @click="ctx.openSide('drive', { driveId: row.drive_id })">
        <span :class="[statusDisplay(row.status).icon, TONE_TEXT[statusDisplay(row.status).tone]]" class="shrink-0" />
        <span class="flex-1 min-w-0">
          <span class="block truncate text-sm font-medium text-warm-800 dark:text-warm-100">{{ row.title || row.drive_id }}</span>
          <span class="block truncate text-xs text-warm-500">{{ statusDisplay(row.status).label }} · {{ row.kind || "—" }} · {{ assigneeName(row.assignee_creature_id) }}</span>
        </span>
        <span class="i-carbon-chevron-right text-warm-400 shrink-0" />
      </button>
    </div>
    <table v-else class="w-full text-xs">
      <thead class="text-warm-500 text-left">
        <tr class="border-b kt-v2-line">
          <th class="font-medium px-4 py-2">{{ t("status.col.title") }}</th>
          <th class="font-medium px-2 py-2">{{ t("status.col.status") }}</th>
          <th class="font-medium px-2 py-2">{{ t("status.col.kind") }}</th>
          <th class="font-medium px-2 py-2">{{ t("status.col.owner") }}</th>
          <th class="font-medium px-4 py-2">{{ t("status.col.assignee") }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="row.drive_id" class="border-b last:border-b-0 kt-v2-line hover:bg-warm-50 dark:hover:bg-warm-800/40 cursor-pointer" :data-test="`status-drive-${row.drive_id}`" @click="ctx.openSide('drive', { driveId: row.drive_id })">
          <td class="px-4 py-2 font-medium text-warm-800 dark:text-warm-100 truncate max-w-80">{{ row.title || row.drive_id }}</td>
          <td class="px-2 py-2">
            <span class="inline-flex items-center gap-1" :class="TONE_TEXT[statusDisplay(row.status).tone]"><span :class="statusDisplay(row.status).icon" />{{ statusDisplay(row.status).label }}</span>
          </td>
          <td class="px-2 py-2 text-warm-500">{{ row.kind || "—" }}</td>
          <td class="px-2 py-2 text-warm-600 dark:text-warm-300">{{ actorLabel(row.owner) }}</td>
          <td class="px-4 py-2 text-warm-600 dark:text-warm-300">{{ assigneeName(row.assignee_creature_id) }}</td>
        </tr>
      </tbody>
    </table>
  </StatusSection>
</template>

<script setup>
import { computed } from "vue"

import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { useSessionDrives } from "@/components/session-v2/model/useSessionDrives"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import StatusSection from "@/components/session-v2/status/StatusSection.vue"
import { useDensity } from "@/composables/useDensity"
import { TONE_TEXT, actorLabel, statusDisplay } from "@/utils/driveStatus"

/** The session's drives with status, kind, owner and assignee; a row opens its detail beside the chat (full screen on phones). */
const ctx = useSessionV2()
const t = useV2T()
const { isCompact } = useDensity()
const { store } = useSessionDrives(ctx, { poll: true, kept: true })

const rows = computed(() => store.order.map((id) => store.records[id]).filter(Boolean))

function assigneeName(id) {
  if (!id) return t("status.unassigned")
  return (ctx.instance.value?.creatures || []).find((c) => c.creature_id === id)?.name || id
}
</script>
