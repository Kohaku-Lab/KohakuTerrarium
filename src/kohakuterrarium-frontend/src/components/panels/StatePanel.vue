<template>
  <div class="h-full flex bg-warm-50 dark:bg-warm-900 overflow-hidden">
    <!-- Vertical tab rail on the left -->
    <div class="flex flex-col gap-1 py-2 px-1 border-r border-warm-200 dark:border-warm-700 shrink-0">
      <button v-for="tab in tabs" :key="tab.id" class="relative w-8 h-8 flex items-center justify-center rounded text-warm-400 hover:text-warm-600 dark:hover:text-warm-300 transition-colors" :class="activeTab === tab.id ? 'bg-iolite/10 text-iolite' : ''" :title="tab.label" :data-testid="`state-tab-${tab.id}`" @click="activeTab = tab.id">
        <div :class="tab.icon" class="text-sm" />
        <span v-if="tab.id === 'drives' && liveDriveCount > 0" class="absolute -top-0.5 -right-0.5 min-w-3.5 h-3.5 px-0.5 rounded-full bg-iolite text-white text-[8px] font-bold flex items-center justify-center" data-testid="state-drive-count">{{ liveDriveCount > 9 ? "9+" : liveDriveCount }}</span>
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
        <!-- Drives tab — the active creature's commitments, with management -->
        <template v-if="activeTab === 'drives'">
          <div class="flex items-center gap-1 mb-2 text-[10px] flex-wrap">
            <button v-for="s in DRIVE_SCOPES" :key="s" class="px-2 py-0.5 rounded transition-colors" :class="driveScope === s ? 'bg-iolite/10 text-iolite' : 'text-warm-400 hover:text-warm-600'" :disabled="s === 'mine' && !activeCreatureId" :data-testid="`state-drive-scope-${s}`" @click="driveScope = s">
              {{ t(`state.drivesScope.${s}`) }}
            </button>
            <span class="flex-1" />
            <button class="px-2 py-0.5 rounded text-iolite hover:bg-iolite/10 transition-colors disabled:opacity-50 flex items-center gap-1" :disabled="!sessionId || !createKinds.length" :title="createKinds.length ? '' : t('state.drivesUnavailable')" data-testid="state-new-goal" @click="openCreate"><span class="i-carbon-add text-[11px]" />{{ t("state.newGoal") }}</button>
            <button class="px-2 py-0.5 rounded text-warm-500 hover:text-warm-700 dark:hover:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800 transition-colors flex items-center gap-1" :title="t('state.openDrivesPanel')" data-testid="state-open-drives" @click="openFullPanel"><span class="i-carbon-launch text-[11px]" />{{ t("state.openDrivesPanel") }}</button>
          </div>

          <div v-if="drives.loading && !driveRows.length" class="text-warm-400 py-6 text-center">{{ t("state.loading") }}</div>
          <div v-else-if="drives.error" class="text-coral py-4 text-[11px]">{{ drives.error }}</div>
          <div v-else-if="driveRows.length === 0" class="text-warm-400 py-6 text-center text-[11px]">
            <p>{{ effectiveScope === "mine" ? t("state.noDrives") : t("state.noDrivesInGraph") }}</p>
            <p v-if="runtimeEnabled === false" class="mt-1 text-[10px] opacity-80">{{ t("state.drivesUnavailable") }}</p>
          </div>
          <div v-else class="flex flex-col -mx-3">
            <DriveSummaryRow v-for="r in driveRows" :key="r.drive_id" :record="r" :selected="drives.selectedId === r.drive_id" :flags="drives.deliveryFlags[r.drive_id]" @select="drives.select" />
          </div>

          <!-- Selected record: the same detail + actions as the full panel,
               stacked below the list because the column is narrow. -->
          <div v-if="drives.selected" class="-mx-3 mt-2 border-t border-warm-200 dark:border-warm-700 h-96 flex flex-col" data-testid="state-drive-detail">
            <DriveDetail :record="drives.selected" :detail="drives.detail" :deliveries="drives.deliveries[drives.selectedId] || []" :flags="drives.deliveryFlags[drives.selectedId]" :allowed-actions="drives.selected.allowed_actions || []" :replaying="replaying" :pending-proposal="drives.pendingProposals[drives.selectedId] || null" closable @close="drives.selectedId = null" @transition="actions.onTransition" @wake="actions.onWake" @assign="actions.onAssign" @unassign="actions.onUnassign" @transfer-owner="actions.onTransferOwner" @report-progress="actions.onProgress" @propose-terminal="actions.onProposeTerminal" @verify-terminal="actions.onVerifyTerminal" @edit="openEdit" @replay="actions.onReplay" />
          </div>
        </template>

        <!-- Scratchpad tab -->
        <template v-else-if="activeTab === 'scratchpad'">
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

    <DriveEditor v-model="editorOpen" :mode="editorMode" :record="editorMode === 'edit' ? drives.selected : null" :kinds="createKinds" :creatures="creatures" :default-creature-id="activeCreatureId || ''" :conflict="drives.conflict" :saving="saving" @create="onCreate" @save="onSave" @load-server="drives.dismissConflict" />
  </div>
</template>

<script setup>
import { computed, onMounted, ref, watch } from "vue"

import DriveDetail from "@/components/drives/DriveDetail.vue"
import DriveEditor from "@/components/drives/DriveEditor.vue"
import DriveSummaryRow from "@/components/drives/DriveSummaryRow.vue"
import { useDriveActions } from "@/composables/useDriveActions"
import { useDrivesLive } from "@/composables/useDrivesLive"
import { useChatStore } from "@/stores/chat"
import { useDrivesStore } from "@/stores/drives"
import { useScratchpadStore } from "@/stores/scratchpad"
import { driveSettingsAPI } from "@/utils/driveSettingsApi"
import { useI18n } from "@/utils/i18n"
import { fireOpenDrives, fireOpenDrivesDrawer } from "@/utils/layoutEvents"
import { sessionAPI } from "@/utils/api"

const props = defineProps({
  instance: { type: Object, default: null },
})

const scratchpad = useScratchpadStore()
const chat = useChatStore()
const { t, statusLabel } = useI18n()

// Drives come first: the commitment a creature is pursuing is the state a
// user wants to see before its scratchpad.
const tabs = computed(() => [
  { id: "drives", label: t("state.tab.drives"), icon: "i-carbon-flow" },
  { id: "scratchpad", label: t("state.tab.scratchpad"), icon: "i-carbon-notebook" },
  { id: "memory", label: t("state.tab.memory"), icon: "i-carbon-data-base" },
  { id: "compact", label: t("state.tab.compact"), icon: "i-carbon-compare" },
])
const activeTab = ref("drives")

const activeLabel = computed(() => tabs.value.find((t) => t.id === activeTab.value)?.label || "")

// Unified routing — ``instance.id`` is always the canonical
// session_id (graph_id). The scratchpad / triggers / env / prompt
// endpoints all live under ``/sessions/{sid}/creatures/{target}/...``
// and accept a creature name or id as ``target``. Solo sessions just
// have one creature in the roster; the same routing works.
const instanceId = computed(() => props.instance?.id || null)
const routingId = instanceId
const creatures = computed(() => props.instance?.creatures || [])
const terrariumTarget = computed(() => {
  if (creatures.value.length === 0) return null
  // Multi-creature: per-creature panels are scoped to the active tab.
  // Solo: there's only one creature, default to its name so the
  // scratchpad panel opens automatically without forcing a tab click.
  if (creatures.value.length > 1) return chat.terrariumTarget
  return chat.terrariumTarget || creatures.value[0].name
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

// ── Drives ────────────────────────────────────────────────────
const sessionId = computed(() => props.instance?.graph_id || props.instance?.id || "")
const drives = useDrivesStore(sessionId.value || undefined)
const actions = useDriveActions(drives, { sessionId })
const { saving, replaying } = actions

const DRIVE_SCOPES = ["mine", "graph"]
const driveScope = ref("mine")
// Drives are keyed by creature id while the chat target is a name; a
// channel tab has no creature and falls back to the whole graph.
const activeCreatureId = computed(() => {
  const name = terrariumTarget.value
  const hit = creatures.value.find((c) => c.name === name)
  if (hit) return hit.creature_id || null
  if (creatures.value.length === 1) return creatures.value[0].creature_id || null
  return null
})
const effectiveScope = computed(() => (driveScope.value === "mine" && activeCreatureId.value ? "mine" : "graph"))
const driveRows = computed(() => {
  const rows = drives.list
  if (effectiveScope.value !== "mine") return rows
  return rows.filter((r) => r.assignee_creature_id === activeCreatureId.value)
})
const liveDriveCount = computed(() => driveRows.value.filter((r) => !["completed", "failed", "cancelled", "retired"].includes(r.status)).length)

const editorOpen = ref(false)
const editorMode = ref("create")
// Goal first so the create form opens on the human-facing kind (R1-38).
const createKinds = ref([])
const runtimeEnabled = ref(null)
let kindsGen = 0

useDrivesLive(sessionId, drives, { onStart: loadKinds })

async function loadKinds() {
  const gen = ++kindsGen
  try {
    const rs = await driveSettingsAPI.runtimeStatus(props.instance?.home_node || "_host")
    if (gen !== kindsGen) return
    runtimeEnabled.value = rs?.enabled !== false
    const kinds = [...new Set((rs?.registrations || []).map((r) => r.kind).filter(Boolean))]
    createKinds.value = kinds.sort((a, b) => (a === "goal" ? -1 : b === "goal" ? 1 : 0))
  } catch {
    if (gen !== kindsGen) return
    runtimeEnabled.value = false
    createKinds.value = []
  }
}

function openCreate() {
  editorMode.value = "create"
  editorOpen.value = true
}
function openEdit() {
  editorMode.value = "edit"
  editorOpen.value = true
}
async function onCreate(request) {
  if (await actions.onCreate(request)) editorOpen.value = false
}
async function onSave(payload) {
  if (await actions.onSave(payload)) editorOpen.value = false
}

function openFullPanel() {
  const detail = { sessionId: sessionId.value, driveId: drives.selectedId || undefined }
  // A mounted full panel focuses itself; otherwise the header badge hosts a
  // drawer. Nothing happens on a legacy layout with neither.
  if (fireOpenDrives(detail)) return
  fireOpenDrivesDrawer(detail)
}

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
