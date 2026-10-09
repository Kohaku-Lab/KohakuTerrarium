<template>
  <section class="kt-v2-float kt-v2-edge rounded-xl border shadow-lg w-72 max-w-[calc(100vw-2rem)] overflow-hidden" data-test="lab-history">
    <button type="button" class="w-full h-9 px-3 flex items-center gap-2 text-[12px] font-medium text-warm-700 dark:text-warm-200 hover:bg-warm-100/60 dark:hover:bg-warm-800/40" :aria-expanded="open" data-test="lab-history-toggle" @click="toggle">
      <span class="i-carbon-recently-viewed" />{{ t("lab.history.recentTitle") }}
      <span class="flex-1" />
      <span :class="open ? 'i-carbon-chevron-down' : 'i-carbon-chevron-up'" />
    </button>
    <div v-if="open" class="kt-v2-line border-t">
      <div v-if="error" class="px-3 py-3 text-[11px] text-coral" role="alert">{{ error }}</div>
      <div v-else-if="!rows.length" class="px-3 py-3 text-[11px] text-warm-500">{{ loading ? t("sessions.loading") : t("sessions.noSaved") }}</div>
      <ul v-else>
        <li v-for="r in rows" :key="r.key" class="flex items-center gap-2 px-3 py-1.5 hover:bg-warm-100/60 dark:hover:bg-warm-800/40" :data-test="`lab-history-${r.key}`">
          <button type="button" class="min-w-0 flex-1 text-left" :title="r.line || r.key" @click="view(r)">
            <span class="flex items-center gap-1.5 min-w-0">
              <span class="text-[12px] text-warm-800 dark:text-warm-100 truncate">{{ r.label }}</span>
              <span v-if="r.status === 'crashed' || r.status === 'shutdown'" class="shrink-0 w-1.5 h-1.5 rounded-full" :class="r.status === 'crashed' ? 'bg-coral' : 'bg-amber'" :title="t(`lab.history.status.${r.status}`)" />
            </span>
            <span v-if="r.line" class="block text-[11px] text-warm-600 dark:text-warm-300 truncate" data-test="lab-history-line">{{ r.line }}</span>
            <span class="block text-[10px] text-warm-500 truncate"
              >{{ whenLabel(r.lastActive, t) }}<template v-if="r.agents"> · {{ t("sessions.agentCount", { count: r.agents }) }}</template></span
            >
          </button>
          <button type="button" class="shrink-0 h-6 px-2 rounded-md text-[11px] bg-iolite/10 text-iolite dark:text-iolite-light hover:bg-iolite hover:text-white disabled:opacity-50" :disabled="!!resuming" data-test="lab-history-resume" @click="resume(r)">{{ resuming === r.key ? t("sessions.resuming") : t("common.resume") }}</button>
        </li>
      </ul>
      <button type="button" class="kt-v2-line border-t w-full h-8 text-[11px] text-iolite dark:text-iolite-light hover:underline" data-test="lab-history-all" @click="tabs.openTab({ kind: 'saved-sessions', id: 'saved-sessions' })">{{ t("lab.history.openAll") }}</button>
    </div>
  </section>
</template>

<script setup>
import { computed, onMounted, ref, watch } from "vue"
import { ElMessage } from "element-plus"

import { historyRow, resumeNode, whenLabel } from "@/components/shell/history/historyRows"
import { useTabsStore } from "@/stores/tabs"
import { sessionAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"

/**
 * The last few saved sessions, floating on the bench, each with its
 * one-line summary: resume one where it last ran, view its history, or go
 * to the full History page. Re-reads when
 * `refreshKey` changes (a session started or ended). Starts open only on an
 * empty bench (`defaultOpen`) until the user opens or closes it; that choice
 * is remembered per browser.
 */
const props = defineProps({
  refreshKey: { type: String, default: "" },
  defaultOpen: { type: Boolean, default: true },
})

const OPEN_KEY = "kt.lab.historyOpen"
const { t } = useI18n()
const tabs = useTabsStore()
const sessions = ref([])
const loading = ref(false)
const error = ref("")
const resuming = ref("")
const chosen = ref(readChoice())
const open = computed(() => chosen.value ?? props.defaultOpen)
let request = 0

const rows = computed(() => sessions.value.map(historyRow))

function readChoice() {
  try {
    const raw = localStorage.getItem(OPEN_KEY)
    return raw === "1" ? true : raw === "0" ? false : null
  } catch {
    return null
  }
}

function toggle() {
  chosen.value = !open.value
  try {
    localStorage.setItem(OPEN_KEY, chosen.value ? "1" : "0")
  } catch {
    /* the choice just isn't remembered */
  }
}

async function load() {
  const mine = ++request
  loading.value = true
  error.value = ""
  try {
    const data = await sessionAPI.list({ limit: 5, sort: "last_active" })
    if (mine === request) sessions.value = data?.sessions || []
  } catch (err) {
    if (mine === request) error.value = err?.response?.data?.detail || err?.message || String(err)
  } finally {
    if (mine === request) loading.value = false
  }
}

function view(r) {
  tabs.openTab({ kind: "session-viewer", id: `session:${r.key}`, name: r.key, config_name: r.label })
}

async function resume(r) {
  if (resuming.value) return
  resuming.value = r.key
  try {
    const session = sessions.value.find((s) => s.name === r.key)
    await tabs.createSession({ kind: "resume", sessionName: r.key, attachMode: "chat", onNode: resumeNode(session, "") })
  } catch (err) {
    ElMessage.error(t("sessions.resumeFailed", { message: err?.response?.data?.detail || err?.message || String(err) }))
  } finally {
    resuming.value = ""
  }
}

watch(() => props.refreshKey, load)
onMounted(load)
</script>
