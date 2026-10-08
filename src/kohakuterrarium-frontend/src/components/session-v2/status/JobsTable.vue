<template>
  <StatusSection id="jobs" :title="t('status.jobs')" icon="i-carbon-in-progress" :count="rows.length" :empty="!rows.length" :empty-label="t('status.none.jobs')">
    <div v-if="error" class="px-4 py-2 text-xs text-coral" role="alert">{{ error }}</div>
    <table class="w-full text-xs">
      <thead class="text-warm-500 text-left">
        <tr class="border-b kt-v2-line">
          <th class="font-medium px-4 py-2">{{ t("status.col.name") }}</th>
          <th class="font-medium px-2 py-2">{{ t("status.col.kind") }}</th>
          <th class="font-medium px-2 py-2">{{ t("status.col.creature") }}</th>
          <th class="font-medium px-2 py-2 text-right">{{ t("status.col.elapsed") }}</th>
          <th class="px-4 py-2" />
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="row.id" class="border-b last:border-b-0 kt-v2-line" :data-test="`status-job-${row.id}`">
          <td class="px-4 py-2">
            <span class="inline-flex items-center gap-1.5 font-medium text-warm-800 dark:text-warm-100"><span class="i-carbon-circle-dash animate-spin text-aquamarine" />{{ row.name }}</span>
          </td>
          <td class="px-2 py-2 text-warm-500">{{ row.type }}</td>
          <td class="px-2 py-2 text-warm-600 dark:text-warm-300">{{ row.tab ? tabLabel(row.tab, chat._rootSourceName) : "—" }}</td>
          <td class="px-2 py-2 text-right font-mono text-warm-500">{{ chat.getJobElapsed(chat.runningJobs[row.id]) || "—" }}</td>
          <td class="px-4 py-2 text-right">
            <span v-if="row.cancelling" class="text-warm-500">{{ t("status.cancelling") }}</span>
            <button v-else class="kt-v2-b px-2 py-0.5 rounded border border-coral/30 text-coral hover:bg-coral/10" @click="stopJob(row)">{{ t("status.stop") }}</button>
          </td>
        </tr>
      </tbody>
    </table>
  </StatusSection>
</template>

<script setup>
import { computed, ref } from "vue"

import { tabLabel } from "@/components/session-v2/model/creatureKeys"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { jobOwnerTab, jobRows } from "@/components/session-v2/model/status/statusModel"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import StatusSection from "@/components/session-v2/status/StatusSection.vue"
import { terrariumAPI } from "@/utils/api"

/** Running tools and sub-agents with elapsed time; stop one. */
const ctx = useSessionV2()
const t = useV2T()
const chat = ctx.chat
const error = ref("")

const rows = computed(() => jobRows(chat.runningJobs))

const ownerOf = (row) => jobOwnerTab(row, chat.tabs, ctx.instance.value?.creatures)

async function stopJob(row) {
  error.value = ""
  try {
    await terrariumAPI.stopCreatureTask(ctx.sessionId.value, ownerOf(row), row.id)
    const job = chat.runningJobs[row.id]
    if (job) job.cancelling = true
  } catch (err) {
    error.value = t("status.stopFailed", { name: row.name, error: err?.response?.data?.detail || err?.message || String(err) })
  }
}
</script>
