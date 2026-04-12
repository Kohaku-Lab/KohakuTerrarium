<template>
  <div class="h-full overflow-y-auto">
    <div class="container-page max-w-5xl">
      <!-- Header -->
      <div class="mb-6">
        <h1 class="text-xl font-bold text-warm-800 dark:text-warm-200 mb-1">Saved Sessions</h1>
        <p class="text-secondary">Resume a previous agent or terrarium session.</p>
      </div>

      <!-- Loading state -->
      <div v-if="loading" class="card p-12 text-center text-secondary">
        <div class="i-carbon-renew kohaku-pulse text-2xl mx-auto mb-3 text-amber" />
        <div>Loading sessions...</div>
      </div>

      <!-- Error state -->
      <div v-else-if="error" class="card p-8 text-center">
        <div class="i-carbon-warning-alt text-2xl mx-auto mb-3 text-coral" />
        <div class="text-warm-700 dark:text-warm-300 mb-3">Failed to load sessions</div>
        <div class="text-secondary text-xs mb-4">{{ error }}</div>
        <button class="btn-secondary" @click="fetchSessions">
          <span class="i-carbon-renew mr-1" /> Retry
        </button>
      </div>

      <!-- Empty state (truly no sessions at all, not just filtered) -->
      <div
        v-else-if="totalSessions === 0 && !searchQuery"
        class="card p-12 text-center text-secondary"
      >
        <div class="i-carbon-time text-3xl mx-auto mb-3 text-warm-400" />
        <div class="text-warm-600 dark:text-warm-400 mb-1">No saved sessions</div>
        <div class="text-xs">Sessions are saved automatically when instances run.</div>
      </div>

      <!-- Search + Session list (always show search once we have any sessions or a query) -->
      <template v-else>
        <div class="mb-4">
          <input
            v-model="searchQuery"
            type="text"
            class="input-field w-full"
            placeholder="Search sessions by name, config, agents, preview..."
          />
        </div>

        <div v-if="sessions.length === 0" class="card p-8 text-center text-secondary">
          No sessions match "{{ searchQuery }}"
        </div>

        <div v-else class="flex flex-col gap-2">
          <div
            v-for="session in sessions"
            :key="session.name"
            class="card-hover p-4 flex items-center gap-4"
          >
            <!-- Icon -->
            <div
              :class="session.config_type === 'terrarium' ? 'i-carbon-network-4' : 'i-carbon-bot'"
              class="text-lg shrink-0"
              :style="{
                color: session.config_type === 'terrarium' ? '#5A4FCF' : '#4C9989',
              }"
            />

            <!-- Info -->
            <div class="flex-1 min-w-0">
              <div class="flex items-center gap-2 mb-0.5">
                <span class="font-medium text-warm-800 dark:text-warm-200 truncate">
                  {{ session.name }}
                </span>
                <GemBadge :gem="session.config_type === 'terrarium' ? 'iolite' : 'aquamarine'">
                  {{ session.config_type }}
                </GemBadge>
              </div>
              <div class="flex items-center gap-3 text-xs text-secondary">
                <span v-if="session.config_path" class="font-mono truncate">
                  {{ session.config_path }}
                </span>
                <span v-if="session.agents && session.agents.length > 0">
                  {{ session.agents.length }} agent{{ session.agents.length !== 1 ? "s" : "" }}
                </span>
                <span
                  v-if="session.pwd"
                  class="font-mono truncate text-warm-400"
                  :title="session.pwd"
                >
                  {{ session.pwd }}
                </span>
              </div>
              <div
                v-if="session.preview"
                class="text-xs text-warm-400 dark:text-warm-500 mt-1 truncate italic"
                :title="session.preview"
              >
                "{{ session.preview }}"
              </div>
            </div>

            <!-- Last active time -->
            <div class="text-xs text-warm-400 shrink-0 text-right min-w-24">
              <div>{{ formatTime(session.last_active) }}</div>
              <div class="text-warm-400/60">
                {{ formatDate(session.last_active) }}
              </div>
            </div>

            <!-- Resume + Delete buttons -->
            <div class="flex gap-2 shrink-0">
              <button
                class="btn-primary flex items-center gap-1"
                :disabled="resuming === session.name"
                :class="{
                  'opacity-50 cursor-not-allowed': resuming === session.name,
                }"
                @click="resumeSession(session)"
              >
                <span
                  :class="
                    resuming === session.name ? 'i-carbon-renew kohaku-pulse' : 'i-carbon-play'
                  "
                />
                {{ resuming === session.name ? "Resuming..." : "Resume" }}
              </button>
              <button
                class="btn-secondary flex items-center gap-1 text-coral hover:bg-coral/10"
                title="Delete session"
                @click="deleteSession(session)"
              >
                <span class="i-carbon-trash-can" />
              </button>
            </div>
          </div>

          <!-- Pagination -->
          <div class="flex items-center justify-between mt-4 text-xs text-warm-400">
            <span>{{ totalSessions }} sessions total</span>
            <div class="flex gap-2">
              <button class="btn-secondary" :disabled="!hasPrev" @click="prevPage">
                <span class="i-carbon-chevron-left" /> Prev
              </button>
              <span class="py-1 px-2">
                {{ currentOffset + 1 }}-{{ Math.min(currentOffset + pageSize, totalSessions) }}
              </span>
              <button class="btn-secondary" :disabled="!hasMore" @click="nextPage">
                Next <span class="i-carbon-chevron-right" />
              </button>
            </div>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { sessionAPI } from "@/utils/api"
import { useInstancesStore } from "@/stores/instances"
import { ElMessage } from "element-plus"

const router = useRouter()
const instances = useInstancesStore()

const sessions = ref([])
const totalSessions = ref(0)
const currentOffset = ref(0)
const pageSize = 20
const loading = ref(false)
const error = ref(null)
const resuming = ref(null)
const searchQuery = ref("")
let _searchTimer = null

// Debounced server-side search
watch(searchQuery, () => {
  clearTimeout(_searchTimer)
  _searchTimer = setTimeout(() => {
    currentOffset.value = 0
    fetchSessions()
  }, 300)
})

const hasMore = computed(() => currentOffset.value + pageSize < totalSessions.value)
const hasPrev = computed(() => currentOffset.value > 0)

async function fetchSessions() {
  loading.value = true
  error.value = null
  try {
    const result = await sessionAPI.list({
      limit: pageSize,
      offset: currentOffset.value,
      search: searchQuery.value.trim(),
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

async function resumeSession(session) {
  resuming.value = session.name
  try {
    const result = await sessionAPI.resume(session.name)
    await instances.fetchAll()
    ElMessage.success(`Resumed session: ${session.name}`)
    router.push(`/instances/${result.instance_id}`)
  } catch (err) {
    ElMessage.error(`Failed to resume: ${err.response?.data?.detail || err.message}`)
  } finally {
    resuming.value = null
  }
}

async function deleteSession(session) {
  if (!confirm(`Delete session "${session.name}"?`)) return
  try {
    await sessionAPI.delete(session.name)
    ElMessage.success("Session deleted")
    await fetchSessions()
  } catch (err) {
    ElMessage.error(`Failed to delete: ${err.response?.data?.detail || err.message}`)
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

  if (diffDays === 0) return "Today"
  if (diffDays === 1) return "Yesterday"
  if (diffDays < 7) return `${diffDays}d ago`
  return d.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
  })
}

fetchSessions()
</script>
