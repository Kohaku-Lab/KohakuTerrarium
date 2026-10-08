<template>
  <div class="kt-graph-surface h-full w-full flex flex-col min-h-0 bg-warm-50 dark:bg-warm-950">
    <GraphToolbar ref="toolbarEl" :store-key="storeKey" :compact="isCompact" @fit="fit" @quick-add="openQuickAdd" @new-session="newSessionOpen = true" />

    <div class="flex-1 min-h-0 flex relative">
      <div class="flex-1 min-w-0 relative">
        <BusView v-if="view.view === 'bus'" :view="view" :actions="actions" @open-chat="openChat" @request-delete="requestDelete" @menu="openMenu" />
        <component :is="canvasView" v-else :key="view.view" ref="canvasEl" :view="view" :actions="actions" :flow-id="`${flowId}-${view.view}`" @open-chat="openChat" @request-delete="requestDelete" @quick-add="onCanvasQuickAdd" @focus-search="toolbarEl?.focusSearch()" @menu="openMenu" />
        <button v-if="!dockOpen" class="absolute top-2 right-2 kt-graph-action bg-warm-50 dark:bg-warm-950" :title="t('graph.dock.open')" @click="dockOpen = true"><span class="i-carbon-side-panel-open" /></button>
      </div>
      <div v-if="dockOpen" class="border-l border-warm-200 dark:border-warm-700 min-h-0" :class="isCompact ? 'absolute inset-x-0 bottom-0 h-[65%] border-t z-20 shadow-lg' : 'shrink-0'" :style="isCompact ? null : { width: `${dockWidth}px` }">
        <div v-if="!isCompact" class="absolute top-0 bottom-0 w-1.5 -ml-0.75 cursor-col-resize z-10 hover:bg-iolite/30" :style="{ right: `${dockWidth - 3}px` }" @pointerdown="startResize" />
        <GraphDock ref="dockEl" :view="view" :actions="actions" @close="dockOpen = false" @quick-add="openQuickAdd" @new-session="newSessionOpen = true" />
      </div>
    </div>

    <footer class="h-6 shrink-0 flex items-center gap-3 px-2 text-[10px] font-mono text-warm-500 border-t border-warm-200 dark:border-warm-700 whitespace-nowrap overflow-hidden">
      <span class="flex items-center gap-1 shrink-0"><span class="w-1.5 h-1.5 rounded-full" :class="wsDot" />{{ wsLabel }}</span>
      <span class="shrink-0">{{ t("graph.summary.counts", view.projection.stats) }}</span>
      <span v-if="view.projection.stats.hosts > 1" class="shrink-0">{{ t("graph.summary.hosts", { n: view.projection.stats.hosts }) }}</span>
      <span v-if="view.layoutInfo && view.view !== 'bus'" class="min-w-0 truncate" :title="layoutTitle">{{ t("graph.summary.layout", { name: view.layoutInfo.chosen, crossings: view.layoutInfo.metrics.crossings, hits: view.layoutInfo.metrics.nodeHits, labels: view.layoutInfo.metrics.labelHits ?? 0 }) }}</span>
      <span v-if="view.isSample" class="px-1.5 rounded bg-amber/15 text-amber-shadow dark:text-amber-light">{{ t("graph.summary.sample") }}</span>
      <span v-if="live.error" class="text-coral truncate">{{ live.error }}</span>
      <span class="flex-1" />
      <span class="hidden xl:inline shrink-0">{{ t("graph.summary.keys") }}</span>
    </footer>

    <GraphContextMenu v-if="menu" :x="menu.x" :y="menu.y" :title="menu.title" :items="menu.items" @pick="onMenuPick" @close="menu = null" />
    <GraphQuickAdd v-if="quickAdd" :view="view" :actions="actions" :initial-kind="quickAdd.kind" :context="quickAdd.context" @close="quickAdd = null" />
    <NewSessionDialog v-if="newSessionOpen" silent @close="newSessionOpen = false" />
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref, watch } from "vue"

import "@/components/graph/graph.css"
import BusView from "@/components/graph/views/bus/BusView.vue"
import FlowView from "@/components/graph/views/flow/FlowView.vue"
import NetworkView from "@/components/graph/views/network/NetworkView.vue"
import TiersView from "@/components/graph/views/tiers/TiersView.vue"
import GraphContextMenu from "@/components/graph/canvas/GraphContextMenu.vue"
import { menuItemsFor } from "@/components/graph/canvas/graphMenus"
import GraphDock from "@/components/graph/dock/GraphDock.vue"
import GraphQuickAdd from "@/components/graph/toolbar/GraphQuickAdd.vue"
import GraphToolbar from "@/components/graph/toolbar/GraphToolbar.vue"
import NewSessionDialog from "@/components/shell/newSession/NewSessionDialog.vue"
import { useGraphActions } from "@/composables/graph/useGraphActions"
import { useDensity } from "@/composables/useDensity"
import { useGraphLiveStore } from "@/stores/graph/live"
import { useGraphViewStore } from "@/stores/graph/view"
import { useI18n } from "@/utils/i18n"
import { readLocalJsonPref, writeLocalJsonPref } from "@/utils/uiPrefs"

const props = defineProps({
  storeKey: { type: String, required: true },
  sessionId: { type: String, default: null },
  lockSession: { type: Boolean, default: false },
  embeddedChat: { type: Boolean, default: true },
})
const emit = defineEmits(["open-chat"])

const DOCK_WIDTH_KEY = "kt.graph.dockWidth"

const { t } = useI18n()
const { isCompact } = useDensity()
const live = useGraphLiveStore()
const view = useGraphViewStore(props.storeKey)
const actions = useGraphActions(view)

const flowId = `kt-graph-${props.storeKey}`
const toolbarEl = ref(null)
const canvasEl = ref(null)
const dockEl = ref(null)
const dockOpen = ref(!isCompact.value && props.embeddedChat)
const DOCK_DEFAULT = 300
const DOCK_MIN = 240
const DOCK_MAX = 720
const dockWidth = ref(readLocalJsonPref(DOCK_WIDTH_KEY, DOCK_DEFAULT) || DOCK_DEFAULT)
const quickAdd = ref(null)
const newSessionOpen = ref(false)
const menu = ref(null)
const CANVAS_VIEWS = { network: NetworkView, flow: FlowView, tiers: TiersView }
const canvasView = computed(() => CANVAS_VIEWS[view.view] || NetworkView)

if (props.sessionId || props.lockSession) view.setSession(props.sessionId, { lock: props.lockSession })
watch(
  () => props.sessionId,
  (id) => {
    if (props.lockSession || id) view.setSession(id, { lock: props.lockSession })
  },
)

watch(
  () => view.selection,
  (sel) => {
    if (sel && !isCompact.value) dockOpen.value = true
  },
)

const wsDot = computed(() => {
  if (view.isSample) return "bg-amber"
  if (live.wsStatus === "open") return "bg-aquamarine"
  if (live.wsStatus === "closed") return "bg-warm-400"
  return "bg-amber"
})
const layoutTitle = computed(() => (view.layoutInfo?.tried || []).map((c) => (c.metrics ? `${c.name}: cost ${c.metrics.cost}, ${c.metrics.crossings} crossings, ${c.metrics.merged}px shared by unrelated edges, ${c.metrics.nodeHits} through nodes, length ${c.metrics.length} (ink ${c.metrics.ink}), ${c.metrics.width}×${c.metrics.height} (aspect ${c.metrics.aspect} vs view ${view.viewportAspect})` : `${c.name}: ${c.error}`)).join("\n"))
const wsLabel = computed(() => (view.isSample ? t("graph.summary.sampleData") : t(`graph.ws.${live.wsStatus}`)))

function fit() {
  canvasEl.value?.fit()
}

function openChat() {
  if (!props.embeddedChat) {
    emit("open-chat", view.selected)
    return
  }
  dockOpen.value = true
  requestAnimationFrame(() => dockEl.value?.openChat())
}

function requestDelete() {
  dockOpen.value = true
  requestAnimationFrame(() => dockEl.value?.requestDelete())
}

function openQuickAdd(kind) {
  if (view.isSample || !view.model.sessions.length) {
    newSessionOpen.value = true
    return
  }
  quickAdd.value = { kind: kind || "creature", context: null }
}

function onCanvasQuickAdd({ context }) {
  if (view.isSample) return
  quickAdd.value = { kind: context?.handle === "send" ? "channel" : "creature", context }
}

function menuTitle(target) {
  if (target.kind === "creature" || target.kind === "channel") return target.item.name
  if (target.kind === "group") return target.item.label
  if (target.kind === "edge") return t(`graph.edge.kind.${target.item.kind}`)
  return ""
}

function openMenu({ x, y, target }) {
  const items = menuItemsFor(target, view, t)
  if (items.length) menu.value = { x, y, target, items, title: menuTitle(target) }
}

function creatureTabKey(c) {
  const session = view.model.sessions.find((s) => s.id === c.sessionId)
  return c.root && session?.creatureIds.length > 1 ? "root" : c.name
}

function openTabFor(target) {
  if (target.kind === "creature") actions.openChatTab(target.item.sessionId, creatureTabKey(target.item))
  else if (target.kind === "channel") actions.openChatTab(target.item.sessionId, `ch:${target.item.name}`)
  else if (target.kind === "group") actions.openChatTab(target.item.key)
}

function showDetails() {
  dockOpen.value = true
  requestAnimationFrame(() => dockEl.value?.showDetails())
}

function onMenuPick(id) {
  const target = menu.value?.target
  menu.value = null
  if (!target) return
  const item = target.item
  switch (id) {
    case "chat":
      return openChat()
    case "open-tab":
      return openTabFor(target)
    case "inspect":
      return actions.openInspector(item.sessionId)
    case "interrupt":
    case "start":
    case "stop":
      return actions[id](item)
    case "new-channel-from":
      quickAdd.value = { kind: "channel", context: { creatureId: item.id, handle: "send" } }
      return
    case "new-creature-wired":
      quickAdd.value = { kind: "creature", context: { creatureId: item.id, handle: "wire" } }
      return
    case "focus":
      view.focusMode = true
      return
    case "details":
      return showDetails()
    case "wire-payload":
      return actions.updateWire(item, { withContent: item.withContent === false, prompt: item.prompt || "" })
    case "wire-reverse":
      return actions.reverseWire(item)
    case "remove":
    case "stop-session":
      return requestDelete()
    case "toggle-collapse":
      return view.toggleCollapse(item.id)
    case "focus-session":
      return view.setSession(item.key)
    case "add-creature":
    case "add-channel":
      return openQuickAdd(id === "add-creature" ? "creature" : "channel")
    case "fit":
      return fit()
    case "relayout":
      return view.relayout()
  }
}

function startResize(e) {
  const startX = e.clientX
  const startWidth = dockWidth.value
  const move = (ev) => {
    dockWidth.value = Math.min(DOCK_MAX, Math.max(DOCK_MIN, startWidth + (startX - ev.clientX)))
  }
  const up = () => {
    window.removeEventListener("pointermove", move)
    window.removeEventListener("pointerup", up)
    writeLocalJsonPref(DOCK_WIDTH_KEY, dockWidth.value)
  }
  window.addEventListener("pointermove", move)
  window.addEventListener("pointerup", up)
}

onMounted(() => live.acquire())
onUnmounted(() => live.release())
</script>
