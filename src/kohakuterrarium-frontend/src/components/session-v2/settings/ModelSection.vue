<template>
  <SectionShell :title="t('set.model.title')" :hint="isCompact ? t('phone.modelHint') : t('set.model.hint')" :error="error" :status="status" bare>
    <SettingsGroup v-if="isCompact" data-test="v2-settings-models">
      <button v-for="row in rows" :key="row.name" type="button" class="w-full min-h-14 flex items-center gap-3 px-4 py-2 text-left active:bg-warm-200/60 dark:active:bg-warm-800" :data-test="`v2-settings-model-${row.name}`" @click="session.openSheet('model', { agent: row.name })">
        <span class="flex-1 min-w-0">
          <span class="block truncate text-[15px] font-medium text-warm-800 dark:text-warm-100">{{ row.name }}</span>
          <span class="flex items-center gap-2 min-w-0 text-xs">
            <span class="truncate font-mono text-iolite dark:text-iolite-light">{{ row.current || "—" }}</span>
            <span v-if="row.context" class="shrink-0 font-mono text-warm-500">{{ formatTokens(row.context) }}</span>
          </span>
        </span>
        <span class="i-carbon-chevron-right text-warm-400 shrink-0" />
      </button>
    </SettingsGroup>
    <div v-else-if="loading" class="text-sm text-warm-400">{{ t("loading") }}</div>
    <SettingsGroup v-else data-test="v2-settings-models">
      <SettingsRow v-for="row in rows" :key="row.name" :title="row.name" :hint="rowHint(row)" :status="rowErrors[row.name] || ''" tone="error">
        <el-select v-model="drafts[row.name]" size="small" filterable class="!w-80" :placeholder="t('set.model.pick')" :disabled="saving">
          <el-option v-for="m in models" :key="modelId(m)" :label="modelId(m)" :value="modelId(m)" />
        </el-select>
      </SettingsRow>
    </SettingsGroup>
    <template v-if="!isCompact" #actions>
      <span class="flex-1 text-xs text-warm-400">{{ changed.length ? t("set.pending", { n: changed.length }) : "" }}</span>
      <button class="h-8 px-3 rounded-md text-sm text-warm-600 dark:text-warm-300 hover:bg-warm-200/60 dark:hover:bg-warm-800 disabled:opacity-40" :disabled="!changed.length || saving" @click="resetDrafts">{{ t("set.reset") }}</button>
      <button class="h-8 px-4 rounded-md text-sm bg-iolite text-white disabled:opacity-40" :disabled="!changed.length || saving" data-test="v2-settings-models-apply" @click="apply">{{ saving ? t("set.saving") : t("set.apply") }}</button>
    </template>
  </SectionShell>
</template>

<script setup>
import { computed, onMounted, reactive, ref, watch } from "vue"

import { tabKeyFor } from "@/components/session-v2/model/creatureKeys"
import { creatureNames, errorText, formatTokens, modelId } from "@/components/session-v2/model/settings/settingsModel"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import SectionShell from "@/components/session-v2/settings/SectionShell.vue"
import { onShownAgain } from "@/components/session-v2/settings/useSectionTarget"
import SettingsGroup from "@/components/settings/ui/SettingsGroup.vue"
import SettingsRow from "@/components/settings/ui/SettingsRow.vue"
import { useDensity } from "@/composables/useDensity"
import { configAPI, terrariumAPI } from "@/utils/api"

/** Every creature's model in one table; picks are applied together. On phones a creature's row opens the model sheet, which switches it at once. */
const t = useV2T()
const session = useSessionV2()
const { isCompact } = useDensity()
const chat = session.chat

const models = ref([])
const loading = ref(true)
const saving = ref(false)
const error = ref("")
const status = ref("")
const drafts = reactive({})
const rowErrors = reactive({})

function creatureByName(name) {
  return (session.instance.value?.creatures || []).find((c) => c.name === name) || null
}

const liveInfo = (name) => chat.modelByTab?.[tabKeyFor(name, chat._rootSourceName)]

function currentModel(name) {
  const live = liveInfo(name)
  const c = creatureByName(name)
  return live?.llmName || live?.model || c?.llm_name || c?.model || ""
}

function recordModel(name, canonical) {
  for (const key of new Set([name, tabKeyFor(name, chat._rootSourceName)])) {
    chat.modelByTab[key] = { ...(chat.modelByTab[key] || {}), model: canonical, llmName: canonical }
  }
}

const rows = computed(() =>
  creatureNames(session.instance.value).map((name) => {
    const live = liveInfo(name)
    const c = creatureByName(name)
    return { name, current: currentModel(name), context: live?.maxContext || c?.max_context || session.instance.value?.max_context || 0 }
  }),
)
const rowHint = (row) => (row.context ? `${t("set.model.context")}: ${formatTokens(row.context)}` : "")
const changed = computed(() => rows.value.filter((r) => drafts[r.name] && drafts[r.name] !== r.current))

function resetDrafts() {
  for (const r of rows.value) drafts[r.name] = r.current
  for (const k of Object.keys(rowErrors)) delete rowErrors[k]
  status.value = ""
}

async function loadModels() {
  loading.value = !models.value.length
  try {
    models.value = ((await configAPI.getModels()) || []).filter((m) => m.available !== false)
  } catch (err) {
    error.value = errorText(err)
  } finally {
    loading.value = false
  }
}

async function apply() {
  const sid = session.sessionId.value
  if (!sid) return
  saving.value = true
  error.value = ""
  status.value = ""
  let ok = 0
  for (const row of changed.value) {
    delete rowErrors[row.name]
    try {
      const res = await terrariumAPI.switchCreatureModel(sid, row.name, drafts[row.name])
      const canonical = res?.model || drafts[row.name]
      recordModel(row.name, canonical)
      drafts[row.name] = canonical
      baseline[row.name] = canonical
      ok += 1
    } catch (err) {
      rowErrors[row.name] = errorText(err)
    }
  }
  try {
    await session.refresh()
  } finally {
    if (ok) status.value = t("set.model.switched", { n: ok })
    saving.value = false
  }
}

// A row the user has not edited follows the live model; an edited row keeps the pick.
const baseline = {}
watch(
  () => rows.value.map((r) => `${r.name}=${r.current}`).join("|"),
  () => {
    for (const r of rows.value) {
      if (drafts[r.name] === undefined || drafts[r.name] === baseline[r.name]) drafts[r.name] = r.current
      baseline[r.name] = r.current
    }
  },
  { immediate: true },
)

onMounted(loadModels)
onShownAgain(loadModels)
</script>
