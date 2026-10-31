<template>
  <!-- Assistant text: Markdown, with each inline <ui> block drawn as a kt.ui tree. -->
  <MarkdownRenderer v-if="!segments" :content="content" :breaks="breaks" :origin="origin" />
  <div v-else class="kt-assistant-text" data-test="assistant-text">
    <template v-for="(segment, i) in segments" :key="`${i}:${segment.kind}`">
      <MarkdownRenderer v-if="segment.kind === 'markdown'" :content="segment.text" :breaks="breaks" :origin="origin" />
      <ExtensionFenceBlock v-else-if="segment.kind === 'fence'" :id="segment.id" :source="segment.source" @say="(text) => emit('say', text)" />
      <InlineUiBlock v-else :source="segment.source" :closed="segment.closed" :streaming="streaming" :can-open-canvas="canOpenCanvas" :binding="bindingFor(blockIds[i])" :say-blocked="sayBlocked" @say="(text) => emit('say', text)" @open-canvas="(payload) => emit('open-canvas', payload)" />
    </template>
  </div>
</template>

<script setup>
import { computed } from "vue"

import ExtensionFenceBlock from "@/ext/ExtensionFenceBlock.vue"
import { splitFences } from "@/ext/fenceSplit"
import { renderersIn } from "@/ext/messageRenderers"
import { blockIdsFor } from "../widgets/ktui/blockId.js"
import InlineUiBlock from "../widgets/ktui/InlineUiBlock.vue"
import { mayHaveMarkup, splitMarkup } from "../widgets/ktui/markup.js"
import MarkdownRenderer from "../../../public/chat/MarkdownRenderer.vue"

const props = defineProps({
  content: { type: String, default: "" },
  breaks: { type: Boolean, default: false },
  origin: { type: String, default: null },
  streaming: { type: Boolean, default: false },
  canOpenCanvas: { type: Boolean, default: false },
  // The chat store and ``{tab, turn, branch}`` of this reply; without them block state stays local.
  uiStore: { type: Object, default: null },
  uiAddress: { type: Object, default: null },
  // Set where a block's ``send`` must not post (for example a sub-agent view): the tooltip text.
  sayBlocked: { type: String, default: "" },
})

const emit = defineEmits(["say", "open-canvas"])

// Null keeps the plain single-renderer path for text without any <ui> block or extension fence.
const segments = computed(() => {
  const fenceIds = renderersIn(props.content)
  const hasMarkup = mayHaveMarkup(props.content)
  if (!hasMarkup && !fenceIds.length) return null
  let parts = hasMarkup ? splitMarkup(props.content, { holdPartial: props.streaming }) : [{ kind: "markdown", text: props.content }]
  if (fenceIds.length) parts = parts.flatMap((part) => (part.kind === "markdown" ? splitFences(part.text, fenceIds) : [part]))
  if (parts.length === 1 && parts[0].kind === "markdown" && parts[0].text === props.content) return null
  return parts
})

const blockIds = computed(() => blockIdsFor(segments.value || []))

/** State binding of one block: stored per reply when addressable, held until then. */
function bindingFor(block) {
  const store = props.uiStore
  const address = props.uiAddress
  if (!store || !address?.tab || !block) return null
  const { tab, turn, branch } = address
  const keyed = Number.isInteger(turn) && Number.isInteger(branch)
  return {
    key: keyed ? `${tab}|${turn}:${branch}:${block}` : `${tab}|${block}`,
    get state() {
      return keyed ? store.inlineUiStateOf(tab, turn, branch, block) : null
    },
    set(patch) {
      if (keyed) store.setInlineUiState(tab, turn, branch, block, patch)
      else store.holdInlineUiState(tab, block, patch)
    },
    claim() {
      if (keyed) store.claimInlineUiState(tab, turn, branch, block)
    },
  }
}
</script>
