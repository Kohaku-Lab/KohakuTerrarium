<template>
  <div class="card p-4 flex flex-col gap-2" data-test="viewer-identity">
    <div class="flex flex-wrap items-start gap-2">
      <div class="min-w-48 flex-1">
        <div class="text-[14px] font-semibold text-warm-800 dark:text-warm-100 truncate" data-test="viewer-title">{{ info.title || sessionName }}</div>
        <div class="text-[12px] text-warm-600 dark:text-warm-300" data-test="viewer-summary">
          <span v-if="info.summary">{{ info.summary }}</span>
          <span v-else class="italic text-warm-400">{{ t("sessionViewer.overview.noSummary") }}</span>
          <span v-if="info.summary && info.summary_source" class="text-[11px] text-warm-400"> · {{ t(`lab.history.summary.${info.summary_source}`) }}</span>
        </div>
      </div>
      <div class="kt-identity-actions flex flex-wrap items-center gap-2">
        <el-button size="small" plain data-test="viewer-rename" @click="act(renameSession(t, sessionName, info.title))"><span class="i-carbon-edit mr-1" />{{ t("lab.history.rename") }}</el-button>
        <el-button size="small" plain data-test="viewer-edit-summary" @click="act(editSummary(t, sessionName, info.summary))"><span class="i-carbon-text-short-paragraph mr-1" />{{ t("lab.history.editSummary") }}</el-button>
        <el-button size="small" plain :loading="regenerating" data-test="viewer-regenerate" @click="regenerate"><span class="i-carbon-magic-wand mr-1" />{{ t("lab.history.regenerate") }}</el-button>
      </div>
    </div>
    <div class="text-[10px] uppercase tracking-wider text-warm-400">{{ t("sessionViewer.overview.latestExchange") }}</div>
    <HistoryQuote :session-key="sessionName" :limit="1" :reload-key="reload" />
  </div>
</template>

<script setup>
import { onMounted, ref, watch } from "vue"

import HistoryQuote from "@/components/shell/history/HistoryQuote.vue"
import { editSummary, regenerateSummary, renameSession } from "@/components/shell/history/sessionLabelActions"
import { sessionAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"

/**
 * What a saved session is: its name and one-line summary (both editable,
 * the summary also regenerable) and its latest exchange, quoted.
 */
const props = defineProps({
  sessionName: { type: String, required: true },
})

const { t } = useI18n()
const info = ref({})
const reload = ref(0)
const regenerating = ref(false)

async function load() {
  try {
    info.value = await sessionAPI.getExchanges(props.sessionName, 1)
  } catch {
    info.value = {}
  }
}

async function act(action) {
  if (await action) {
    reload.value++
    await load()
  }
}

async function regenerate() {
  regenerating.value = true
  try {
    await act(regenerateSummary(t, props.sessionName))
  } finally {
    regenerating.value = false
  }
}

watch(() => props.sessionName, load)
onMounted(load)
</script>

<style scoped>
.kt-identity-actions :deep(.el-button + .el-button) {
  margin-left: 0;
}
</style>
