/**
 * The latest saved sessions for the lab: rows as History shows them, the
 * total, quick starts from their configs, and view / resume. Re-reads when
 * `refreshKey` changes from one value to another (a session started or
 * ended); its first value after null is not a change. Older replies are
 * dropped.
 */

import { computed, onMounted, ref, watch } from "vue"
import { ElMessage } from "element-plus"

import { quickStarts } from "@/components/lab/model/quickStarts"
import { historyRow, resumeNode } from "@/components/shell/history/historyRows"
import { useTabsStore } from "@/stores/tabs"
import { sessionAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"

export const RECENT_LIMIT = 12

export function useRecentSessions(refreshKey) {
  const { t } = useI18n()
  const tabs = useTabsStore()
  const sessions = ref([])
  const total = ref(0)
  const loading = ref(false)
  const error = ref("")
  const resuming = ref("")
  let request = 0

  const rows = computed(() => sessions.value.map(historyRow))
  const starts = computed(() => quickStarts(sessions.value))

  async function load() {
    const mine = ++request
    loading.value = true
    error.value = ""
    try {
      const data = await sessionAPI.list({ limit: RECENT_LIMIT, sort: "last_active" })
      if (mine !== request) return
      sessions.value = data?.sessions || []
      total.value = data?.total ?? sessions.value.length
    } catch (err) {
      if (mine === request) error.value = err?.response?.data?.detail || err?.message || String(err)
    } finally {
      if (mine === request) loading.value = false
    }
  }

  function view(row) {
    tabs.openTab({
      kind: "session-viewer",
      id: `session:${row.key}`,
      name: row.key,
      config_name: row.label,
    })
  }

  function openHistory() {
    tabs.openTab({ kind: "saved-sessions", id: "saved-sessions" })
  }

  async function resume(row) {
    if (resuming.value) return
    resuming.value = row.key
    try {
      const session = sessions.value.find((s) => s.name === row.key)
      await tabs.createSession({
        kind: "resume",
        sessionName: row.key,
        attachMode: "chat",
        onNode: resumeNode(session, ""),
      })
    } catch (err) {
      ElMessage.error(
        t("sessions.resumeFailed", {
          message: err?.response?.data?.detail || err?.message || String(err),
        }),
      )
    } finally {
      resuming.value = ""
    }
  }

  watch(refreshKey, (next, prev) => {
    if (prev != null) load()
  })
  onMounted(load)

  return { rows, total, loading, error, resuming, starts, load, view, resume, openHistory }
}
