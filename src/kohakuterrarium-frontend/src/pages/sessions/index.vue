<template>
  <div class="h-full overflow-y-auto">
    <div class="container-page max-w-5xl">
      <div class="mb-6">
        <h1 class="text-xl font-bold text-warm-800 dark:text-warm-200 mb-1">{{ t("sessions.title") }}</h1>
        <p class="text-secondary">{{ t("sessions.subtitle") }}</p>
      </div>

      <div v-if="loading" class="card p-12 text-center text-secondary">
        <div class="i-carbon-renew kohaku-pulse text-2xl mx-auto mb-3 text-amber" />
        <div>{{ t("sessions.loading") }}</div>
      </div>

      <div v-else-if="error" class="card p-8 text-center">
        <div class="i-carbon-warning-alt text-2xl mx-auto mb-3 text-coral" />
        <div class="text-warm-700 dark:text-warm-300 mb-3">{{ t("sessions.failedTitle") }}</div>
        <div class="text-secondary text-xs mb-4">{{ error }}</div>
        <button class="btn-secondary" @click="fetchSessions(true)"><span class="i-carbon-renew mr-1" /> {{ t("common.retry") }}</button>
      </div>

      <div v-else-if="totalSessions === 0 && !searchQuery" class="card p-12 text-center text-secondary">
        <div class="i-carbon-time text-3xl mx-auto mb-3 text-warm-400" />
        <div class="text-warm-600 dark:text-warm-400 mb-1">{{ t("sessions.noSaved") }}</div>
        <div class="text-xs">{{ t("sessions.noSavedHint") }}</div>
      </div>

      <template v-else>
        <div class="mb-4">
          <input v-model="searchQuery" type="text" class="input-field w-full" :placeholder="t('sessions.searchPlaceholder')" />
        </div>

        <div v-if="sessions.length === 0" class="card p-8 text-center text-secondary">{{ t("sessions.noMatch", { query: searchQuery }) }}</div>

        <div v-else class="flex flex-col gap-2">
          <div v-for="session in sessions" :key="session.name" class="card-hover p-4 flex items-center gap-4">
            <div
              :class="session.config_type === 'terrarium' ? 'i-carbon-network-4' : 'i-carbon-bot'"
              class="text-lg shrink-0"
              :style="{
                color: session.config_type === 'terrarium' ? GEM.iolite.main : GEM.aquamarine.main,
              }"
            />

            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2 mb-0.5 flex-wrap">
                <span class="font-medium text-warm-800 dark:text-warm-200 truncate">
                  {{ session.name }}
                </span>
                <GemBadge :gem="session.config_type === 'terrarium' ? 'iolite' : 'aquamarine'">
                  {{ session.config_type }}
                </GemBadge>
                <!-- Lineage badges (Wave E fork / Wave D migration) -->
                <span v-if="session.parent_session_id" class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-iolite/10 text-iolite-shadow dark:text-iolite-light" :title="`Forked from ${session.parent_session_id} at event ${session.fork_point}`">
                  <span class="i-carbon-fork-vertical text-[10px]" />
                  fork
                </span>
                <span v-if="session.forked_children && session.forked_children.length" class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-aquamarine/10 text-aquamarine-shadow dark:text-aquamarine-light" :title="`${session.forked_children.length} fork(s) of this session`">
                  <span class="i-carbon-tree-view-alt text-[10px]" />
                  {{ session.forked_children.length }} fork{{ session.forked_children.length === 1 ? "" : "s" }}
                </span>
                <span v-if="session.migrated_from_version" class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber/10 text-amber-shadow dark:text-amber-light" :title="`Migrated from format v${session.migrated_from_version}`">
                  <span class="i-carbon-migrate text-[10px]" />
                  migrated v{{ session.migrated_from_version }}
                </span>
                <span v-if="session.format_version && session.format_version > 1" class="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono bg-warm-100 dark:bg-warm-800 text-warm-500" :title="`Format version ${session.format_version}`"> v{{ session.format_version }} </span>
              </div>
              <div class="flex items-center gap-3 text-xs text-secondary">
                <span v-if="session.config_path" class="font-mono truncate">
                  {{ session.config_path }}
                </span>
                <span v-if="session.agents && session.agents.length > 0"> {{ t("sessions.agentCount", { count: session.agents.length }) }} </span>
                <span v-if="session.pwd" class="font-mono truncate text-warm-400" :title="session.pwd">
                  {{ session.pwd }}
                </span>
              </div>
              <div v-if="previewText(session)" class="text-xs text-warm-400 dark:text-warm-500 mt-1 truncate italic" :title="previewText(session, 600)">"{{ previewText(session) }}"</div>
            </div>

            <div class="text-xs text-warm-400 shrink-0 text-right min-w-24">
              <div>{{ formatTime(session.last_active) }}</div>
              <div class="text-warm-400/60">
                {{ formatDate(session.last_active) }}
              </div>
            </div>

            <div class="flex gap-2 shrink-0">
              <button class="btn-secondary flex items-center gap-1" @click="viewSession(session)">
                <span class="i-carbon-view" />
                {{ t("common.view") }}
              </button>
              <button
                class="btn-primary flex items-center gap-1"
                :disabled="resuming === session.name"
                :class="{
                  'opacity-50 cursor-not-allowed': resuming === session.name,
                }"
                @click="resumeSession(session)"
              >
                <span :class="resuming === session.name ? 'i-carbon-renew kohaku-pulse' : 'i-carbon-play'" />
                {{ resuming === session.name ? t("sessions.resuming") : t("common.resume") }}
              </button>
              <button class="btn-secondary flex items-center gap-1 text-coral hover:bg-coral/10" :title="t('common.delete')" @click="deleteSession(session)">
                <span class="i-carbon-trash-can" />
              </button>
            </div>
          </div>

          <div class="flex items-center justify-between mt-4 text-xs text-warm-400">
            <span>{{ t("sessions.total", { count: totalSessions }) }}</span>
            <div class="flex gap-2">
              <button class="btn-secondary" :disabled="!hasPrev" @click="prevPage"><span class="i-carbon-chevron-left" /> {{ t("sessions.prev") }}</button>
              <span class="py-1 px-2"> {{ currentOffset + 1 }}-{{ Math.min(currentOffset + pageSize, totalSessions) }} </span>
              <button class="btn-secondary" :disabled="!hasMore" @click="nextPage">{{ t("sessions.next") }} <span class="i-carbon-chevron-right" /></button>
            </div>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { ElMessage, ElMessageBox } from "element-plus"

import GemBadge from "@/components/common/GemBadge.vue"
import { useInstancesStore } from "@/stores/instances"
import { GEM } from "@/utils/colors"
import { useI18n } from "@/utils/i18n"
import { sessionAPI } from "@/utils/api"
import { extractTextPreview } from "@/utils/multimodal"

function previewText(session, limit = 200) {
  // Backend flattens preview to a string in ``_read_session_entry``,
  // but legacy session-index entries cached during older runs may
  // still surface a list of multi-modal parts. Defensive flatten so
  // the row never renders ``[object Object]`` or a base64 blob.
  return extractTextPreview(session?.preview, limit)
}

// Optional embed callbacks — when this page is mounted as a v2 tab
// (SavedSessionsTab), the host passes navigation callbacks instead of
// relying on vue-router. v1 page callers use the route-based fallback.
const props = defineProps({
  onView: { type: Function, default: null },
  onResume: { type: Function, default: null },
})
const isMobile = inject("mobileLayout", false)
const router = useRouter()
const instances = useInstancesStore()
const { t } = useI18n()

const sessions = ref([])
const totalSessions = ref(0)
const currentOffset = ref(0)
const pageSize = 20
const loading = ref(false)
const error = ref(null)
const resuming = ref(null)
const searchQuery = ref("")
let searchTimer = null

watch(searchQuery, () => {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    currentOffset.value = 0
    fetchSessions()
  }, 300)
})

const hasMore = computed(() => currentOffset.value + pageSize < totalSessions.value)
const hasPrev = computed(() => currentOffset.value > 0)

async function fetchSessions(forceRefresh = false) {
  loading.value = true
  error.value = null
  try {
    // ``refresh: true`` forces the backend to re-scan + re-parse every
    // session file on disk. We respect the 30s server-side cache by
    // default; only the explicit Refresh button (forceRefresh=true)
    // triggers a full rebuild. Without this, opening the Sessions list
    // tab is laggy because every navigation rebuilds the index.
    const result = await sessionAPI.list({
      limit: pageSize,
      offset: currentOffset.value,
      search: searchQuery.value.trim(),
      refresh: forceRefresh,
    })
    sessions.value = result.sessions || []
    totalSessions.value = result.total || 0
  } catch (err) {
    error.value = err.response?.data?.detail || err.message
  } finally {
    loading.value = false
  }
}

function nextPage() {
  currentOffset.value += pageSize
  fetchSessions()
}

function prevPage() {
  currentOffset.value = Math.max(0, currentOffset.value - pageSize)
  fetchSessions()
}

function viewSession(session) {
  if (typeof props.onView === "function") {
    props.onView(session)
    return
  }
  router.push(isMobile ? `/mobile/sessions/${session.name}` : `/sessions/${session.name}`)
}

async function resumeSession(session) {
  resuming.value = session.name
  try {
    const result = await sessionAPI.resume(session.name)
    await instances.fetchAll()
    ElMessage.success(t("sessions.resumed", { name: session.name }))
    if (typeof props.onResume === "function") {
      props.onResume({ session, result })
      return
    }
    router.push(isMobile ? `/mobile/${result.instance_id}` : `/instances/${result.instance_id}`)
  } catch (err) {
    ElMessage.error(t("sessions.resumeFailed", { message: err.response?.data?.detail || err.message }))
  } finally {
    resuming.value = null
  }
}

async function deleteSession(session) {
  try {
    await ElMessageBox.confirm(t("sessions.deleteConfirm", { name: session.name }), t("common.delete"), {
      type: "warning",
      confirmButtonText: t("common.delete"),
      cancelButtonText: t("common.cancel"),
    })
  } catch {
    return // user cancelled
  }
  try {
    await sessionAPI.delete(session.name)
    ElMessage.success(t("sessions.deleted"))
    await fetchSessions()
  } catch (err) {
    ElMessage.error(t("sessions.deleteFailed", { message: err.response?.data?.detail || err.message }))
  }
}

function formatTime(dateStr) {
  if (!dateStr) return "--"
  const d = new Date(dateStr)
  if (isNaN(d.getTime())) return "--"
  return d.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  })
}

function formatDate(dateStr) {
  if (!dateStr) return ""
  const d = new Date(dateStr)
  if (isNaN(d.getTime())) return ""
  const now = new Date()
  const diffMs = now.getTime() - d.getTime()
  const diffDays = Math.floor(diffMs / (1000 * 60 * 60 * 24))

  if (diffDays === 0) return t("sessions.today")
  if (diffDays === 1) return t("sessions.yesterday")
  if (diffDays < 7) return t("sessions.daysAgo", { count: diffDays })
  return d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  })
}

fetchSessions()
</script>
