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

        <div v-if="reasoningEntries.length" class="shrink-0 card p-3">
          <button class="flex items-center gap-2 text-xs font-medium text-iolite dark:text-iolite-light" @click="showReasoning = !showReasoning">
            <span class="i-carbon-chevron-down transition-transform" :class="{ 'rotate-180': showReasoning }" />
            {{ showReasoning ? "Hide" : "Show" }} chain-of-thought ({{ reasoningEntries.length }})
          </button>
          <div v-if="showReasoning" class="mt-2 max-h-80 overflow-y-auto flex flex-col gap-2">
            <details v-for="(entry, i) in reasoningEntries" :key="i" class="rounded-lg border border-warm-200 dark:border-warm-700 p-2">
              <summary class="text-xs cursor-pointer select-none">[msg {{ entry.messageIndex }}] {{ entry.label }}</summary>
              <pre class="mt-2 text-xs whitespace-pre-wrap font-mono">{{ entry.text }}</pre>
            </details>
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
import { useDensity } from "@/composables/useDensity"
import { useChatStore, _convertHistory, _replayEvents } from "@/stores/chat"
import { useSessionDetailStore } from "@/stores/sessionDetail"
import { sessionAPI } from "@/utils/api"
import { extractReasoning } from "@/utils/chatReasoning"

const props = defineProps({
  embedded: { type: Boolean, default: false },
})

// `goBack()` routes to either the v1 mobile sessions URL (kept for the
// legacy /sessions/:name page) or the desktop equivalent. Density is
// the right signal: compact users land on /mobile/sessions when v1 is
// still active; once v1 retires the route guard rewrites it anyway.
const { isCompact } = useDensity()
const isMobile = isCompact
const route = useRoute()
const router = useRouter()
const detail = useSessionDetailStore()

// In v1 the URL is ``/sessions/:name`` and ``route.params.name`` is the
// canonical source. In v2 the macro shell URL is ``/?tabs=session:foo``
// — ``route.params.name`` is empty, but the parent ``pages/sessions/
// [name].vue`` has already loaded the session into the ``sessionDetail``
// store via its ``sessionNameProp``. Reading from the store first keeps
// both paths working without forwarding props through ConvTab.
const sessionName = computed(() => detail.name || String(route.params.name || ""))

// ChatPanel scopes its store by ``props.instance.id`` (see
// ``useChatStore`` factory). The viewer renders ``<ChatPanel
// :instance="viewerInstance">`` with ``id = "session:<name>"``, so the
// viewer's own writes (``loadTarget`` → ``messagesByTab[tab]``) MUST go
// into that SAME scope or ChatPanel's reads come up empty and the
// transcript renders blank. Previously this called ``useChatStore()``
// with no argument, which inside ``<script setup>`` resolves through
// ``injectScope()`` — in the v1 ``/sessions/:name`` route there's no
// AttachTab ancestor providing a scope, so it fell back to the
// ``"default"`` singleton; ChatPanel meanwhile created a fresh
// ``"session:<name>"``-scoped store and read its empty
// ``messagesByTab``. Result: the saved session viewer showed nothing.
const _chatStoreRef = shallowRef(null)
const chat = new Proxy(
  {},
  {
    get(_target, key) {
      const inner = _chatStoreRef.value
      if (!inner) return undefined
      return inner[key]
    },
    set(_target, key, value) {
      const inner = _chatStoreRef.value
      if (!inner) return false
      inner[key] = value
      return true
    },
  },
)
watch(
  sessionName,
  (name) => {
    if (!name) return
    _chatStoreRef.value = useChatStore(`session:${name}`)
  },
  { immediate: true },
)
const loading = ref(false)
const error = ref("")
const viewerMeta = ref(null)
const historyTargets = ref([])
const reasoningEntries = ref([])
const showReasoning = ref(false)

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
  router.push(isMobile.value ? "/mobile/sessions" : "/sessions")
}

function resetViewer() {
  reasoningEntries.value = []
  showReasoning.value = false
  chat._cleanup()
  chat._instanceId = `session:${sessionName.value}`
  chat._instanceType = viewerInstance.value.type
  chat.tabs = []
  chat.messagesByTab = {}
  chat.tokenUsage = {}
  chat.runningJobs = {}
  chat.unreadCounts = {}
  chat.queuedMessagesByTab = {}
  chat.processingByTab = {}
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
  reasoningEntries.value = []
  const data = await sessionAPI.getHistory(sessionName.value, tab)
  for (const [messageIndex, message] of (data.messages || []).entries()) {
    // New sessions render segments inline through ChatMessage; the
    // fallback panel only serves old snapshots without ordered segments.
    if (Array.isArray(message?._kt_assistant_segments)) continue
    for (const entry of extractReasoning(message)) {
      reasoningEntries.value.push({ messageIndex, ...entry })
    }
  }
  if (data.events?.length) {
    // Read-only saved history: never populate ``runningJobs`` — a frozen
    // session has no live work, and its unfinished jobs already replay as
    // ``interrupted`` via the backend's synthetic terminals (UXI-04).
    const { messages } = _replayEvents(data.messages || [], data.events)
    chat.messagesByTab[tab] = messages
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
  sessionName,
  (name) => {
    if (!name) return
    chat.activeTab = null
    loadSession()
  },
  { immediate: true },
)

// Live-session refresh signal (UXI-01): repull the active target's
// history so new conversation appears without a full reset. Only bumps
// for a live inspector; a saved session never fires this.
watch(
  () => detail.reloadKey,
  () => {
    if (chat.activeTab) loadTarget(chat.activeTab).catch(() => {})
  },
)

// The viewer borrows the live chat store as its render surface (it
// writes saved-session events into ``messagesByTab`` / ``tabs`` and
// flips ``_instanceId`` to ``session:<name>``). Wipe everything on
// unmount so navigating to a running instance afterwards doesn't show
// the previous session's frozen history while ``initForInstance`` is
// still awaiting its ``fetchOne`` round-trip.
onUnmounted(() => {
  chat.resetForRouteSwitch()
})
</script>
