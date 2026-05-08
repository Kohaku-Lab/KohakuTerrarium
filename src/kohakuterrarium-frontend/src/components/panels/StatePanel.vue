<template>
  <div class="h-full flex bg-warm-50 dark:bg-warm-900 overflow-hidden">
    <!-- Vertical tab rail on the left -->
    <div class="flex flex-col gap-1 py-2 px-1 border-r border-warm-200 dark:border-warm-700 shrink-0">
      <button v-for="tab in tabs" :key="tab.id" class="w-8 h-8 flex items-center justify-center rounded text-warm-400 hover:text-warm-600 dark:hover:text-warm-300 transition-colors" :class="activeTab === tab.id ? 'bg-iolite/10 text-iolite' : ''" :title="tab.label" @click="activeTab = tab.id">
        <div :class="tab.icon" class="text-sm" />
      </button>
    </div>

    <!-- Tab body -->
    <div class="flex-1 min-w-0 flex flex-col overflow-hidden">
      <div class="flex items-center gap-2 px-3 py-2 border-b border-warm-200 dark:border-warm-700 shrink-0">
        <span class="text-xs font-medium text-warm-500 dark:text-warm-400 flex-1">
          {{ activeLabel }}
        </span>
        <button v-if="activeTab === 'scratchpad'" class="w-6 h-6 flex items-center justify-center rounded text-warm-400 hover:text-warm-600 dark:hover:text-warm-300 transition-colors" :title="t('common.refresh')" @click="refreshScratchpad">
          <div class="i-carbon-renew text-sm" />
        </button>
      </div>

      <div class="flex-1 overflow-y-auto px-3 py-2 text-xs">
        <!-- Scratchpad tab -->
        <template v-if="activeTab === 'scratchpad'">
          <div v-if="loading && !entries.length" class="text-warm-400 py-6 text-center">{{ t("state.loading") }}</div>
          <div v-else-if="errorMsg" class="text-coral py-4 text-[11px]">
            {{ errorMsg }}
          </div>
          <div v-else-if="entries.length === 0" class="text-warm-400 py-6 text-center">{{ t("state.scratchpadEmpty") }}</div>
          <div v-else class="flex flex-col gap-2">
            <div v-for="[key, value] in entries" :key="key" class="flex flex-col gap-0.5 rounded border border-warm-200 dark:border-warm-700 px-2 py-1.5">
              <div class="flex items-center gap-2">
                <span class="text-iolite font-mono text-[10px]">{{ key }}</span>
                <span class="flex-1" />
                <button class="text-warm-400 hover:text-coral transition-colors" :title="t('state.deleteEntry')" @click="deleteKey(key)">
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
            <el-input v-model="memQuery" :placeholder="t('state.searchMemory')" size="small" clearable @keyup.enter="runMemorySearch">
              <template #append>
                <el-button @click="runMemorySearch">
                  <div class="i-carbon-search text-[11px]" />
                </el-button>
              </template>
            </el-input>
            <div class="flex items-center gap-1">
              <button v-for="m in ['auto', 'fts', 'semantic', 'hybrid']" :key="m" class="px-2 py-0.5 rounded text-[10px] transition-colors" :class="memMode === m ? 'bg-iolite/10 text-iolite' : 'text-warm-400 hover:text-warm-600'" @click="setMemMode(m)">
                {{ m }}
              </button>
            </div>
            <div v-if="memLoading" class="text-warm-400 text-center py-4 text-[11px]">{{ t("state.searching") }}</div>
            <div v-else-if="memError" class="text-coral text-[11px] py-2">
              {{ memError }}
            </div>
            <div v-else-if="memSearched && memResults.length === 0" class="text-warm-400 text-center py-4 text-[11px]">{{ t("state.noMemoryResults", { query: memQuery }) }}</div>
            <div v-else-if="!memSearched" class="text-warm-400 text-center py-4 text-[11px]">
              <p>{{ t("state.memoryPrompt") }}</p>
              <p class="mt-1 text-[9px] opacity-70">{{ t("state.memoryHint") }}</p>
            </div>
            <div v-else class="flex flex-col gap-1.5">
              <div v-for="(r, i) in memResults" :key="i" class="flex flex-col gap-0.5 rounded border border-warm-200 dark:border-warm-700 px-2 py-1.5">
                <div class="flex items-center gap-2 text-[9px] text-warm-400 font-mono">
                  <span>{{ r.agent || t("state.agentFallback") }}</span>
                  <span>·</span>
                  <span>{{ r.block_type }}</span>
                  <span>·</span>
                  <span>r{{ r.round }}b{{ r.block }}</span>
                  <span class="flex-1" />
                  <span>{{ t("state.score") }} {{ r.score?.toFixed ? r.score.toFixed(2) : r.score }}</span>
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
          <div v-if="toolCalls.length === 0" class="text-warm-400 py-6 text-center text-[11px]">{{ t("state.noToolCalls") }}</div>
          <div v-else class="flex flex-col gap-1">
            <div v-for="(tc, i) in toolCalls" :key="i" class="flex items-center gap-2 px-2 py-1 rounded text-[11px] hover:bg-warm-100 dark:hover:bg-warm-800">
              <span class="w-1.5 h-1.5 rounded-full shrink-0" :class="tc.status === 'done' ? 'bg-aquamarine' : tc.status === 'error' ? 'bg-coral' : 'bg-amber kohaku-pulse'" />
              <span class="font-mono text-iolite truncate">{{ tc.name }}</span>
              <span class="flex-1" />
              <span class="text-warm-400 text-[9px] font-mono">{{ statusLabel(tc.status, tc.status) }}</span>
            </div>
          </div>
        </template>

        <!-- Compaction tab — reads chat store's compact messages -->
        <template v-else-if="activeTab === 'compact'">
          <div v-if="compactions.length === 0" class="text-warm-400 py-6 text-center text-[11px]">{{ t("state.noCompactions") }}</div>
          <div v-else class="flex flex-col gap-2">
            <div v-for="c in compactions" :key="c.id" class="rounded border border-warm-200 dark:border-warm-700 px-2 py-1.5 text-[11px]">
              <div class="flex items-center gap-2 text-[9px] text-warm-400 font-mono">
                <span>{{ t("state.roundMessages", { round: c.round, count: c.messagesCompacted }) }}</span>
                <span class="flex-1" />
                <span class="px-1 rounded" :class="c.status === 'done' ? 'bg-aquamarine/10 text-aquamarine' : 'bg-amber/10 text-amber'">
                  {{ statusLabel(c.status, c.status) }}
                </span>
              </div>
              <div v-if="c.summary" class="mt-1 text-warm-600 dark:text-warm-400 break-words line-clamp-4">
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
import { useI18n } from "@/utils/i18n"
import { sessionAPI } from "@/utils/api"

const props = defineProps({
  instance: { type: Object, default: null },
})

const scratchpad = useScratchpadStore()
const chat = useChatStore()
const { t, statusLabel } = useI18n()

const tabs = computed(() => [
  { id: "scratchpad", label: t("state.tab.scratchpad"), icon: "i-carbon-notebook" },
  { id: "tools", label: t("state.tab.tools"), icon: "i-carbon-tools" },
  { id: "memory", label: t("state.tab.memory"), icon: "i-carbon-data-base" },
  { id: "compact", label: t("state.tab.compact"), icon: "i-carbon-compare" },
])
const activeTab = ref("scratchpad")

const activeLabel = computed(() => tabs.value.find((t) => t.id === activeTab.value)?.label || "")

// Unified routing — ``instance.id`` is always the canonical
// session_id (graph_id). The scratchpad / triggers / env / prompt
// endpoints all live under ``/sessions/{sid}/creatures/{target}/...``
// and accept a creature name or id as ``target``. Solo sessions just
// have one creature in the roster; the same routing works.
const instanceId = computed(() => props.instance?.id || null)
const routingId = instanceId
const terrariumTarget = computed(() => {
  const creatures = props.instance?.creatures || []
  if (creatures.length === 0) return null
  // Multi-creature: per-creature panels are scoped to the active tab.
  // Solo: there's only one creature, default to its name so the
  // scratchpad panel opens automatically without forcing a tab click.
  if (creatures.length > 1) return chat.terrariumTarget
  return chat.terrariumTarget || creatures[0].name
})
const scratchpadTarget = computed(() => terrariumTarget.value)
const scratchpadKey = computed(() => {
  const id = routingId.value
  if (!id) return null
  return scratchpadTarget.value ? `${id}:${scratchpadTarget.value}` : id
})
// Scratchpad is inspectable whenever we have a session and a target
// creature to scope to. ``terrariumTarget`` already resolves the
// active creature for solo sessions (defaults to the only creature
// in the roster) so there's no need to fork on instance.type here.
const canInspectScratchpad = computed(() => !!routingId.value && !!scratchpadTarget.value)

// ── Scratchpad ────────────────────────────────────────────────
const entries = computed(() => {
  const id = routingId.value
  if (!id || !canInspectScratchpad.value) return []
  return Object.entries(scratchpad.getFor(id, scratchpadTarget.value)).filter(([k]) => k !== "_plan" && !/^__.*__$/.test(k))
})

const loading = computed(() => {
  const key = scratchpadKey.value
  return key ? !!scratchpad.loading[key] : false
})

const errorMsg = computed(() => {
  if (!scratchpadTarget.value && (props.instance?.creatures?.length || 0) > 1) {
    return t("state.scratchpadUnavailable")
  }
  const key = scratchpadKey.value
  return key ? scratchpad.error[key] || "" : ""
})

function refreshScratchpad() {
  if (routingId.value && canInspectScratchpad.value) scratchpad.fetch(routingId.value, scratchpadTarget.value)
}

async function deleteKey(key) {
  if (!routingId.value || !canInspectScratchpad.value) return
  await scratchpad.patch(routingId.value, { [key]: null }, scratchpadTarget.value)
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

function setMemMode(m) {
  memMode.value = m
  if (memSearched.value) runMemorySearch()
}

async function runMemorySearch() {
  const q = memQuery.value.trim()
  if (!q) {
    memResults.value = []
    return
  }
  const name = chat.sessionInfo.sessionId || props.instance?.session_id || props.instance?.id
  if (!name) {
    memError.value = t("state.noSessionId")
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

// Fetch on mount and when routingId changes.
watch(
  [routingId, scratchpadTarget],
  ([id]) => {
    if (id && canInspectScratchpad.value) scratchpad.fetch(id, scratchpadTarget.value)
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
    if ((!processing && prevProcessing) || routingId.value) {
      refreshScratchpad()
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
    refreshScratchpad()
  },
)

onMounted(() => {
  refreshScratchpad()
})
</script>
