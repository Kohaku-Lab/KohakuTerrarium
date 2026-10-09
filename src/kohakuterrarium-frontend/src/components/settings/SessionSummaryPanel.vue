<template>
  <div class="flex flex-col gap-4" data-test="session-summary-settings">
    <div>
      <div class="text-sm font-medium text-warm-800 dark:text-warm-100">{{ t("settings.sessionSummary.title") }}</div>
      <div class="text-xs text-warm-500 mt-1">{{ t("settings.sessionSummary.hint") }}</div>
    </div>
    <div v-if="override" class="text-xs px-3 py-2 rounded bg-amber/10 text-amber-shadow dark:text-amber-light" data-test="session-summary-override">{{ t("settings.sessionSummary.override", { value: override }) }}</div>
    <label class="flex items-center justify-between gap-4">
      <span class="text-sm text-warm-700 dark:text-warm-200">{{ t("settings.sessionSummary.source") }}</span>
      <el-select v-model="form.source" class="!w-72" data-test="session-summary-source" @change="save">
        <el-option v-for="s in sources" :key="s" :value="s" :label="t(`settings.sessionSummary.source.${s}`)" />
      </el-select>
    </label>
    <label class="flex items-center justify-between gap-4">
      <span class="text-sm text-warm-700 dark:text-warm-200">{{ t("settings.sessionSummary.every") }}</span>
      <el-input-number v-model="form.every_n_turns" :min="1" :max="100" data-test="session-summary-every" @change="save" />
    </label>
    <label class="flex items-start justify-between gap-4">
      <span class="text-sm text-warm-700 dark:text-warm-200 pt-1">{{ t("settings.sessionSummary.model") }}</span>
      <span class="flex flex-col gap-1 items-end">
        <el-input v-model="form.model" class="!w-72" placeholder="provider/model" clearable data-test="session-summary-model" @change="save" />
        <span class="text-[11px] text-warm-500">{{ t("settings.sessionSummary.modelHint") }}</span>
      </span>
    </label>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from "vue"
import { ElMessage } from "element-plus"

import { settingsAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"

/**
 * Global session-summary settings: who writes the one-line summaries, how
 * often they refresh, and an optional model that replaces each session's own.
 */
const { t } = useI18n()
const form = reactive({ source: "llm", every_n_turns: 5, model: "" })
const sources = ref(["llm", "compaction", "heuristic", "off"])
const override = ref(null)

function apply(data) {
  if (!data) return
  form.source = data.source
  form.every_n_turns = data.every_n_turns
  form.model = data.model || ""
  if (Array.isArray(data.sources)) sources.value = data.sources
  override.value = data.source_override || null
}

async function save() {
  try {
    apply(await settingsAPI.saveSessionSummary({ source: form.source, every_n_turns: form.every_n_turns, model: form.model || "" }))
    ElMessage.success(t("settings.sessionSummary.saved"))
  } catch (err) {
    ElMessage.error(err?.response?.data?.detail || err?.message || String(err))
  }
}

onMounted(async () => {
  try {
    apply(await settingsAPI.getSessionSummary())
  } catch {
    /* defaults stay shown */
  }
})
</script>
