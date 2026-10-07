<template>
  <FlowHost ref="host" :flow-id="flowId" :view="view" :actions="actions" :nodes="nodes" :edges="edges" :node-types="nodeTypes" :fit-signal="fitSignal" :layout-error="layoutError" @hover="hoverId = $event" @menu="$emit('menu', $event)" @open-chat="$emit('open-chat')" @request-delete="$emit('request-delete')" @focus-search="$emit('focus-search')" />
</template>

<script setup>
import { computed, markRaw, ref } from "vue"

import TierCardNode from "@/components/graph/views/tiers/TierCardNode.vue"
import TreeRootNode from "@/components/graph/views/tiers/TreeRootNode.vue"
import FlowHost from "@/components/graph/views/shared/FlowHost.vue"
import { edgeElement } from "@/components/graph/views/shared/elements"
import { useViewLayout } from "@/composables/graph/useViewLayout"
import { layoutGraph } from "@/utils/graph/layout/auto"
import { neighbourhoodOf } from "@/utils/graph/data/projection"
import { sizeOf } from "@/utils/graph/layout/place/elk"
import { bracketRoutes } from "@/utils/graph/layout/place/tree"
import { buildTierInput } from "@/utils/graph/views/tiers"

const props = defineProps({
  view: { type: Object, required: true },
  actions: { type: Object, required: true },
  flowId: { type: String, required: true },
})
defineEmits(["request-delete", "open-chat", "focus-search", "menu"])

const host = ref(null)
const hoverId = ref(null)
const nodeTypes = { tier: markRaw(TierCardNode), user: markRaw(TreeRootNode), session: markRaw(TreeRootNode) }

const input = computed(() => buildTierInput(props.view.projection))
const structure = computed(() => {
  const i = input.value
  return `${i.nodes.map((n) => n.id).join(",")}|${i.edges.map((e) => e.id).join(",")}|${props.view.layoutNonce}`
})
const fitScope = computed(() => `${props.view.effectiveSessionId || "*"}|${props.view.layoutNonce}|${props.view.sample || ""}`)

const { boxes, routes, layoutError, fitSignal } = useViewLayout({
  view: props.view,
  input,
  structure,
  run: (i, ctx) => layoutGraph(i, "tiers", ctx),
  sizeOf,
  fitScope,
})

const hoverLit = computed(() => (hoverId.value ? neighbourhoodOf(hoverId.value, input.value.nodes, input.value.edges) : null))

function nameOf(id) {
  return props.view.index.get(id)?.item?.name || id
}

function isDim(id) {
  return props.view.projection.dimmed.has(id) || (!!hoverLit.value && !hoverLit.value.has(id))
}

const nodes = computed(() => {
  const out = []
  for (const n of input.value.nodes) {
    const box = boxes.value.get(n.id)
    if (!box) continue
    const node = {
      id: n.id,
      type: n.kind,
      position: { x: box.x, y: box.y },
      style: { width: `${box.width}px`, height: `${box.height}px` },
      connectable: false,
    }
    if (n.kind === "tier") {
      node.selected = props.view.selection?.id === n.id
      node.data = {
        target: { kind: "creature", item: n.creature },
        creature: n.creature,
        wiresOut: n.wiresOut,
        showHost: input.value.multiHost,
        nameOf,
        onSelect: props.view.select,
        dimmed: isDim(n.id),
        lit: !!hoverLit.value && hoverLit.value.has(n.id) && hoverId.value !== n.id,
      }
    } else {
      node.selectable = false
      node.data = { kind: n.kind, label: n.label || "", dimmed: isDim(n.id) }
    }
    out.push(node)
  }
  return out
})

// A dragged card's connectors fall back to brackets from the current boxes, so the tree keeps its shape.
const brackets = computed(() => bracketRoutes(input.value.edges, boxes.value))

function pointsOf(e) {
  const moved = props.view.positionOverrides[e.source] || props.view.positionOverrides[e.target]
  return (!moved && routes.value.get(e.id)) || brackets.value.get(e.id)
}

const edges = computed(() =>
  input.value.edges
    .filter((e) => boxes.value.has(e.source) && boxes.value.has(e.target))
    .map((e) =>
      edgeElement(e, {
        source: e.source,
        target: e.target,
        points: pointsOf(e),
        lit: !!hoverLit.value && hoverLit.value.has(e.id),
        dimmed: !!hoverLit.value && !hoverLit.value.has(e.id),
        menu: false,
      }),
    ),
)

defineExpose({ fit: () => host.value?.fit() })
</script>
