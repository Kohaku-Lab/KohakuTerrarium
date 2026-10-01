<template>
  <ModalShell @close="$emit('close')">
    <template #title>{{ t("shell.modal.resume.title") }}</template>

    <form class="space-y-4" @submit.prevent="onSubmit">
      <div>
        <input v-model="search" type="text" class="input-field w-full text-sm" :placeholder="t('shell.modal.resume.searchPlaceholder')" />
      </div>

      <div>
        <label class="block text-xs uppercase tracking-wider text-warm-500 mb-1">
          {{ search ? t("shell.modal.resume.matches") : t("shell.modal.resume.recent") }}
        </label>
        <div v-if="loading" class="text-warm-400 italic text-sm py-3 text-center">{{ t("shell.modal.resume.loading") }}</div>
        <div v-else-if="allSessions.length === 0" class="text-warm-400 italic text-sm py-3 text-center">
          {{ search ? t("shell.modal.resume.noMatches") : t("shell.modal.resume.empty") }}
        </div>
        <div v-else class="max-h-72 overflow-y-auto space-y-1 pr-1">
          <label v-for="s in allSessions" :key="s.session_name ?? s.name" class="flex items-start gap-3 px-3 py-2 rounded cursor-pointer transition-colors border border-transparent" :class="selected === (s.session_name ?? s.name) ? 'bg-iolite/10 border-iolite/40' : 'hover:bg-warm-100 dark:hover:bg-warm-900'">
            <input v-model="selected" type="radio" :value="s.session_name ?? s.name" class="mt-1 accent-iolite" />
            <div class="flex-1 min-w-0">
              <div class="text-sm font-medium truncate">
                {{ savedSessionLabel(s) }}
              </div>
              <div v-if="savedSessionLabel(s) !== (s.session_name ?? s.name)" class="text-xs text-warm-400 truncate">{{ s.session_name ?? s.name }}</div>
              <div class="text-xs text-warm-500">
                <span v-if="s.last_active">{{ formatDate(s.last_active) }}</span>
                <span v-if="s.turn_count"> · {{ s.turn_count }} turns</span>
                <span v-if="s.file_size ?? s.size_bytes"> · {{ formatBytes(s.file_size ?? s.size_bytes) }}</span>
                <span v-if="s.config_type"> · {{ s.config_type }}</span>
              </div>
              <div v-if="previewOf(s)" class="text-xs text-warm-500 dark:text-warm-500 italic line-clamp-2 mt-0.5" :title="previewOf(s, 600)">
                {{ previewOf(s) }}
              </div>
            </div>
          </label>
        </div>
      </div>

      <div v-if="total > pageSize" class="flex items-center justify-between gap-2 text-xs">
        <button type="button" class="btn-secondary" :disabled="loading || offset === 0" @click="changePage(-1)">{{ t("sessions.prev") }}</button>
        <span>{{ offset + 1 }}–{{ Math.min(offset + pageSize, total) }} / {{ total }}</span>
        <button type="button" class="btn-secondary" :disabled="loading || offset + pageSize >= total" @click="changePage(1)">{{ t("sessions.next") }}</button>
      </div>

      <SitePicker v-model="onNode" :label="t('cluster.resume.label')" />

      <label class="flex items-center gap-2 text-sm">
        <input v-model="alsoOpenInspector" type="checkbox" class="accent-iolite" />
        {{ t("shell.modal.resume.alsoOpenInspector") }}
      </label>

      <div v-if="errorMsg" class="text-coral text-xs">{{ errorMsg }}</div>
    </form>

    <template #footer>
      <div class="flex justify-end gap-2">
        <button class="btn-secondary text-xs px-3 py-1.5" @click="$emit('close')">{{ t("shell.modal.resume.cancel") }}</button>
        <button class="btn-primary text-xs px-3 py-1.5" :disabled="!canSubmit" @click="onSubmit">
          {{ resuming ? t("shell.modal.resume.resuming") : t("shell.modal.resume.resume") }}
        </button>
      </div>
    </template>
  </ModalShell>
</template>

<script setup>
import { savedSessionLabel } from "@/utils/sessionLabels"
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue"

import ModalShell from "@/components/common/ModalShell.vue"
import SitePicker from "@/components/cluster/SitePicker.vue"
import { useTabsStore } from "@/stores/tabs"
import { sessionAPI } from "@/utils/api"
import { consumeWorkspaceResumeAction } from "@/utils/workdirPrompt"
import { useI18n } from "@/utils/i18n"
import { extractTextPreview } from "@/utils/multimodal"

const { t } = useI18n()

function previewOf(s, limit = 160) {
  return extractTextPreview(s?.preview, limit)
}

const emit = defineEmits(["close"])

const tabs = useTabsStore()
const allSessions = ref([])
const loading = ref(true)
const search = ref("")
const selected = ref(null)
const alsoOpenInspector = ref(false)
const resuming = ref(false)
const errorMsg = ref("")
const onNode = ref("_host")

const pageSize = 20
const offset = ref(0)
const total = ref(0)
let requestGeneration = 0
let searchTimer

async function fetchSessions() {
  const generation = ++requestGeneration
  loading.value = true
  errorMsg.value = ""
  try {
    const data = await sessionAPI.list({ limit: pageSize, offset: offset.value, search: search.value.trim() })
    if (generation !== requestGeneration) return
    const list = Array.isArray(data) ? data : (data?.sessions ?? data?.items ?? [])
    allSessions.value = Array.isArray(list) ? list : []
    total.value = data?.total ?? allSessions.value.length
  } catch (err) {
    if (generation !== requestGeneration) return
    allSessions.value = []
    total.value = 0
    errorMsg.value = err?.response?.data?.detail || err?.message || String(err)
  } finally {
    if (generation === requestGeneration) loading.value = false
  }
}

watch(search, () => {
  requestGeneration++
  clearTimeout(searchTimer)
  selected.value = null
  offset.value = 0
  loading.value = true
  searchTimer = setTimeout(fetchSessions, 300)
})

function changePage(delta) {
  clearTimeout(searchTimer)
  offset.value = Math.max(0, offset.value + delta * pageSize)
  selected.value = null
  fetchSessions()
}

onMounted(fetchSessions)
onBeforeUnmount(() => {
  requestGeneration++
  clearTimeout(searchTimer)
})

// Default the site picker to the session's originating site when the
// saved metadata records it.  Falls back to "_host" otherwise — same
// as before.  Lookup is done lazily so we only consult the selected
// session, not every entry in the list.
watch(selected, (sid) => {
  if (!sid) return
  const match = allSessions.value.find((s) => (s.session_name ?? s.name) === sid)
  const origin = match?.on_node || match?.home_node || match?.node_id
  if (origin) onNode.value = origin
})

const canSubmit = computed(() => Boolean(selected.value && !resuming.value && !loading.value))

async function onSubmit() {
  if (!canSubmit.value) return
  resuming.value = true
  errorMsg.value = ""
  try {
    const id = await tabs.createSession({
      kind: "resume",
      sessionName: selected.value,
      attachMode: alsoOpenInspector.value ? "both" : "chat",
      onNode: onNode.value,
    })
    const workspaceAction = consumeWorkspaceResumeAction()
    if (id || workspaceAction === "history") emit("close")
  } catch (err) {
    errorMsg.value = err?.response?.data?.detail || err?.message || String(err)
  } finally {
    resuming.value = false
  }
}

function formatDate(t) {
  const d = typeof t === "string" ? new Date(t) : new Date(t * 1000)
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" })
}

function formatBytes(b) {
  if (!b) return ""
  if (b < 1024) return `${b} B`
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(0)} KB`
  return `${(b / 1024 / 1024).toFixed(1)} MB`
}
</script>
