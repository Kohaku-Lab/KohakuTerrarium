<template>
  <div class="h-full min-h-0 overflow-y-auto py-1" data-test="v2-widget-jobs">
    <div v-if="error" class="px-3 py-1 text-[11px] text-coral">{{ error }}</div>
    <div v-if="!rows.length" class="px-3 py-6 text-center text-xs text-warm-400">{{ t("widget.jobs.empty") }}</div>
    <div v-for="j in rows" :key="j.id" class="flex items-center gap-2.5 px-3 py-2 hover:bg-warm-100 dark:hover:bg-warm-800/60">
      <span class="shrink-0 text-sm" :class="j.type === 'subagent' ? 'i-carbon-bot text-taaffeite' : 'i-carbon-tool-kit text-sapphire'" />
      <div class="min-w-0 flex-1">
        <div class="text-[13px] text-warm-800 dark:text-warm-100 truncate font-medium">{{ j.name }}</div>
        <div class="text-[11px] text-warm-400 truncate">
          {{ j.type === "subagent" ? t("widget.jobs.subagent") : t("widget.jobs.tool") }}<template v-if="j.tab"> · {{ tabLabel(j.tab, ctx.chat._rootSourceName) }}</template> · <span class="font-mono">{{ elapsed(j) }}</span>
        </div>
      </div>
      <button v-if="j.type === 'subagent'" class="h-6 px-2 rounded text-[11px] text-iolite dark:text-iolite-light hover:bg-iolite/10 shrink-0" @click="view(j)">{{ t("widget.jobs.view") }}</button>
      <span v-if="j.cancelling" class="text-[11px] text-warm-400 shrink-0">{{ t("widget.jobs.cancelling") }}</span>
      <button v-else class="h-6 px-2 rounded text-[11px] text-coral hover:bg-coral/10 shrink-0" @click="stop(j)">{{ t("widget.stop") }}</button>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from "vue"

import { tabLabel } from "@/components/session-v2/model/creatureKeys"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { jobOwnerTab, jobRows } from "@/components/session-v2/model/status/statusModel"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import { elapsedLabel } from "@/components/session-v2/model/widgets/widgetData"
import { terrariumAPI } from "@/utils/api"

/** Running tools and sub-agents of the session with elapsed time; stop one, or open a sub-agent beside the chat. */
defineProps({ mode: { type: String, default: "widget" } })

const ctx = useSessionV2()
const t = useV2T()
const error = ref("")

const rows = computed(() => jobRows(ctx.chat.runningJobs || {}))

// `_jobTick` is the chat store's visibility-paused 1 s clock while jobs run.
function elapsed(j) {
  void ctx.chat._jobTick
  return elapsedLabel(j.startedAt)
}

async function stop(j) {
  error.value = ""
  try {
    await terrariumAPI.stopCreatureTask(ctx.sessionId.value, ownerOf(j), j.id)
    const job = ctx.chat.runningJobs?.[j.id]
    if (job) job.cancelling = true
  } catch (err) {
    error.value = err?.response?.data?.detail || err?.message || String(err)
  }
}

function view(j) {
  ctx.closeWidget()
  ctx.openSide("subagent", { parent: ownerOf(j), jobId: j.id, name: j.name, status: "running", live: true })
}

function ownerOf(j) {
  return jobOwnerTab(j, ctx.chat.tabs, ctx.instance.value?.creatures)
}
</script>
