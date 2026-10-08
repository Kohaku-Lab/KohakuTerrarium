<template>
  <div class="h-full min-h-0 overflow-auto" :class="viewer === 'image' ? 'flex items-center justify-center bg-warm-100 dark:bg-warm-950 p-4' : ''">
    <img v-if="viewer === 'image'" :src="artifact.content" :alt="artifact.name" class="max-w-full max-h-full object-contain rounded shadow-sm" />
    <iframe v-else-if="viewer === 'html' && !source" :srcdoc="artifact.content" sandbox="" class="w-full h-full border-0 bg-white" />
    <div v-else-if="viewer === 'markdown' && !source" class="px-5 py-4 text-sm text-warm-800 dark:text-warm-200"><MarkdownRenderer :content="artifact.content" /></div>
    <div v-else class="px-3 py-2 text-[12px]"><MarkdownRenderer :content="fenced" /></div>
  </div>
</template>

<script setup>
import { computed } from "vue"

import { MarkdownRenderer } from "@kohakuterrarium/chat-ui"
import { artifactViewer } from "@/components/session-v2/model/widgets/widgetData"

/** One canvas artifact at full size: image, sandboxed HTML (no scripts), rendered markdown, or highlighted code. */
const props = defineProps({
  artifact: { type: Object, required: true },
  source: { type: Boolean, default: false },
})

const viewer = computed(() => artifactViewer(props.artifact))
// Code goes through the chat markdown renderer as one fenced block, for its highlighting.
const fenced = computed(() => {
  const body = String(props.artifact.content || "")
  const fence = body.includes("```") ? "~~~~" : "```"
  const lang = viewer.value === "code" ? props.artifact.lang || "" : props.artifact.type === "html" ? "html" : "markdown"
  return `${fence}${lang}\n${body}\n${fence}`
})
</script>
