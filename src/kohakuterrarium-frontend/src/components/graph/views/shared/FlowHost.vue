<template>
  <div ref="rootEl" class="kt-graph-canvas relative h-full w-full outline-none" tabindex="0" @keydown="onKeydown">
    <VueFlow :id="flowId" :nodes="nodes" :edges="edges" :node-types="nodeTypes" :edge-types="edgeTypes" :min-zoom="0.1" :max-zoom="2" :delete-key-code="null" :selection-key-code="null" :multi-selection-key-code="null" :nodes-connectable="connectable" :only-render-visible-elements="nodes.length > 160" :is-valid-connection="isValidConnection" :connection-radius="28" :elevate-edges-on-select="true" @node-click="onNodeClick" @node-double-click="onNodeDoubleClick" @node-context-menu="onNodeContextMenu" @node-mouse-enter="({ node }) => $emit('hover', node.type === 'group' ? null : node.id)" @node-mouse-leave="$emit('hover', null)" @edge-click="onEdgeClick" @edge-context-menu="onEdgeContextMenu" @edge-mouse-enter="({ edge }) => $emit('hover-edge', edge.id)" @edge-mouse-leave="$emit('hover-edge', null)" @pane-click="view.select(null)" @pane-context-menu="onPaneContextMenu" @node-drag-start="onNodeDragStart" @node-drag="onNodeDrag" @node-drag-stop="onNodeDragStop" @connect="onConnect" @connect-start="onConnectStart" @connect-end="onConnectEnd">
      <Background :pattern-color="canvasColors.grid" :gap="22" :size="1.2" />
      <Controls position="bottom-left" :show-interactive="false" />
      <MiniMap v-if="view.minimap && nodes.length > 1" position="bottom-right" pannable zoomable :node-color="minimapColor" :node-stroke-width="0" :mask-color="canvasColors.mask" />
    </VueFlow>
    <button v-if="view.minimap && nodes.length > 1" class="kt-graph-minimap-hide absolute z-10 w-5 h-5 flex items-center justify-center rounded bg-warm-100/90 dark:bg-warm-800/90 text-warm-500 hover:text-iolite" :title="t('graph.toolbar.hideMinimap')" @click="view.setMinimap(false)"><span class="i-carbon-close text-xs" /></button>
    <div v-if="layoutError" class="absolute top-2 left-1/2 -translate-x-1/2 text-xs px-2 py-1 rounded bg-coral/15 text-coral">{{ layoutError }}</div>
    <div v-if="!nodes.length" class="absolute inset-0 flex flex-col items-center justify-center gap-2 text-sm text-warm-500 pointer-events-none">
      <span class="i-carbon-network-3 text-3xl text-warm-400" />
      <span>{{ t("graph.empty.title") }}</span>
      <span class="text-xs text-warm-400">{{ t("graph.empty.hint") }}</span>
    </div>
  </div>
</template>

<script setup>
import { computed, markRaw, nextTick, onBeforeUnmount, onMounted, provide, ref, watch } from "vue"
import { VueFlow, useVueFlow } from "@vue-flow/core"
import { Background } from "@vue-flow/background"
import { Controls } from "@vue-flow/controls"
import { MiniMap } from "@vue-flow/minimap"

import GraphEdge from "@/components/graph/canvas/edges/GraphEdge.vue"
import { connectionIntent } from "@/composables/graph/useGraphActions"
import { useThemeStore } from "@/stores/theme"
import { useI18n } from "@/utils/i18n"
import { GEM } from "@/utils/colors"
import { FIT_MAX, FIT_PAD, fitViewport } from "@/utils/graph/views/fit"

const props = defineProps({
  flowId: { type: String, required: true },
  view: { type: Object, required: true },
  actions: { type: Object, required: true },
  nodes: { type: Array, required: true },
  edges: { type: Array, required: true },
  nodeTypes: { type: Object, required: true },
  fitSignal: { type: Number, default: 0 },
  layoutError: { type: String, default: "" },
  connectable: { type: Boolean, default: false },
  fitMinZoom: { type: Number, default: 0.55 },
})
const emit = defineEmits(["request-delete", "quick-add", "open-chat", "focus-search", "menu", "hover", "hover-edge"])

const { t } = useI18n()
const theme = useThemeStore()
const { fitView, getNodes, getViewport, project, setViewport } = useVueFlow(props.flowId)
const edgeTypes = { graph: markRaw(GraphEdge) }

// SVG presentation attributes cannot read CSS variables, so the warm-scale values are passed directly.
const canvasColors = computed(() => (theme.dark ? { grid: "#3A3632", mask: "rgba(26, 24, 22, 0.7)" } : { grid: "#C5BFB7", mask: "rgba(239, 236, 231, 0.7)" }))

const rootEl = ref(null)

let resizeObserver = null
let refitTimer = null
onMounted(() => {
  if (typeof ResizeObserver === "undefined" || !rootEl.value) return
  resizeObserver = new ResizeObserver(([entry]) => {
    props.view.setViewportSize(entry.contentRect.width, entry.contentRect.height)
    // A canvas that resizes under an untouched fit (toolbar wrap, dock resize) is fitted again.
    clearTimeout(refitTimer)
    refitTimer = setTimeout(() => {
      if (lastFit && sameViewport(getViewport(), lastFit)) fit()
    }, 150)
  })
  resizeObserver.observe(rootEl.value)
})
onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  clearTimeout(refitTimer)
})

let lastFit = null
const sameViewport = (a, b) => Math.abs(a.x - b.x) < 1 && Math.abs(a.y - b.y) < 1 && Math.abs(a.zoom - b.zoom) < 0.001

// A drag publishes live positions to the view store, so backdrops and edges follow it
// and a status refresh mid-drag re-renders the node where the pointer has it.
let drag = null

function groupMembers(node) {
  const g = node.data?.group
  return g ? [...g.creatureIds, ...g.channelIds, ...(g.extraIds || [])] : []
}

function onNodeDragStart({ node }) {
  const at = new Map(props.nodes.map((n) => [n.id, n.position]))
  const members = node.type === "group" ? groupMembers(node).filter((id) => at.has(id)) : []
  drag = { id: node.id, origin: { ...node.position }, members: members.map((id) => [id, { ...at.get(id) }]) }
}

function dragTargets(node) {
  if (!drag || drag.id !== node.id) return {}
  if (!drag.members.length) return node.type === "group" ? {} : { [node.id]: { x: node.position.x, y: node.position.y } }
  const dx = node.position.x - drag.origin.x
  const dy = node.position.y - drag.origin.y
  return Object.fromEntries(drag.members.map(([id, p]) => [id, { x: p.x + dx, y: p.y + dy }]))
}

function onNodeDrag({ node }) {
  props.view.setDragPositions(dragTargets(node))
}

function onNodeDragStop({ node }) {
  const final = dragTargets(node)
  if (Object.keys(final).length) props.view.rememberPositions(final)
  props.view.setDragPositions(null)
  drag = null
}

watch(
  () => props.fitSignal,
  async () => {
    // Fit only after Vue Flow has measured the new nodes, or the bounds are stale.
    await nextTick()
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)))
    fit()
  },
)

// Fitting never zooms below legible text; the minimap covers what falls outside.
function fit() {
  const bounds = contentBounds()
  const canvas = rootEl.value?.getBoundingClientRect()
  if (!bounds || !canvas?.width) {
    fitView({ padding: FIT_PAD, duration: 200, minZoom: props.fitMinZoom, maxZoom: FIT_MAX })
    return
  }
  const map = rootEl.value.querySelector(".vue-flow__minimap")?.getBoundingClientRect()
  lastFit = fitViewport(bounds, { width: canvas.width, height: canvas.height }, map ? { width: map.width, height: map.height, right: canvas.right - map.right, bottom: canvas.bottom - map.bottom } : null, props.fitMinZoom)
  setViewport(lastFit, { duration: 200 })
}

function contentBounds() {
  const list = getNodes.value.filter((n) => n.dimensions?.width)
  if (!list.length) return null
  const x0 = Math.min(...list.map((n) => n.computedPosition.x))
  const y0 = Math.min(...list.map((n) => n.computedPosition.y))
  const x1 = Math.max(...list.map((n) => n.computedPosition.x + n.dimensions.width))
  const y1 = Math.max(...list.map((n) => n.computedPosition.y + n.dimensions.height))
  return { x: x0, y: y0, width: x1 - x0, height: y1 - y0 }
}

function targetOf(id) {
  return props.nodes.find((n) => n.id === id)?.data?.target || null
}

function entryOf(id) {
  const target = targetOf(id)
  if (!target) return null
  return { id, kind: target.kind, creature: target.kind === "creature" ? target.item : null, channel: target.kind === "channel" ? target.item : null }
}

function openMenuFor(event, id) {
  const target = targetOf(id)
  if (!target) return
  props.view.select(target.kind, target.selectId || id)
  emit("menu", { x: event.clientX, y: event.clientY, target })
}
provide("ktGraphMenu", openMenuFor)

// Vue Flow also runs this over every edge it is given; those carry an id and are already valid.
function isValidConnection(conn) {
  if (conn.id) return true
  return !!connectionIntent(entryOf(conn.source), conn.sourceHandle, entryOf(conn.target))
}

let pendingConnect = null

function onConnectStart(params) {
  pendingConnect = { nodeId: params.nodeId, handleId: params.handleId, connected: false }
}

function onConnect(conn) {
  if (pendingConnect) pendingConnect.connected = true
  const source = entryOf(conn.source)
  const target = entryOf(conn.target)
  const intent = connectionIntent(source, conn.sourceHandle, target)
  if (intent) props.actions.connect(intent, source, target)
}

// A drop anywhere on a node body connects to it; a drop on empty canvas opens quick-add.
function onConnectEnd(event) {
  const pending = pendingConnect
  pendingConnect = null
  if (!pending || pending.connected || !event) return
  const point = "changedTouches" in event && event.changedTouches?.length ? event.changedTouches[0] : event
  const source = entryOf(pending.nodeId)
  if (!source) return
  const hit = document
    .elementsFromPoint(point.clientX, point.clientY)
    .map((el) => el.closest?.(".vue-flow__node"))
    .find(Boolean)
  const targetId = hit?.dataset?.id
  if (targetId && targetId !== source.id) {
    const target = entryOf(targetId)
    const intent = connectionIntent(source, pending.handleId, target)
    if (intent) props.actions.connect(intent, source, target)
    return
  }
  if (!hit && source.kind === "creature") {
    emit("quick-add", { clientX: point.clientX, clientY: point.clientY, context: { creatureId: source.id, handle: pending.handleId } })
  }
}

function onNodeClick({ node }) {
  const target = node.data?.target
  if (target) props.view.select(target.kind, target.selectId || node.id)
  rootEl.value?.focus({ preventScroll: true })
}

function onNodeDoubleClick({ node }) {
  const target = node.data?.target
  if (!target) return
  if (target.kind === "group") {
    props.view.toggleCollapse(node.id)
    return
  }
  props.view.select(target.kind, target.selectId || node.id)
  emit("open-chat")
}

function onNodeContextMenu({ event, node }) {
  event.preventDefault()
  openMenuFor(event, node.id)
}

function onEdgeClick({ edge }) {
  if (edge.data?.target) props.view.select("edge", edge.id, edge.data.target.item)
  rootEl.value?.focus({ preventScroll: true })
}

function onEdgeContextMenu({ event, edge }) {
  event.preventDefault()
  if (!edge.data?.target) return
  props.view.select("edge", edge.id, edge.data.target.item)
  const item = props.view.selected?.item || edge.data.target.item
  emit("menu", { x: event.clientX, y: event.clientY, target: { kind: "edge", item } })
}

function onPaneContextMenu(event) {
  event.preventDefault()
  const bounds = rootEl.value?.getBoundingClientRect()
  const flowPos = bounds ? project({ x: event.clientX - bounds.left, y: event.clientY - bounds.top }) : null
  emit("menu", { x: event.clientX, y: event.clientY, target: { kind: "pane", flowPos } })
}

function onKeydown(e) {
  if (["INPUT", "TEXTAREA"].includes(e.target?.tagName)) return
  switch (e.key) {
    case "Delete":
    case "Backspace":
      if (props.view.selection) {
        e.preventDefault()
        emit("request-delete")
      }
      break
    case "Escape":
      props.view.select(null)
      break
    case "f":
      fit()
      break
    case "Enter":
      if (props.view.selection) emit("open-chat")
      break
    case "/":
      e.preventDefault()
      emit("focus-search")
      break
  }
}

const MINIMAP_STATUS = { busy: GEM.aquamarine.main, idle: GEM.amber.light, paused: GEM.amber.main, stopped: "#C5BFB7", error: GEM.coral.main }

function minimapColor(node) {
  const target = node.data?.target
  if (node.type === "group") return "transparent"
  if (target?.kind === "channel") return GEM.aquamarine.main
  if (target?.kind === "creature") return MINIMAP_STATUS[target.item.status] || GEM.amber.light
  return "#C5BFB7"
}

defineExpose({ fit })
</script>
