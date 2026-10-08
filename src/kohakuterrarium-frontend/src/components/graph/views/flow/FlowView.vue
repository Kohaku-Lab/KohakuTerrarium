<template>
  <FlowHost ref="host" :flow-id="flowId" :view="view" :actions="actions" :nodes="nodes" :edges="edges" :node-types="nodeTypes" :fit-signal="fitSignal" :layout-error="layoutError" connectable :fit-min-zoom="0.85" @hover="hoverId = $event" @menu="$emit('menu', $event)" @open-chat="$emit('open-chat')" @request-delete="$emit('request-delete')" @quick-add="$emit('quick-add', $event)" @focus-search="$emit('focus-search')" />
</template>

<script setup>
import { computed, markRaw, ref } from "vue"

import AggregateNode from "@/components/graph/canvas/nodes/AggregateNode.vue"
import GroupNode from "@/components/graph/canvas/nodes/GroupNode.vue"
import FlowBundleLabel from "@/components/graph/views/flow/FlowBundleLabel.vue"
import FlowHubNode from "@/components/graph/views/flow/FlowHubNode.vue"
import FlowPrivilegedNode from "@/components/graph/views/flow/FlowPrivilegedNode.vue"
import FlowStageNode from "@/components/graph/views/flow/FlowStageNode.vue"
import FlowHost from "@/components/graph/views/shared/FlowHost.vue"
import { backdropElements, edgeElement } from "@/components/graph/views/shared/elements"
import { useViewLayout } from "@/composables/graph/useViewLayout"
import { neighbourhoodOf } from "@/utils/graph/data/projection"
import { layoutGraph } from "@/utils/graph/layout/auto"
import { flowEndpoints, labelSize, sizeOf } from "@/utils/graph/layout/place/elk"
import { orthogonalRoute, sideRoute } from "@/utils/graph/layout/route/route"
import { buildFlowInput, flowLabel } from "@/utils/graph/views/flow"

const props = defineProps({
  view: { type: Object, required: true },
  actions: { type: Object, required: true },
  flowId: { type: String, required: true },
})
defineEmits(["request-delete", "quick-add", "open-chat", "focus-search", "menu"])

const host = ref(null)
const hoverId = ref(null)

const nodeTypes = {
  stage: markRaw(FlowStageNode),
  privileged: markRaw(FlowPrivilegedNode),
  hub: markRaw(FlowHubNode),
  group: markRaw(GroupNode),
  aggregate: markRaw(AggregateNode),
  bundleLabel: markRaw(FlowBundleLabel),
}

const input = computed(() => buildFlowInput(props.view.projection, { privilegedLinks: props.view.privilegedLinks }))
const structure = computed(() => {
  const i = input.value
  return `${props.view.projection.groupBy}|${i.nodes.map((n) => `${n.id}@${n.parent || ""}:${n.size ? `${n.size.width}x${n.size.height}` : ""}`).join(",")}|${i.edges
    .filter((e) => !e.side)
    .map((e) => `${e.id}:${e.layoutLabel || ""}`)
    .join(",")}|${i.bundles.map((b) => `${b.id}:${b.text}`).join(",")}|${props.view.layoutNonce}`
})
const fitScope = computed(() => `${props.view.effectiveSessionId || "*"}|${props.view.projection.groupBy}|${props.view.layoutNonce}|${props.view.sample || ""}|${props.view.privilegedLinks}`)

const { boxes, routes, labels, layoutError, fitSignal } = useViewLayout({
  view: props.view,
  input,
  structure,
  run: (i, ctx) => layoutGraph(i, "flow", ctx),
  sizeOf,
  fitScope,
})

const backdrops = computed(() => backdropElements(input.value.groups, boxes.value, props.view))

const hoverLit = computed(() => (hoverId.value ? neighbourhoodOf(hoverId.value, input.value.nodes, input.value.edges) : null))

function isDim(id) {
  if (props.view.projection.dimmed.has(id)) return true
  return !!hoverLit.value && !hoverLit.value.has(id)
}

function litOf(id) {
  return !!hoverLit.value && hoverLit.value.has(id) && hoverId.value !== id
}

function boxStyle(box) {
  return { width: `${box.width}px`, height: `${box.height}px` }
}

const nodes = computed(() => {
  const out = [...backdrops.value]
  for (const n of input.value.nodes) {
    const box = boxes.value.get(n.id)
    if (!box) continue
    const node = { id: n.id, type: n.kind, position: { x: box.x, y: box.y }, style: boxStyle(box), connectable: n.kind === "stage" }
    const common = { dimmed: isDim(n.id), lit: litOf(n.id) }
    if (n.kind === "privileged") {
      node.selected = props.view.selection?.id === n.id
      node.data = { ...common, target: { kind: "creature", item: n.creature }, creature: n.creature }
    } else if (n.kind === "hub") {
      node.selectable = false
      node.data = { ...common, count: n.count }
    } else if (n.kind === "stage") {
      node.selected = props.view.selection?.id === n.id
      node.data = { ...common, target: { kind: "creature", item: n.creature }, creature: n.creature, rooms: n.rooms, inlets: n.inlets, outlets: n.outlets, wireChips: n.wireChips, showHost: input.value.multiHost }
    } else {
      node.selected = props.view.selection?.id === n.id
      node.data = { ...common, target: { kind: "group", item: n.group }, group: n.group, memberStatuses: [], onToggleCollapse: props.view.toggleCollapse }
    }
    out.push(node)
  }
  // A bundle label names the channel a whole fan of arrows carries; it follows the drawing, not a drag.
  const moved = (b) =>
    b.members.some((id) => {
      const e = input.value.edges.find((x) => x.id === id)
      return e && (props.view.positionOverrides[e.source] || props.view.positionOverrides[e.target])
    })
  for (const b of input.value.bundles) {
    const at = labels.value.get(b.id)
    if (!at || moved(b)) continue
    const size = labelSize(b.text)
    out.push({ id: b.id, type: "bundleLabel", position: { x: at.x - size.width / 2, y: at.y - size.height / 2 }, style: boxStyle(size), draggable: false, selectable: false, connectable: false, zIndex: 5, data: { text: b.text, dimmed: b.members.every((id) => isDim(id)) } })
  }
  return out
})

// The axis work flows along in the chosen layout; direct links enter cards across it.
const flowAxis = computed(() => (/^flow-down/.test(props.view.layoutInfo?.chosen || "") ? "down" : "right"))

// Side routes depend only on the boxes and the axis, so hover and selection never recompute them.
const sideRoutes = computed(() => {
  const out = new Map()
  const obstacles = [...boxes.value.values()]
  for (const e of input.value.edges) {
    if (!e.side || !boxes.value.has(e.source) || !boxes.value.has(e.target)) continue
    out.set(e.id, sideRoute(boxes.value.get(e.source), boxes.value.get(e.target), flowAxis.value, obstacles))
  }
  return out
})

const edges = computed(() =>
  input.value.edges
    .filter((e) => !e.layoutOnly && boxes.value.has(e.source) && boxes.value.has(e.target))
    .map((e) => {
      const [source, target] = flowEndpoints(e)
      const moved = props.view.positionOverrides[source] || props.view.positionOverrides[target]
      const points = e.side ? sideRoutes.value.get(e.id) : (!moved && routes.value.get(e.id)) || orthogonalRoute(boxes.value.get(source), boxes.value.get(target))
      return edgeElement(e, {
        source,
        target,
        points,
        labelPos: moved ? null : labels.value.get(e.id) || null,
        selected: props.view.selection?.id === e.id,
        dimmed: isDim(e.id),
        lit: !!hoverLit.value && hoverLit.value.has(e.id),
        label: flowLabel(e),
        menu: true,
      })
    }),
)

defineExpose({ fit: () => host.value?.fit() })
</script>
