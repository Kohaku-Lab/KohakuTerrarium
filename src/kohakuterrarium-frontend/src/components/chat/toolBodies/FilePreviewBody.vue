<template>
  <div class="min-w-0" data-test="file-preview-body">
    <div v-if="preview" class="border-b border-sapphire/15 dark:border-sapphire/20">
      <div class="flex items-center gap-2 px-3 py-1 text-[10px] text-warm-500 font-mono">
        <span class="i-carbon-document text-[11px]" />
        <span class="truncate" :title="preview.file_path">{{ preview.file_path }}</span>
      </div>
      <div v-if="preview.content == null" class="px-3 pb-2 text-[11px] text-warm-400" data-test="file-preview-too-large">The file is too large to preview here.</div>
      <div v-else class="px-2 pb-2 max-h-80 overflow-auto text-[12px]" data-test="file-preview-content"><MarkdownRenderer :content="fenced" /></div>
    </div>
    <div class="text-xs font-mono px-3 py-2 text-warm-500 dark:text-warm-400 whitespace-pre-wrap max-h-64 overflow-y-auto bg-sapphire/4 dark:bg-sapphire/6 break-all" data-test="file-preview-output">{{ output }}</div>
  </div>
</template>

<script setup>
import { computed } from "vue"

import MarkdownRenderer from "../../../public/chat/MarkdownRenderer.vue"

/** Built-in body for write / edit / multi_edit: the new file content, then the tool's own output. */
const props = defineProps({ call: { type: Object, required: true } })

const preview = computed(() => {
  const p = props.call.resultMeta?.canvas_preview
  return p && p.kind !== "image" && p.file_path ? p : null
})
const fenced = computed(() => {
  const body = String(preview.value?.content ?? "")
  const fence = body.includes("```") ? "~~~~" : "```"
  return `${fence}${preview.value?.lang || ""}\n${body}\n${fence}`
})
const output = computed(() => {
  const parts = Array.isArray(props.call.resultParts) ? props.call.resultParts : []
  const text = parts
    .filter((part) => part?.type === "text")
    .map((part) => part.text || "")
    .join("\n")
  return text || props.call.result || "(no output)"
})
</script>
