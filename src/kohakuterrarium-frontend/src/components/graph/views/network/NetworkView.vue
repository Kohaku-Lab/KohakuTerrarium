<template>
  <FlowHost ref="host" :flow-id="flowId" :view="view" :actions="actions" :nodes="nodes" :edges="edges" :node-types="nodeTypes" :fit-signal="fitSignal" :layout-error="layoutError" connectable @hover="hoverId = $event" @hover-edge="hoverEdgeId = $event" @menu="$emit('menu', $event)" @open-chat="$emit('open-chat')" @request-delete="$emit('request-delete')" @quick-add="$emit('quick-add', $event)" @focus-search="$emit('focus-search')" />
</template>

<script setup>
import { computed, markRaw, ref } from "vue"

import AggregateNode from "@/components/graph/canvas/nodes/AggregateNode.vue"
import ChannelNode from "@/components/graph/canvas/nodes/ChannelNode.vue"
import CreatureNode from "@/components/graph/canvas/nodes/CreatureNode.vue"
import GroupNode from "@/components/graph/canvas/nodes/GroupNode.vue"
import FlowHost from "@/components/graph/views/shared/FlowHost.vue"
import { backdropElements, edgeElement } from "@/components/graph/views/shared/elements"
import { useViewLayout } from "@/composables/graph/useViewLayout"
import { useGraphLiveStore } from "@/stores/graph/live"
import { layoutGraph } from "@/utils/graph/layout/auto"
import { controlDock } from "@/utils/graph/layout/place/dock"
import { sizeOf } from "@/utils/graph/layout/place/elk"
import { edgeBends, hoverNeighbourhood } from "@/utils/graph/layout/route/edges"
import { orthogonalRoute } from "@/utils/graph/layout/route/route"

const props = defineProps({
  view: { type: Object, required: true },
  actions: { type: Object, required: true },
  flowId: { type: String, required: true },
})
defineEmits(["request-delete", "quick-add", "open-chat", "focus-search", "menu"])

const live = useGraphLiveStore()
const host = ref(null)
const hoverId = ref(null)
const hoverEdgeId = ref(null)

const nodeTypes = {
  creature: markRaw(CreatureNode),
  channel: markRaw(ChannelNode),
  group: markRaw(GroupNode),
  aggregate: markRaw(AggregateNode),
}

const projection = computed(() => props.view.projection)

const structure = computed(() => {
  const p = projection.value
  return `${p.groupBy}|${p.nodes.map((n) => `${n.id}@${n.parent || ""}`).join(",")}|${p.edges
    .filter((e) => e.kind !== "lineage")
    .map((e) => e.id)
    .join(",")}|${props.view.layoutNonce}`
})
const fitScope = computed(() => `${props.view.effectiveSessionId || "*"}|${projection.value.groupBy}|${props.view.layoutNonce}|${props.view.sample || ""}`)

const { boxes, routes, layoutError, fitSignal } = useViewLayout({
  view: props.view,
  input: projection,
  structure,
  run: (p, ctx) => layoutGraph({ ...p, dock: controlDock(p) }, "network", ctx),
  sizeOf,
  fitScope,
})

const hoverLit = computed(() => (hoverId.value ? hoverNeighbourhood(hoverId.value, projection.value) : null))
const multiHost = computed(() => projection.value.stats.hosts > 1 && projection.value.groupBy !== "host")

function isDim(id) {
  if (projection.value.dimmed.has(id)) return true
  return !!hoverLit.value && !hoverLit.value.has(id)
}

function isLit(id) {
  return !!hoverLit.value && hoverLit.value.has(id) && hoverId.value !== id
}

const groupStatuses = computed(() => {
  const map = new Map()
  for (const g of projection.value.groups)
    map.set(
      g.id,
      g.creatureIds.map((id) => props.view.index.get(id)?.item?.status || "idle"),
    )
  return map
})

const nodes = computed(() => {
  const p = projection.value
  const out = backdropElements(p.groups, boxes.value, props.view)
  for (const n of p.nodes) {
    const box = boxes.value.get(n.id)
    if (!box) continue
    const node = {
      id: n.id,
      type: n.kind,
      position: { x: box.x, y: box.y },
      style: { width: `${box.width}px`, height: `${box.height}px` },
      selected: props.view.selection?.id === n.id,
      connectable: n.kind !== "aggregate",
    }
    const common = { dimmed: isDim(n.id), lit: isLit(n.id) }
    if (n.kind === "creature") {
      node.data = { ...common, target: { kind: "creature", item: n.creature }, creature: n.creature, showHost: multiHost.value }
    } else if (n.kind === "channel") {
      node.data = { ...common, target: { kind: "channel", item: n.channel }, channel: n.channel, lastMessage: live.lastMessages[n.id] || null, pulseAt: live.pulses[n.id] || 0 }
    } else {
      node.data = { ...common, target: { kind: "group", item: n.group }, group: n.group, memberStatuses: groupStatuses.value.get(n.id) || [], onToggleCollapse: props.view.toggleCollapse }
    }
    out.push(node)
  }
  return out
})

const edges = computed(() => {
  const p = projection.value
  // Vue Flow drops edges whose endpoints are not nodes yet, so only placed endpoints are passed.
  const placed = p.edges.filter((e) => boxes.value.has(e.source) && boxes.value.has(e.target))
  const bends = edgeBends(placed, boxes.value)
  return placed.map((e) => {
    const listen = e.kind === "channel" && e.mode === "listen"
    const source = listen ? e.target : e.source
    const target = listen ? e.source : e.target
    const bend = bends.get(e.id) || 0
    let points = null
    if (props.view.edgeStyle === "orthogonal") {
      // Keep the placement's own route until either endpoint is moved by hand.
      const moved = props.view.positionOverrides[source] || props.view.positionOverrides[target]
      points = (!moved && routes.value.get(e.id)) || orthogonalRoute(boxes.value.get(source), boxes.value.get(target), bend / 2)
    }
    return edgeElement(e, {
      source,
      target,
      points,
      bend: props.view.edgeStyle === "curved" ? bend : 0,
      selected: props.view.selection?.id === e.id,
      dimmed: isDim(e.id),
      lit: hoverEdgeId.value === e.id || (!!hoverLit.value && hoverLit.value.has(e.id)),
      pulseAt: e.kind === "channel" ? live.pulses[e.target] || 0 : 0,
    })
  })
})

defineExpose({ fit: () => host.value?.fit() })
</script>
