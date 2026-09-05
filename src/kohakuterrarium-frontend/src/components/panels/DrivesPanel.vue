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
        <DriveDetail :record="store.selected" :detail="store.detail" :deliveries="store.deliveries[store.selectedId] || []" :flags="store.deliveryFlags[store.selectedId]" :allowed-actions="store.selected.allowed_actions || []" :replaying="replaying" :pending-proposal="store.pendingProposals[store.selectedId] || null" closable @close="store.selectedId = null" @transition="actions.onTransition" @wake="actions.onWake" @assign="actions.onAssign" @unassign="actions.onUnassign" @transfer-owner="actions.onTransferOwner" @report-progress="actions.onProgress" @propose-terminal="actions.onProposeTerminal" @verify-terminal="actions.onVerifyTerminal" @edit="openEdit" @replay="actions.onReplay" />
      </div>
    </div>

    <DriveEditor v-model="editorOpen" :mode="editorMode" :record="editorMode === 'edit' ? store.selected : null" :kinds="createKinds" :creatures="creatures" :conflict="store.conflict" :saving="saving" @create="onCreate" @save="onSave" @load-server="store.dismissConflict" />
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue"

import DriveCountBadges from "@/components/drives/DriveCountBadges.vue"
import DriveDetail from "@/components/drives/DriveDetail.vue"
import DriveEditor from "@/components/drives/DriveEditor.vue"
import DriveSummaryRow from "@/components/drives/DriveSummaryRow.vue"
import { useDriveActions } from "@/composables/useDriveActions"
import { useDrivesLive } from "@/composables/useDrivesLive"
import { useDrivesStore } from "@/stores/drives"
import { driveSettingsAPI } from "@/utils/driveSettingsApi"
import { LAYOUT_EVENTS, onLayoutEvent } from "@/utils/layoutEvents"

const props = defineProps({
  instance: { type: Object, default: null },
})

const STATUS_OPTIONS = ["draft", "active", "waiting", "blocked", "paused", "completed", "failed", "cancelled", "retired"]

const sessionId = computed(() => props.instance?.graph_id || props.instance?.id || "")
const store = useDrivesStore(sessionId.value || undefined)

const editorOpen = ref(false)
const editorMode = ref("create")
// Empty until the node's enabled registrations load — never a silent "generic"
// fallback, so an unavailable-runtime create is surfaced, not attempted (R1-38).
const createKinds = ref([])
// Session members for the creature-scope picker (R1-38).
const creatures = computed(() => props.instance?.creatures || [])

const actions = useDriveActions(store, { sessionId })
const { saving, replaying } = actions

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

let kindsGen = 0
let unsubDeepLink = () => {}

useDrivesLive(sessionId, store, { onStart: loadKinds })

onMounted(() => {
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

onBeforeUnmount(() => {
  unsubDeepLink()
})

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
  if (await actions.onCreate(request)) editorOpen.value = false
}

async function onSave(payload) {
  if (await actions.onSave(payload)) editorOpen.value = false
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
