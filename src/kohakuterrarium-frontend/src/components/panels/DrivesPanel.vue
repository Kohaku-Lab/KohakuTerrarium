<template>
  <div class="h-full flex flex-col bg-warm-50 dark:bg-warm-900 overflow-hidden">
    <!-- Header: counts + create + filters -->
    <div class="px-3 py-2 border-b border-warm-200 dark:border-warm-700 shrink-0 flex flex-col gap-2">
      <div class="flex items-center gap-2 flex-wrap">
        <span class="i-carbon-flow text-sm text-warm-500" />
        <span class="text-xs font-medium text-warm-500 dark:text-warm-400">Drives</span>
        <DriveCountBadges :counts="store.counts" />
        <span class="flex-1" />
        <el-button size="small" type="primary" plain :disabled="!sessionId" @click="openCreate"> <span class="i-carbon-add mr-1" /> Create </el-button>
      </div>
      <div class="flex items-center gap-2 flex-wrap">
        <el-select :model-value="store.filters.status" multiple collapse-tags size="small" placeholder="Status" class="!w-36" @update:model-value="store.setFilter('status', $event)">
          <el-option v-for="s in STATUS_OPTIONS" :key="s" :value="s" :label="s" />
        </el-select>
        <el-select v-if="store.kinds.length" :model-value="store.filters.kind" multiple collapse-tags size="small" placeholder="Kind" class="!w-32" @update:model-value="store.setFilter('kind', $event)">
          <el-option v-for="k in store.kinds" :key="k" :value="k" :label="k" />
        </el-select>
        <el-input :model-value="store.filters.text" size="small" placeholder="Filter…" clearable class="flex-1 !min-w-[8rem]" @update:model-value="store.setFilter('text', $event)" />
      </div>
    </div>

    <!-- Body: list | detail -->
    <div class="flex-1 min-h-0 flex">
      <div class="drive-list-pane" :class="{ 'w-full': !store.selected }">
        <div v-if="store.loading" class="text-warm-400 text-xs py-6 text-center">Loading…</div>
        <div v-else-if="store.error" class="text-coral text-xs py-6 text-center px-3">{{ store.error }}</div>
        <div v-else-if="store.list.length === 0" class="text-warm-400 text-xs py-8 text-center px-3">No Drives{{ hasFilters ? " match the filters" : " yet" }}.</div>
        <div v-else class="flex flex-col">
          <template v-for="group in groupedList" :key="group.key">
            <div v-if="showGroupHeaders" class="drive-group-header" :data-testid="`drive-group-${group.key}`">
              <span :class="group.key === UNASSIGNED ? 'i-carbon-user-x' : 'i-carbon-user-avatar'" class="text-[11px]" />
              <span class="truncate">{{ group.label }}</span>
              <span class="text-warm-400 ml-auto">{{ group.rows.length }}</span>
            </div>
            <DriveSummaryRow v-for="r in group.rows" :key="r.drive_id" :record="r" :selected="store.selectedId === r.drive_id" :flags="store.deliveryFlags[r.drive_id]" @select="store.select" />
          </template>
        </div>
      </div>
      <div v-if="store.selected" class="drive-detail-pane">
        <DriveDetail :record="store.selected" :detail="store.detail" :deliveries="store.deliveries[store.selectedId] || []" :flags="store.deliveryFlags[store.selectedId]" :allowed-actions="store.selected.allowed_actions || []" :replaying="replaying" :pending-proposal="store.pendingProposals[store.selectedId] || null" closable @close="store.selectedId = null" @transition="onTransition" @wake="onWake" @assign="onAssign" @unassign="onUnassign" @transfer-owner="onTransferOwner" @report-progress="onProgress" @propose-terminal="onProposeTerminal" @verify-terminal="onVerifyTerminal" @edit="openEdit" @replay="onReplay" />
      </div>
    </div>

    <DriveEditor v-model="editorOpen" :mode="editorMode" :record="editorMode === 'edit' ? store.selected : null" :kinds="createKinds" :creatures="creatures" :conflict="store.conflict" :saving="saving" @create="onCreate" @save="onSave" @load-server="store.dismissConflict" />
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue"
import { ElMessage, ElMessageBox } from "element-plus"

import DriveCountBadges from "@/components/drives/DriveCountBadges.vue"
import DriveDetail from "@/components/drives/DriveDetail.vue"
import DriveEditor from "@/components/drives/DriveEditor.vue"
import DriveSummaryRow from "@/components/drives/DriveSummaryRow.vue"
import { createVisibilityInterval } from "@/composables/useVisibilityInterval"
import { useDrivesStore } from "@/stores/drives"
import { driveSettingsAPI } from "@/utils/driveSettingsApi"
import { LAYOUT_EVENTS, onLayoutEvent } from "@/utils/layoutEvents"
import { wsUrl } from "@/utils/wsUrl"

const props = defineProps({
  instance: { type: Object, default: null },
})

const STATUS_OPTIONS = ["draft", "active", "waiting", "blocked", "paused", "completed", "failed", "cancelled", "retired"]

const sessionId = computed(() => props.instance?.graph_id || props.instance?.id || "")
const store = useDrivesStore(sessionId.value || undefined)

const editorOpen = ref(false)
const editorMode = ref("create")
const saving = ref(false)
const replaying = ref(null)
// Empty until the node's enabled registrations load — never a silent "generic"
// fallback, so an unavailable-runtime create is surfaced, not attempted (R1-38).
const createKinds = ref([])
// Session members for the creature-scope picker (R1-38).
const creatures = computed(() => props.instance?.creatures || [])

// Rows grouped by assignee so a graph reads per creature; members keep the
// session's order and unassigned records sit last.
const UNASSIGNED = "__unassigned"
const groupedList = computed(() => {
  const names = new Map(creatures.value.map((c) => [c.creature_id, c.name || c.creature_id]))
  const rank = new Map([...names.keys()].map((id, i) => [id, i]))
  const groups = new Map()
  for (const r of store.list) {
    const key = r.assignee_creature_id || UNASSIGNED
    if (!groups.has(key)) {
      groups.set(key, { key, label: key === UNASSIGNED ? "Unassigned" : names.get(key) || key, rows: [] })
    }
    groups.get(key).rows.push(r)
  }
  const order = (g) => (g.key === UNASSIGNED ? Number.MAX_SAFE_INTEGER : (rank.get(g.key) ?? names.size))
  return [...groups.values()].sort((a, b) => order(a) - order(b))
})
const showGroupHeaders = computed(() => groupedList.value.length > 1 || creatures.value.length > 1)

const hasFilters = computed(() => {
  const f = store.filters
  return f.status.length || f.kind.length || f.text || f.owner || f.assignee || f.scope
})

let poller = null
let ws = null
let wsClosedByUs = false
let reconnectTimer = null
let kindsGen = 0
let unsubDeepLink = () => {}

onMounted(() => {
  if (sessionId.value) start()
  // Deep-link from the header badge / chat / graph: focus the record when
  // the event targets this session. Never opens the panel itself.
  unsubDeepLink = onLayoutEvent(LAYOUT_EVENTS.OPEN_DRIVES, (evt) => {
    const detail = evt?.detail || {}
    if (detail.sessionId && detail.sessionId !== sessionId.value) return
    // Claiming the event tells the header badge a panel is already showing.
    evt?.preventDefault?.()
    if (detail.driveId) store.select(detail.driveId)
  })
})

watch(sessionId, (id, prev) => {
  if (id === prev) return
  stopLive()
  if (id) start()
})

onBeforeUnmount(() => {
  stopLive()
  unsubDeepLink()
})

function start() {
  store.load(sessionId.value)
  loadKinds()
  poller = createVisibilityInterval(() => store.reconcile(), 6000)
  poller.start()
  startLive()
}

function stopLive() {
  if (poller) {
    poller.stop()
    poller = null
  }
  wsClosedByUs = true
  // Cancel any pending reconnect so its callback can't reopen the socket after
  // a session switch or unmount (R1-39).
  if (reconnectTimer) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
  if (ws) {
    ws.close()
    ws = null
  }
}

// Subscribe to the runtime-graph event stream; Drive EngineEvents are folded
// into the store as they arrive. The periodic reconcile is the reliable
// backstop when the socket is down or an event is missed.
function startLive() {
  if (typeof WebSocket === "undefined") return
  wsClosedByUs = false
  try {
    ws = new WebSocket(wsUrl("/ws/runtime/graph"))
    ws.onmessage = (event) => {
      let data
      try {
        data = JSON.parse(event.data)
      } catch {
        return
      }
      const kind = data.kind || data.type || ""
      if (String(kind).startsWith("drive")) store.applyEvent(data)
    }
    ws.onclose = () => {
      ws = null
      // Track the reconnect timer so stopLive() can cancel it, and recheck the
      // shutdown flag when it fires (the panel may have unmounted or switched
      // sessions in the interval) — R1-39.
      if (!wsClosedByUs) {
        reconnectTimer = setTimeout(() => {
          reconnectTimer = null
          if (!wsClosedByUs && sessionId.value) startLive()
        }, 2000)
      }
    }
    ws.onerror = () => {}
  } catch {
    /* live updates are best-effort; reconcile covers the gap */
  }
}

async function loadKinds() {
  const gen = ++kindsGen
  try {
    const rs = await driveSettingsAPI.runtimeStatus(props.instance?.home_node || "_host")
    // A session/node switch superseded this fetch — drop its result (R1-39).
    if (gen !== kindsGen) return
    const kinds = (rs?.registrations || []).map((r) => r.kind).filter(Boolean)
    // No fallback to "generic": if the node reports no enabled registrations,
    // creating is genuinely unavailable and the editor surfaces that (R1-38).
    createKinds.value = [...new Set(kinds)]
  } catch {
    if (gen !== kindsGen) return
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
  saving.value = true
  try {
    // A graph-scoped Drive lives in this session's graph; a creature-scoped one
    // carries the picked creature id from the editor — never override it with
    // the graph id (R1-38).
    const scope_id = request.scope_type === "creature" ? request.scope_id : sessionId.value
    const res = await store.create({ ...request, scope_id })
    if (res.ok) {
      editorOpen.value = false
      ElMessage.success("Drive created.")
    } else {
      ElMessage.error(res.detail || "Create failed.")
    }
  } finally {
    saving.value = false
  }
}

async function onSave({ patch, expectedRevision }) {
  saving.value = true
  try {
    // Thread the editor's captured base revision through unchanged so a stale
    // draft conflicts instead of overwriting a newer record (R1-35).
    const res = await store.update(store.selectedId, patch, expectedRevision)
    if (res.ok) {
      editorOpen.value = false
      ElMessage.success("Saved.")
    } else if (res.conflict) {
      ElMessage.warning("This Drive changed — compare and retry.")
    } else {
      ElMessage.error(res.detail || "Save failed.")
    }
  } finally {
    saving.value = false
  }
}

async function onTransition(target) {
  await runAction(() => store.transition(store.selectedId, target), `Moved to ${target}.`)
}

async function onWake() {
  await runAction(() => store.wake(store.selectedId), "Woken.")
}

async function onAssign() {
  const id = store.selectedId
  try {
    const { value } = await ElMessageBox.prompt("Creature id to assign", "Assign Drive", { inputPlaceholder: "creature-id" })
    if (value) await runAction(() => store.assign(id, value), "Assigned.")
  } catch {
    /* cancelled */
  }
}

async function onUnassign() {
  await runAction(() => store.unassign(store.selectedId), "Unassigned.")
}

async function onTransferOwner() {
  const id = store.selectedId
  try {
    const { value } = await ElMessageBox.prompt("New owner (e.g. user:alice)", "Transfer owner", { inputPlaceholder: "kind:identity" })
    if (value) await runAction(() => store.setOwner(id, value), "Owner transferred.")
  } catch {
    /* cancelled */
  }
}

async function onProgress(summary) {
  await runAction(() => store.reportProgress(store.selectedId, summary), "Progress recorded.")
}

async function onProposeTerminal() {
  const res = await store.propose(store.selectedId, "completed")
  if (res.ok && res.pending) ElMessage.info("Completion proposed — awaiting verification.")
  else if (res.ok) ElMessage.success("Completed.")
  else if (res.conflict) ElMessage.warning("This Drive changed — reloaded the current version.")
  else ElMessage.error(res.detail || "Propose failed.")
}

async function onVerifyTerminal(approved) {
  if (approved) {
    await runAction(() => store.approve(store.selectedId), "Approved.")
  } else {
    // No dedicated reject route; block the Drive so it leaves the pending
    // terminal state and can be reworked.
    await runAction(() => store.transition(store.selectedId, "blocked", { reason: "verification rejected" }), "Rejected.")
  }
}

async function onReplay(deliveryId) {
  replaying.value = deliveryId
  try {
    const res = await store.replayDelivery(store.selectedId, deliveryId)
    if (res.ok) ElMessage.success("Delivery replayed.")
    else ElMessage.error(res.detail || "Replay failed.")
  } finally {
    replaying.value = null
  }
}

async function runAction(fn, okMsg) {
  const res = await fn()
  if (res.ok) ElMessage.success(okMsg)
  else if (res.conflict) ElMessage.warning("This Drive changed — reloaded the current version.")
  else ElMessage.error(res.detail || "Action failed.")
}

// Deep-link: a parent can request a specific Drive be opened.
defineExpose({
  openDrive(driveId) {
    if (driveId) store.select(driveId)
  },
})
</script>

<style scoped>
.drive-group-header {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  padding: 0.25rem 0.75rem;
  font-size: 11px;
  font-weight: 500;
  color: rgb(120, 109, 98);
  background: rgba(120, 109, 98, 0.08);
  border-bottom: 1px solid rgba(120, 109, 98, 0.12);
  position: sticky;
  top: 0;
}
.drive-list-pane {
  width: 20rem;
  flex-shrink: 0;
  overflow-y: auto;
  border-right: 1px solid rgba(120, 109, 98, 0.18);
}
.drive-detail-pane {
  flex: 1 1 0;
  min-width: 0;
}
@media (max-width: 640px) {
  .drive-list-pane {
    width: 100%;
    border-right: none;
  }
}
</style>
