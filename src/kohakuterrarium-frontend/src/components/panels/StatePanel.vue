<template>
  <div class="h-full flex bg-warm-50 dark:bg-warm-900 overflow-hidden">
    <!-- Vertical tab rail on the left -->
    <div
      class="flex flex-col gap-1 py-2 px-1 border-r border-warm-200 dark:border-warm-700 shrink-0"
    >
      <button
        v-for="t in tabs"
        :key="t.id"
        class="w-8 h-8 flex items-center justify-center rounded text-warm-400 hover:text-warm-600 dark:hover:text-warm-300 transition-colors"
        :class="activeTab === t.id ? 'bg-iolite/10 text-iolite' : ''"
        :title="t.label"
        @click="activeTab = t.id"
      >
        <div :class="t.icon" class="text-sm" />
      </button>
    </div>

    <!-- Tab body -->
    <div class="flex-1 min-w-0 flex flex-col overflow-hidden">
      <div
        class="flex items-center gap-2 px-3 py-2 border-b border-warm-200 dark:border-warm-700 shrink-0"
      >
        <span class="text-xs font-medium text-warm-500 dark:text-warm-400 flex-1">
          {{ activeLabel }}
        </span>
        <button
          v-if="activeTab === 'scratchpad'"
          class="w-6 h-6 flex items-center justify-center rounded text-warm-400 hover:text-warm-600 dark:hover:text-warm-300 transition-colors"
          title="Refresh"
          @click="refreshScratchpad"
        >
          <div class="i-carbon-renew text-sm" />
        </button>
      </div>

      <div class="flex-1 overflow-y-auto px-3 py-2 text-xs">
        <!-- Scratchpad tab -->
        <template v-if="activeTab === 'scratchpad'">
          <div v-if="loading && !entries.length" class="text-warm-400 py-6 text-center">
            Loading...
          </div>
          <div v-else-if="errorMsg" class="text-coral py-4 text-[11px]">
            {{ errorMsg }}
          </div>
          <div v-else-if="entries.length === 0" class="text-warm-400 py-6 text-center">
            Scratchpad is empty
          </div>
          <div v-else class="flex flex-col gap-2">
            <div
              v-for="[key, value] in entries"
              :key="key"
              class="flex flex-col gap-0.5 rounded border border-warm-200 dark:border-warm-700 px-2 py-1.5"
            >
              <div class="flex items-center gap-2">
                <span class="text-iolite font-mono text-[10px]">{{ key }}</span>
                <span class="flex-1" />
                <button
                  class="text-warm-400 hover:text-coral transition-colors"
                  title="Delete"
                  @click="deleteKey(key)"
                >
                  <div class="i-carbon-close text-[10px]" />
                </button>
              </div>
              <div class="text-warm-600 dark:text-warm-400 font-mono text-[11px] break-all">
                {{ value }}
              </div>
            </div>
          </div>
        </template>

        <!-- Memory tab -->
        <template v-else-if="activeTab === 'memory'">
          <div class="flex flex-col gap-2">
            <el-input
              v-model="memQuery"
              placeholder="Search session memory..."
              size="small"
              clearable
              @keyup.enter="runMemorySearch"
            >
              <template #append>
                <el-button @click="runMemorySearch">
                  <div class="i-carbon-search text-[11px]" />
                </el-button>
              </template>
            </el-input>
            <div class="flex items-center gap-1">
              <button
                v-for="m in ['auto', 'fts', 'semantic', 'hybrid']"
                :key="m"
                class="px-2 py-0.5 rounded text-[10px] transition-colors"
                :class="
                  memMode === m ? 'bg-iolite/10 text-iolite' : 'text-warm-400 hover:text-warm-600'
                "
                @click="
                  memMode = m
                  if (memSearched) runMemorySearch()
                "
              >
                {{ m }}
              </button>
            </div>
            <div v-if="memLoading" class="text-warm-400 text-center py-4 text-[11px]">
              Searching...
            </div>
            <div v-else-if="memError" class="text-coral text-[11px] py-2">
              {{ memError }}
            </div>
            <div
              v-else-if="memSearched && memResults.length === 0"
              class="text-warm-400 text-center py-4 text-[11px]"
            >
              No results for "{{ memQuery }}"
            </div>
            <div v-else-if="!memSearched" class="text-warm-400 text-center py-4 text-[11px]">
              <p>Type a query and press Enter to search.</p>
              <p class="mt-1 text-[9px] opacity-70">
                Memory search works on indexed sessions. Running sessions may not have indexed
                events yet.
              </p>
            </div>
            <div v-else class="flex flex-col gap-1.5">
              <div
                v-for="(r, i) in memResults"
                :key="i"
                class="flex flex-col gap-0.5 rounded border border-warm-200 dark:border-warm-700 px-2 py-1.5"
              >
                <div class="flex items-center gap-2 text-[9px] text-warm-400 font-mono">
                  <span>{{ r.agent || "agent" }}</span>
                  <span>·</span>
                  <span>{{ r.block_type }}</span>
                  <span>·</span>
                  <span>r{{ r.round }}b{{ r.block }}</span>
                  <span class="flex-1" />
                  <span>score {{ r.score?.toFixed ? r.score.toFixed(2) : r.score }}</span>
                </div>
                <div class="text-[11px] text-warm-700 dark:text-warm-300 break-words line-clamp-3">
                  {{ r.content }}
                </div>
              </div>
            </div>
          </div>
        </template>

        <!-- Tool History tab — shows tool calls from chat store -->
        <template v-else-if="activeTab === 'tools'">
          <div v-if="toolCalls.length === 0" class="text-warm-400 py-6 text-center text-[11px]">
            No tool calls in this session yet.
          </div>
          <div v-else class="flex flex-col gap-1">
            <div
              v-for="(tc, i) in toolCalls"
              :key="i"
              class="flex items-center gap-2 px-2 py-1 rounded text-[11px] hover:bg-warm-100 dark:hover:bg-warm-800"
            >
              <span
                class="w-1.5 h-1.5 rounded-full shrink-0"
                :class="
                  tc.status === 'done'
                    ? 'bg-aquamarine'
                    : tc.status === 'error'
                      ? 'bg-coral'
                      : 'bg-amber kohaku-pulse'
                "
              />
              <span class="font-mono text-iolite truncate">{{ tc.name }}</span>
              <span class="flex-1" />
              <span class="text-warm-400 text-[9px] font-mono">{{ tc.status }}</span>
            </div>
          </div>
        </template>

        <!-- Compaction tab — reads chat store's compact messages -->
        <template v-else-if="activeTab === 'compact'">
          <div v-if="compactions.length === 0" class="text-warm-400 py-6 text-center text-[11px]">
            No compactions in this session yet.
          </div>
          <div v-else class="flex flex-col gap-2">
            <div
              v-for="c in compactions"
              :key="c.id"
              class="rounded border border-warm-200 dark:border-warm-700 px-2 py-1.5 text-[11px]"
            >
              <div class="flex items-center gap-2 text-[9px] text-warm-400 font-mono">
                <span>round {{ c.round }}</span>
                <span>·</span>
                <span>{{ c.messagesCompacted }} messages</span>
                <span class="flex-1" />
                <span
                  class="px-1 rounded"
                  :class="
                    c.status === 'done'
                      ? 'bg-aquamarine/10 text-aquamarine'
                      : 'bg-amber/10 text-amber'
                  "
                  >{{ c.status }}</span
                >
              </div>
              <div
                v-if="c.summary"
                class="mt-1 text-warm-600 dark:text-warm-400 break-words line-clamp-4"
              >
                {{ c.summary }}
              </div>
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from "vue"

import { useChatStore } from "@/stores/chat"
import { useScratchpadStore } from "@/stores/scratchpad"
import { sessionAPI } from "@/utils/api"

const props = defineProps({
  instance: { type: Object, default: null },
})

const scratchpad = useScratchpadStore()
const chat = useChatStore()

const tabs = [
  { id: "scratchpad", label: "Scratchpad", icon: "i-carbon-notebook" },
  { id: "tools", label: "Tool History", icon: "i-carbon-tools" },
  { id: "memory", label: "Memory", icon: "i-carbon-data-base" },
  { id: "compact", label: "Compaction", icon: "i-carbon-compare" },
]
const activeTab = ref("scratchpad")

const activeLabel = computed(() => tabs.find((t) => t.id === activeTab.value)?.label || "")

const agentId = computed(() => props.instance?.id || null)

// ── Scratchpad ────────────────────────────────────────────────
const entries = computed(() => {
  const id = agentId.value
  if (!id) return []
  // Filter out the reserved _plan key — it lives in the Plan tab.
  return Object.entries(scratchpad.getFor(id)).filter(([k]) => k !== "_plan")
})

const loading = computed(() => {
  const id = agentId.value
  return id ? !!scratchpad.loading[id] : false
})

const errorMsg = computed(() => {
  const id = agentId.value
  return id ? scratchpad.error[id] || "" : ""
})

function refreshScratchpad() {
  if (agentId.value) scratchpad.fetch(agentId.value)
}

async function deleteKey(key) {
  if (!agentId.value) return
  await scratchpad.patch(agentId.value, { [key]: null })
}

// ── Tool History ──────────────────────────────────────────────
// Tool calls live in msg.parts (type: "tool") for streaming messages,
// or msg.tool_calls for history-replayed messages. Check both.
const toolCalls = computed(() => {
  const tab = chat.activeTab
  if (!tab) return []
  const msgs = chat.messagesByTab?.[tab] || []
  const out = []
  for (const m of msgs) {
    // Streaming format: parts array with type="tool"
    if (m.parts) {
      for (const p of m.parts) {
        if (p.type === "tool" && p.name) out.push(p)
      }
    }
    // History format: tool_calls array
    if (m.tool_calls) {
      for (const tc of m.tool_calls) {
        if (tc?.name) out.push(tc)
      }
    }
  }
  // newest first
  return out.reverse()
})

// ── Memory search ─────────────────────────────────────────────
const memQuery = ref("")
const memMode = ref("auto")
const memResults = ref([])
const memLoading = ref(false)
const memError = ref("")
const memSearched = ref(false)

async function runMemorySearch() {
  const q = memQuery.value.trim()
  if (!q) {
    memResults.value = []
    return
  }
  const name = chat.sessionInfo.sessionId || props.instance?.session_id || props.instance?.id
  if (!name) {
    memError.value = "No session id available"
    return
  }
  memLoading.value = true
  memError.value = ""
  memSearched.value = true
  try {
    const data = await sessionAPI.searchMemory(name, {
      q,
      mode: memMode.value,
      k: 20,
    })
    memResults.value = data.results || []
  } catch (err) {
    memError.value = err?.response?.data?.detail || err?.message || String(err)
    memResults.value = []
  } finally {
    memLoading.value = false
  }
}

// ── Compaction ────────────────────────────────────────────────
const compactions = computed(() => {
  const tab = chat.activeTab
  if (!tab) return []
  const msgs = chat.messagesByTab?.[tab] || []
  return msgs.filter((m) => m.role === "compact")
})

// Fetch on mount and when agentId changes.
watch(
  agentId,
  (id) => {
    if (id) scratchpad.fetch(id)
  },
  { immediate: true },
)

// Auto-refetch scratchpad when processing stops (tool calls that
// modify scratchpad happen during processing). Also refetch when
// runningJobs count changes (tool completions).
watch(
  () => [chat.processing, Object.keys(chat.runningJobs).length],
  ([processing, _jobCount], [prevProcessing]) => {
    // Refetch when processing ends (agent finished a turn) or
    // when a job completes (job count decreased).
    if ((!processing && prevProcessing) || agentId.value) {
      scratchpad.fetch(agentId.value)
    }
  },
)
// Also refetch on new messages arriving.
watch(
  () => {
    const tab = chat.activeTab
    if (!tab) return 0
    return chat.messagesByTab?.[tab]?.length || 0
  },
  () => {
    if (agentId.value) scratchpad.fetch(agentId.value)
  },
)

onMounted(() => {
  if (agentId.value) scratchpad.fetch(agentId.value)
})
</script>
