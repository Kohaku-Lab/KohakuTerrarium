<template>
  <!--
    Single root so the parent layout sees one definite child element.
    Multi-root v-if/v-else-if templates can render Vue fragment
    comment placeholders that confuse h-full / flex-row sizing in
    deeply-nested split trees (the ``up | bottom then split up into
    left | right → upper portion shrinks with a hole`` bug).

    The outer div ALWAYS has h-full + overflow-hidden so percentage
    height propagation is reliable. The flex-direction class only
    applies in the split branch.
  -->
  <div ref="containerEl" class="h-full w-full overflow-hidden" :class="node.type === 'split' ? (node.direction === 'horizontal' ? 'flex flex-row' : 'flex flex-col') : 'flex flex-col'">
    <!-- Leaf: one ChatPanel for this group. Wrap in flex-1 min-h-0
         so the panel's internal flex-col can shrink correctly inside
         deep nested splits (mirrors LayoutNode's leaf pattern). -->
    <template v-if="node.type === 'leaf'">
      <div class="flex-1 min-h-0 min-w-0">
        <ChatPanel :group-id="node.groupId" :instance="instance" :read-only="readOnly" :empty-title="emptyTitle" :empty-subtitle="emptySubtitle" />
      </div>
    </template>

    <!-- Split: two child sub-trees + draggable resize handle. The
         handle math comes from the shared ``useSplitResize`` composable
         (same shape that ``LayoutNode.vue`` uses for the workspace
         layout tree). The store action is ``setGroupSplitRatio(path,
         ratio)`` and ``path`` is an array of child indices encoding the
         location of THIS split inside the chat-internal tree. -->
    <template v-else-if="node.type === 'split'">
      <!--
        ``overflow-hidden`` + explicit style width/height % only. NO
        ``min-h-0`` / ``min-w-0`` — those would let flex-shrink (1 by
        default) collapse the wrapper below its stated 50%, producing
        a "hole" inside the split. LayoutNode's split wrappers use
        the exact same pattern and don't shrink, so we mirror them.
      -->
      <div class="overflow-hidden" :style="firstStyle">
        <ChatGroupNode :node="node.children[0]" :path="[...path, 0]" :instance="instance" :read-only="readOnly" :empty-title="emptyTitle" :empty-subtitle="emptySubtitle" />
      </div>
      <div class="layout-split__handle shrink-0" :class="handleClass" :style="{ background: dragging ? 'var(--color-iolite, #6366f1)' : '' }" @pointerdown.prevent="onPointerDown" />
      <div class="overflow-hidden" :style="secondStyle">
        <ChatGroupNode :node="node.children[1]" :path="[...path, 1]" :instance="instance" :read-only="readOnly" :empty-title="emptyTitle" :empty-subtitle="emptySubtitle" />
      </div>
    </template>
  </div>
</template>

<script setup>
import { inject } from "vue"

import ChatPanel from "@/components/chat/ChatPanel.vue"
import { useSplitResize } from "@/composables/useSplitResize"

const props = defineProps({
  node: { type: Object, required: true },
  path: { type: Array, default: () => [] },
  instance: { type: Object, default: null },
  readOnly: { type: Boolean, default: false },
  emptyTitle: { type: String, default: "" },
  emptySubtitle: { type: String, default: "" },
})

// Inject the same chat store the container resolved at the top of
// the slot. Recursive ``ChatGroupNode`` instances each call inject
// — Vue walks up to the same provider every time.
const chat = inject("chatStore", null)

// Pass ``getNode`` (a getter) NOT ``node`` (a destructured value).
// Vue 3 reuses the same ChatGroupNode instance when a leaf becomes
// a split — setup doesn't re-run, so a destructured ``node`` value
// would freeze on the original (direction=undefined) leaf and the
// computed styles would silently apply the vertical-fallback
// ``{ height: 50%, width: 100% }`` to a horizontal split. See
// ``useSplitResize`` doc-comment for the full hazard write-up.
const { containerEl, dragging, firstStyle, secondStyle, handleClass, onPointerDown } = useSplitResize({
  getNode: () => props.node,
  onChange: (ratio) => chat?.setGroupSplitRatio(props.path, ratio),
})
</script>

<style scoped>
.layout-split__handle {
  transition: background 0.15s ease;
  touch-action: none;
  z-index: 1;
}
</style>
