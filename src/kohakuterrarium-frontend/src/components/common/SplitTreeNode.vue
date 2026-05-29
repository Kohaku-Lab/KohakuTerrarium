<template>
  <!--
    Generic recursive renderer for a binary split tree (see
    ``utils/splitTree.js`` for the node shape). Consumers provide a
    ``#leaf`` slot that receives ``{ id, path }`` and render their own
    leaf content (a chat panel, a tab group, a workspace panel).

    Single root element so the parent layout sees one definite child —
    multi-root v-if/v-else templates can emit Vue fragment comment
    placeholders that confuse ``h-full`` / flex sizing in deeply-nested
    split trees (the "split has a hole" bug). The outer div is ALWAYS
    ``h-full + overflow-hidden`` so percentage height propagates; the
    flex-direction class only matters in the split branch.
  -->
  <div ref="containerEl" class="h-full w-full overflow-hidden" :class="node.type === 'split' ? (node.direction === 'horizontal' ? 'flex flex-row' : 'flex flex-col') : 'flex flex-col'">
    <template v-if="node.type === 'leaf'">
      <!-- flex-1 + min-h-0/min-w-0 so the leaf's own flex-col can shrink
           correctly inside deep nested splits. -->
      <div class="flex-1 min-h-0 min-w-0">
        <slot :id="node.id" name="leaf" :path="path" />
      </div>
    </template>

    <template v-else-if="node.type === 'split'">
      <!--
        ``overflow-hidden`` + explicit style width/height % only. NO
        ``min-h-0`` / ``min-w-0`` here — flex-shrink (1 by default)
        would otherwise collapse the wrapper below its stated %,
        producing a "hole" inside the split.
      -->
      <div class="overflow-hidden" :style="firstStyle">
        <SplitTreeNode :node="node.children[0]" :path="[...path, 0]" :on-set-ratio="onSetRatio">
          <template #leaf="slotProps"><slot name="leaf" v-bind="slotProps" /></template>
        </SplitTreeNode>
      </div>
      <div class="layout-split__handle shrink-0" :class="handleClass" :style="{ background: dragging ? 'var(--color-iolite, #6366f1)' : '' }" @pointerdown.prevent="onPointerDown" />
      <div class="overflow-hidden" :style="secondStyle">
        <SplitTreeNode :node="node.children[1]" :path="[...path, 1]" :on-set-ratio="onSetRatio">
          <template #leaf="slotProps"><slot name="leaf" v-bind="slotProps" /></template>
        </SplitTreeNode>
      </div>
    </template>
  </div>
</template>

<script setup>
import { useSplitResize } from "@/composables/useSplitResize"

// Named for recursive self-reference (Vue resolves the SFC by filename
// for recursion, but the explicit name is the documented, safe form).
defineOptions({ name: "SplitTreeNode" })

const props = defineProps({
  node: { type: Object, required: true },
  path: { type: Array, default: () => [] },
  // (path: number[], ratio: number) => void — called on every drag tick
  // for the split at THIS node's path.
  onSetRatio: { type: Function, default: null },
})

// Pass ``getNode`` (a getter), NOT a destructured ``node`` value —
// Vue reuses the same SplitTreeNode instance when a leaf becomes a
// split (setup does not re-run), so a frozen value would apply the
// wrong axis. See ``useSplitResize`` doc-comment for the full hazard.
const { containerEl, dragging, firstStyle, secondStyle, handleClass, onPointerDown } = useSplitResize({
  getNode: () => props.node,
  onChange: (ratio) => props.onSetRatio?.(props.path, ratio),
})
</script>

<style scoped>
.layout-split__handle {
  transition: background 0.15s ease;
  touch-action: none;
  z-index: 1;
}
</style>
