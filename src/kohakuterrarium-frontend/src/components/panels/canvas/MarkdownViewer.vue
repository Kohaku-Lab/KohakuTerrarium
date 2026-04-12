<template>
  <div class="h-full w-full flex flex-col bg-white dark:bg-warm-900">
    <div
      class="flex items-center gap-2 px-2 py-1 border-b border-warm-200 dark:border-warm-700 text-[10px] shrink-0"
    >
      <button
        class="px-2 py-0.5 rounded transition-colors"
        :class="
          mode === 'preview' ? 'bg-iolite/15 text-iolite' : 'text-warm-500 hover:text-warm-700'
        "
        @click="mode = 'preview'"
      >
        Preview
      </button>
      <button
        class="px-2 py-0.5 rounded transition-colors"
        :class="mode === 'raw' ? 'bg-iolite/15 text-iolite' : 'text-warm-500 hover:text-warm-700'"
        @click="mode = 'raw'"
      >
        Raw
      </button>
    </div>

    <div class="flex-1 min-h-0 overflow-auto p-4 text-xs">
      <div v-if="mode === 'preview'" class="markdown-body" v-html="rendered" />
      <pre
        v-else
        class="font-mono text-[11px] whitespace-pre-wrap break-words text-warm-700 dark:text-warm-300"
        >{{ content }}</pre
      >
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from "vue"
import MarkdownIt from "markdown-it"

const props = defineProps({
  content: { type: String, default: "" },
})

const mode = ref("preview")

const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
})

const rendered = computed(() => md.render(props.content || ""))
</script>

<style scoped>
.markdown-body :deep(h1) {
  font-size: 1.25rem;
  font-weight: 600;
  margin: 0.5rem 0;
}
.markdown-body :deep(h2) {
  font-size: 1.1rem;
  font-weight: 600;
  margin: 0.5rem 0;
}
.markdown-body :deep(h3) {
  font-size: 1rem;
  font-weight: 600;
  margin: 0.5rem 0;
}
.markdown-body :deep(p) {
  margin: 0.25rem 0;
}
.markdown-body :deep(code) {
  background: rgba(120, 120, 120, 0.15);
  padding: 0 0.2rem;
  border-radius: 2px;
  font-family: monospace;
  font-size: 0.9em;
}
.markdown-body :deep(pre) {
  background: rgba(120, 120, 120, 0.1);
  padding: 0.5rem;
  border-radius: 4px;
  overflow-x: auto;
  font-size: 0.85em;
}
.markdown-body :deep(ul),
.markdown-body :deep(ol) {
  padding-left: 1.25rem;
  margin: 0.25rem 0;
}
.markdown-body :deep(blockquote) {
  border-left: 3px solid rgba(120, 120, 120, 0.3);
  padding-left: 0.75rem;
  margin: 0.5rem 0;
  color: rgba(120, 120, 120, 0.9);
}
</style>
