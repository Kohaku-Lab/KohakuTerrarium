<template>
  <div class="kt-v2 kt-v2-canvas h-full overflow-y-auto" data-test="history">
    <div class="max-w-5xl mx-auto px-4 sm:px-6 py-5 flex flex-col gap-3">
      <header class="flex items-center gap-3 flex-wrap">
        <h1 class="text-lg font-semibold text-warm-800 dark:text-warm-100">{{ t("lab.history.title") }}</h1>
        <span v-if="total" class="text-xs font-mono text-warm-500">{{ total }}</span>
        <span class="flex-1" />
        <label v-if="cluster.showPickers" class="flex items-center gap-2 text-xs text-warm-500">
          {{ t("lab.history.resumeOn") }}
          <el-select v-model="resumeOn" size="small" class="!w-40" data-test="history-resume-on">
            <el-option value="" :label="t('lab.history.lastRan')" />
            <el-option v-for="s in cluster.sites" :key="s.nodeId" :value="s.nodeId" :label="s.isHost ? t('cluster.site.host') : s.nodeId" />
          </el-select>
        </label>
        <button type="button" :class="GHOST" :disabled="loading" :title="t('lab.history.refresh')" data-test="history-refresh" @click="fetchSessions(true)"><span class="i-carbon-renew" :class="loading ? 'animate-spin' : ''" /></button>
      </header>

      <div class="flex items-center gap-2 flex-wrap">
        <div class="kt-v2-edge kt-v2-card !rounded-lg flex items-center gap-2 px-3 h-9 flex-1 min-w-56">
          <span class="i-carbon-search text-warm-400" />
          <input v-model="search" class="flex-1 min-w-0 bg-transparent border-none outline-none text-sm text-warm-800 dark:text-warm-100 placeholder-warm-400" :placeholder="t('sessions.searchPlaceholder')" data-test="history-search" @keydown.escape="search = ''" />
        </div>
        <div class="kt-v2-edge flex rounded-lg border overflow-hidden text-xs" role="radiogroup" :aria-label="t('sessions.sortBy')">
          <button v-for="o in SORTS" :key="o.value" type="button" role="radio" :aria-checked="sort === o.value" class="h-8 px-3" :class="sort === o.value ? 'bg-iolite text-white' : 'text-warm-600 dark:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800'" :data-test="`history-sort-${o.value}`" @click="sort = o.value">{{ t(o.label) }}</button>
        </div>
      </div>

      <div v-if="error" class="kt-v2-card p-6 text-center flex flex-col items-center gap-2" role="alert">
        <span class="text-sm text-warm-700 dark:text-warm-200">{{ t("sessions.failedTitle") }}</span>
        <span class="text-xs text-warm-500">{{ error }}</span>
        <button type="button" :class="GHOST" @click="fetchSessions(true)"><span class="i-carbon-renew" />{{ t("common.retry") }}</button>
      </div>
      <div v-else-if="loading && !rows.length" class="kt-v2-card p-8 text-center text-sm text-warm-500">{{ t("sessions.loading") }}</div>
      <div v-else-if="!rows.length" class="kt-v2-card p-8 text-center flex flex-col gap-1" data-test="history-empty">
        <span class="text-sm text-warm-600 dark:text-warm-300">{{ filtered ? t("sessions.noMatch", { query: search }) : t("sessions.noSaved") }}</span>
        <span v-if="!filtered" class="text-xs text-warm-500">{{ t("sessions.noSavedHint") }}</span>
      </div>

      <ul v-else class="kt-v2-card !p-0 overflow-hidden" :class="loading ? 'opacity-60' : ''">
        <li v-for="r in rows" :key="r.key" class="kt-v2-line border-b last:border-b-0 grid grid-cols-[20px_minmax(0,1fr)_auto] sm:grid-cols-[20px_minmax(0,1fr)_88px_auto] items-center gap-x-3 px-4 py-2.5 hover:bg-warm-100/60 dark:hover:bg-warm-800/40" :data-test="`history-row-${r.key}`">
          <button type="button" class="w-5 h-5 flex items-center justify-center rounded text-warm-500 hover:text-iolite hover:bg-iolite/10" :aria-expanded="expanded.has(r.key)" :title="expanded.has(r.key) ? t('lab.history.hideQuote') : t('lab.history.showQuote')" data-test="history-expand" @click="toggle(r.key)">
            <span :class="expanded.has(r.key) ? 'i-carbon-chevron-down' : 'i-carbon-chevron-right'" />
          </button>
          <div class="min-w-0">
            <div class="flex items-center gap-2 min-w-0">
              <span class="text-[13px] font-medium text-warm-800 dark:text-warm-100 truncate" :title="r.label" data-test="history-label">{{ r.label }}</span>
              <span v-if="r.status" class="shrink-0 text-[10px] px-1.5 rounded flex items-center gap-1" :class="STATUS_CHIP[r.status]" :title="r.status === 'running' ? '' : t(`lab.history.status.${r.status}Hint`)" :data-test="`history-status-${r.status}`"><span class="w-1.5 h-1.5 rounded-full bg-current" />{{ t(`lab.history.status.${r.status}`) }}</span>
              <span v-if="r.labelFrom !== 'recipe' && r.recipe" class="kt-v2-edge shrink-0 max-w-40 truncate text-[10px] px-1.5 rounded border font-mono text-warm-500" :title="r.recipe">{{ r.recipe }}</span>
              <span v-if="r.forkedFrom" class="shrink-0 text-[10px] px-1.5 rounded bg-iolite/10 text-iolite dark:text-iolite-light" :title="r.forkedFrom">{{ t("lab.history.fork") }}</span>
              <span v-if="r.forks" class="shrink-0 text-[10px] px-1.5 rounded bg-aquamarine/10 text-aquamarine-shadow dark:text-aquamarine-light">{{ t("lab.history.forks", { n: r.forks }) }}</span>
              <span v-if="r.migratedFrom" class="shrink-0 text-[10px] px-1.5 rounded bg-amber/10 text-amber-shadow dark:text-amber-light">{{ t("lab.history.migrated", { v: r.migratedFrom }) }}</span>
            </div>
            <div v-if="r.line" class="text-[12px] text-warm-600 dark:text-warm-300 truncate" :title="r.line" data-test="history-line">
              {{ r.line }}<span v-if="r.line === r.summary && r.summaryFrom" class="text-[11px] text-warm-400"> · {{ t(`lab.history.summary.${r.summaryFrom}`) }}</span>
            </div>
            <div class="flex items-center gap-1.5 min-w-0 text-[11px] text-warm-500">
              <span v-if="r.config" class="font-mono shrink-0">{{ r.config }}</span>
              <span v-if="r.agents" class="shrink-0">{{ r.config ? "· " : "" }}{{ t("sessions.agentCount", { count: r.agents }) }}</span>
              <span v-if="r.turns" class="shrink-0">{{ r.config || r.agents ? "· " : "" }}{{ t("lab.history.turns", { n: r.turns }) }}</span>
              <span v-if="r.pwdName" class="font-mono truncate" :title="r.pwd">{{ r.config || r.agents || r.turns ? "· " : "" }}{{ r.pwdName }}</span>
            </div>
          </div>
          <span class="hidden sm:block text-right text-[11px] text-warm-500 tabular-nums" :title="r.lastActive">{{ whenLabel(r.lastActive, t) }}</span>
          <div class="flex items-center gap-1.5">
            <button type="button" :class="GHOST" data-test="history-view" @click="view(r)">
              <span class="i-carbon-view" /><span class="max-sm:hidden">{{ t("common.view") }}</span>
            </button>
            <button type="button" class="h-8 px-3 rounded-lg text-xs bg-iolite text-white hover:bg-iolite-shadow disabled:opacity-50 flex items-center gap-1.5" :disabled="!!resuming" data-test="history-resume" @click="resume(r)"><span :class="resuming === r.key ? 'i-carbon-renew animate-spin' : 'i-carbon-play'" />{{ resuming === r.key ? t("sessions.resuming") : t("common.resume") }}</button>
            <el-dropdown trigger="click" @command="(cmd) => onMore(cmd, r)">
              <button type="button" :class="GHOST" :title="t('common.more')" data-test="history-more"><span class="i-carbon-overflow-menu-horizontal" /></button>
              <template #dropdown>
                <el-dropdown-menu>
                  <el-dropdown-item command="resumeInspector" :disabled="!!resuming"><span class="i-carbon-radar mr-1" />{{ t("lab.history.resumeInspector") }}</el-dropdown-item>
                  <el-dropdown-item divided command="rename"><span class="i-carbon-edit mr-1" />{{ t("lab.history.rename") }}</el-dropdown-item>
                  <el-dropdown-item command="editSummary"><span class="i-carbon-text-short-paragraph mr-1" />{{ t("lab.history.editSummary") }}</el-dropdown-item>
                  <el-dropdown-item command="regenerate"><span class="i-carbon-magic-wand mr-1" />{{ t("lab.history.regenerate") }}</el-dropdown-item>
                  <el-dropdown-item command="buildEmbeddings" :disabled="r.hasVectorIndex"><span class="i-carbon-machine-learning-model mr-1" />{{ t("sessions.buildEmbeddings") }}</el-dropdown-item>
                  <el-dropdown-item command="rebuildEmbeddings" :disabled="!r.hasVectorIndex"><span class="i-carbon-renew mr-1" />{{ t("sessions.rebuildEmbeddings") }}</el-dropdown-item>
                  <el-dropdown-item divided command="delete"><span class="i-carbon-trash-can mr-1 text-coral" />{{ t("common.delete") }}</el-dropdown-item>
                </el-dropdown-menu>
              </template>
            </el-dropdown>
          </div>
          <div v-if="expanded.has(r.key)" class="col-start-2 col-span-2 sm:col-span-3 mt-2 mb-1 flex flex-col gap-2" data-test="history-detail">
            <HistoryQuote :session-key="r.key" :reload-key="r.lastActive" />
          </div>
        </li>
      </ul>

      <footer v-if="total > PAGE" class="flex items-center justify-end gap-2 text-xs text-warm-500">
        <span>{{ offset + 1 }}–{{ Math.min(offset + PAGE, total) }} / {{ total }}</span>
        <button type="button" :class="GHOST" :disabled="loading || offset === 0" data-test="history-prev" @click="page(-1)"><span class="i-carbon-chevron-left" />{{ t("sessions.prev") }}</button>
        <button type="button" :class="GHOST" :disabled="loading || offset + PAGE >= total" data-test="history-next" @click="page(1)">{{ t("sessions.next") }}<span class="i-carbon-chevron-right" /></button>
      </footer>
    </div>
    <BuildEmbeddingsModal v-if="buildTarget" v-model="buildOpen" :session-name="buildTarget.key" :rebuild="buildRebuild" @completed="fetchSessions(true)" />
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue"
import { ElMessage, ElMessageBox } from "element-plus"

import BuildEmbeddingsModal from "@/components/sessions/modals/BuildEmbeddingsModal.vue"
import HistoryQuote from "@/components/shell/history/HistoryQuote.vue"
import { historyRow, resumeNode, whenLabel } from "@/components/shell/history/historyRows"
import { editSummary, regenerateSummary, renameSession } from "@/components/shell/history/sessionLabelActions"
import { useClusterStore } from "@/stores/cluster"
import { useInstancesStore } from "@/stores/instances"
import { useTabsStore } from "@/stores/tabs"
import { sessionAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"

/**
 * Every saved session: search, sort, then view its
 * history or resume it (on the machine it last ran on unless another is
 * picked). Each row shows the name, status, one-line summary and shape; its
 * chevron quotes the latest exchanges. Also renames, edits or regenerates the
 * summary, builds memory embeddings and deletes.
 */
const PAGE = 30
const GHOST = "kt-v2-edge kt-v2-panel h-8 px-2.5 rounded-lg border text-xs text-warm-700 dark:text-warm-200 hover:border-iolite/50 flex items-center gap-1.5 disabled:opacity-50"
const STATUS_CHIP = {
  running: "bg-aquamarine/10 text-aquamarine-shadow dark:text-aquamarine-light",
  crashed: "bg-coral/10 text-coral",
  shutdown: "bg-amber/10 text-amber-shadow dark:text-amber-light",
}
const SORTS = [
  { value: "last_active", label: "lab.history.recent" },
  { value: "created_at", label: "lab.history.created" },
]

const { t } = useI18n()
const tabs = useTabsStore()
const instances = useInstancesStore()
const cluster = useClusterStore()

const sessions = ref([])
const total = ref(0)
const offset = ref(0)
const loading = ref(false)
const error = ref("")
const search = ref("")
const sort = ref("last_active")
const resumeOn = ref("")
const resuming = ref("")
const buildTarget = ref(null)
const buildOpen = ref(false)
const buildRebuild = ref(false)
const expanded = ref(new Set())
let generation = 0
let searchTimer = null

const rows = computed(() => sessions.value.map(historyRow))
const filtered = computed(() => !!search.value.trim())
const byKey = computed(() => new Map(sessions.value.map((s) => [s.name, s])))

async function fetchSessions(refresh = false) {
  const mine = ++generation
  clearTimeout(searchTimer)
  loading.value = true
  error.value = ""
  try {
    const data = await sessionAPI.list({ limit: PAGE, offset: offset.value, search: search.value.trim(), refresh, sort: sort.value })
    if (mine !== generation) return
    sessions.value = data?.sessions || []
    total.value = data?.total || 0
  } catch (err) {
    if (mine === generation) error.value = err?.response?.data?.detail || err?.message || String(err)
  } finally {
    if (mine === generation) loading.value = false
  }
}

// Typing waits for a pause; a sort change reloads at once. Either way older replies are dropped.
watch(search, () => {
  generation++
  clearTimeout(searchTimer)
  offset.value = 0
  loading.value = true
  searchTimer = setTimeout(() => fetchSessions(), 300)
})
watch(sort, () => {
  offset.value = 0
  fetchSessions()
})

function page(delta) {
  offset.value = Math.max(0, offset.value + delta * PAGE)
  fetchSessions()
}

function view(r) {
  tabs.openTab({ kind: "session-viewer", id: `session:${r.key}`, name: r.key, config_name: r.label })
}

async function resume(r, attachMode = "chat") {
  if (resuming.value) return
  resuming.value = r.key
  try {
    const id = await tabs.createSession({ kind: "resume", sessionName: r.key, attachMode, onNode: resumeNode(byKey.value.get(r.key), resumeOn.value) })
    if (id) {
      await instances.fetchAll()
      ElMessage.success(t("sessions.resumed", { name: r.label }))
    }
  } catch (err) {
    ElMessage.error(t("sessions.resumeFailed", { message: err?.response?.data?.detail || err?.message || String(err) }))
  } finally {
    resuming.value = ""
  }
}

async function remove(r) {
  try {
    await ElMessageBox.confirm(t("sessions.deleteConfirm", { name: r.key }), t("common.delete"), { type: "warning", confirmButtonText: t("common.delete"), cancelButtonText: t("common.cancel") })
  } catch {
    return
  }
  try {
    await sessionAPI.delete(r.key)
    ElMessage.success(t("sessions.deleted"))
    await fetchSessions(true)
  } catch (err) {
    ElMessage.error(t("sessions.deleteFailed", { message: err?.response?.data?.detail || err?.message || String(err) }))
  }
}

function toggle(key) {
  const next = new Set(expanded.value)
  if (next.has(key)) next.delete(key)
  else next.add(key)
  expanded.value = next
}

async function relabel(action) {
  if (await action) await fetchSessions(true)
}

function onMore(cmd, r) {
  if (cmd === "resumeInspector") resume(r, "both")
  else if (cmd === "delete") remove(r)
  else if (cmd === "rename") relabel(renameSession(t, r.key, r.labelFrom === "title" ? r.label : ""))
  else if (cmd === "editSummary") relabel(editSummary(t, r.key, r.summary))
  else if (cmd === "regenerate") relabel(regenerateSummary(t, r.key))
  else {
    buildTarget.value = r
    buildRebuild.value = cmd === "rebuildEmbeddings"
    buildOpen.value = true
  }
}

onMounted(() => fetchSessions())
onBeforeUnmount(() => {
  generation++
  clearTimeout(searchTimer)
})
</script>
