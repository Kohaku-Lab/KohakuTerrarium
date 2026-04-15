<template>
  <div class="h-full min-h-0 flex flex-col overflow-hidden">
    <div v-if="!embedded" class="container-page max-w-6xl py-4 flex items-center gap-3 shrink-0">
      <button class="btn-secondary" @click="goBack"><span class="i-carbon-arrow-left mr-1" /> Back</button>
      <div class="min-w-0">
        <h1 class="text-xl font-bold text-warm-800 dark:text-warm-200 truncate">{{ sessionName }}</h1>
        <p class="text-secondary text-sm">Read-only saved session history</p>
      </div>
    </div>

    <div :class="embedded ? 'px-3 py-3' : 'container-page max-w-6xl pb-4'" class="flex-1 min-h-0 overflow-hidden">
      <div class="h-full min-h-0 flex flex-col gap-3 lg:gap-4">
        <div v-if="embedded" class="flex items-center gap-3 shrink-0">
          <button class="btn-secondary" @click="goBack"><span class="i-carbon-arrow-left mr-1" /> Back</button>
          <div class="min-w-0">
            <div class="font-medium text-warm-800 dark:text-warm-200 truncate">{{ sessionName }}</div>
            <div class="text-secondary text-xs">Saved session history</div>
          </div>
        </div>

        <div class="shrink-0 card p-2.5 lg:p-3">
          <div class="text-xs uppercase tracking-wider text-warm-400 mb-2 px-1">Targets</div>
          <div class="flex gap-2 overflow-x-auto lg:grid lg:grid-cols-1 lg:gap-1 lg:overflow-visible">
            <button v-for="tab in chat.tabs" :key="tab" class="text-left px-3 py-2 rounded-lg border transition-colors whitespace-nowrap lg:w-full" :class="chat.activeTab === tab ? 'border-iolite bg-iolite/10 dark:bg-iolite/15' : 'border-warm-200 dark:border-warm-700 hover:border-iolite hover:bg-warm-50 dark:hover:bg-warm-800'" @click="selectTab(tab)">
              <span class="font-medium text-warm-800 dark:text-warm-200">{{ tabLabel(tab) }}</span>
            </button>
          </div>
        </div>

        <div class="flex-1 min-h-0 overflow-hidden">
          <div v-if="loading" class="card h-full flex items-center justify-center text-secondary">Loading history...</div>
          <div v-else-if="error" class="card h-full flex flex-col items-center justify-center text-center p-6">
            <div class="i-carbon-warning-alt text-2xl text-coral mb-3" />
            <div class="text-warm-700 dark:text-warm-300 mb-2">Failed to load session history</div>
            <div class="text-secondary text-xs mb-4">{{ error }}</div>
            <button class="btn-secondary" @click="loadSession">Retry</button>
          </div>
          <div v-else class="h-full min-h-0 overflow-hidden">
            <ChatPanel :instance="viewerInstance" :read-only="true" empty-title="No saved messages" empty-subtitle="This target has no persisted history yet" />
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import ChatPanel from "@/components/chat/ChatPanel.vue"
import { useChatStore, _convertHistory, _replayEvents } from "@/stores/chat"
import { sessionAPI } from "@/utils/api"

const props = defineProps({
  embedded: { type: Boolean, default: false },
})

const isMobile = inject("mobileLayout", false)
const route = useRoute()
const router = useRouter()
const chat = useChatStore()

const sessionName = computed(() => String(route.params.name || ""))
const loading = ref(false)
const error = ref("")
const viewerMeta = ref(null)
const historyTargets = ref([])

const viewerInstance = computed(() => {
  const meta = viewerMeta.value || {}
  const configType = meta.config_type === "terrarium" ? "terrarium" : "creature"
  return {
    id: `session:${sessionName.value}`,
    type: configType,
    config_name: meta.terrarium_name || sessionName.value,
    creatures: (meta.agents || []).filter((name) => name !== "root").map((name) => ({ name, status: "idle" })),
    channels: (meta.terrarium_channels || []).map((ch) => ({ name: ch.name, type: ch.type || "queue" })),
  }
})

function goBack() {
  router.push(isMobile ? "/mobile/sessions" : "/sessions")
}

function resetViewer() {
  chat._cleanup()
  chat._instanceId = `session:${sessionName.value}`
  chat._instanceType = viewerInstance.value.type
  chat.tabs = []
  chat.messagesByTab = {}
  chat.tokenUsage = {}
  chat.runningJobs = {}
  chat.unreadCounts = {}
  chat.queuedMessages = []
  chat.processing = false
  chat.sessionInfo = {
    sessionId: viewerMeta.value?.session_id || "",
    model: "",
    agentName: "",
    compactThreshold: 0,
    maxContext: 0,
  }
}

function ensureTabs(tabs) {
  chat.tabs = tabs
  chat.messagesByTab = Object.fromEntries(tabs.map((tab) => [tab, chat.messagesByTab[tab] || []]))
  if (!chat.activeTab || !tabs.includes(chat.activeTab)) chat.activeTab = tabs[0] || null
}

async function loadTarget(tab) {
  if (!tab) return
  const data = await sessionAPI.getHistory(sessionName.value, tab)
  if (data.events?.length) {
    const { messages, pendingJobs } = _replayEvents(data.messages || [], data.events)
    chat.messagesByTab[tab] = messages
    chat.runningJobs = pendingJobs || {}
  } else {
    chat.messagesByTab[tab] = _convertHistory(data.messages || [])
  }
}

async function loadSession() {
  loading.value = true
  error.value = ""
  try {
    const index = await sessionAPI.getHistoryIndex(sessionName.value)
    viewerMeta.value = index.meta || {}
    historyTargets.value = index.targets || []
    resetViewer()
    ensureTabs(historyTargets.value)
    if (chat.activeTab) {
      await loadTarget(chat.activeTab)
    }
  } catch (err) {
    error.value = err?.response?.data?.detail || err?.message || String(err)
  } finally {
    loading.value = false
  }
}

async function selectTab(tab) {
  chat.activeTab = tab
  if (!chat.messagesByTab[tab]?.length) {
    try {
      await loadTarget(tab)
    } catch (err) {
      error.value = err?.response?.data?.detail || err?.message || String(err)
    }
  }
}

function tabLabel(tab) {
  if (tab === "root") return "Root Agent"
  if (tab.startsWith("ch:")) return `# ${tab.slice(3)}`
  return tab
}

watch(
  () => route.params.name,
  () => {
    chat.activeTab = null
    loadSession()
  },
  { immediate: true },
)
</script>
