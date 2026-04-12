<template>
  <div class="h-full w-full overflow-auto bg-warm-50 dark:bg-warm-950 flex">
    <!-- Line numbers -->
    <div
      class="code-lines shrink-0 py-3 pr-2 text-right select-none border-r border-warm-200/50 dark:border-warm-700/50 bg-warm-100/50 dark:bg-warm-900/50"
    >
      <div
        v-for="n in lineCount"
        :key="n"
        class="px-2 text-[11px] font-mono leading-[1.4] text-warm-400"
      >
        {{ n }}
      </div>
    </div>
    <!-- Code content -->
    <pre
      class="flex-1 m-0 py-3 pl-3 pr-3 text-[11px] font-mono text-warm-700 dark:text-warm-300 whitespace-pre leading-[1.4]"
    ><code v-html="highlighted" /></pre>
  </div>
</template>

<script setup>
import { computed } from "vue"
import hljs from "highlight.js/lib/core"

import bash from "highlight.js/lib/languages/bash"
import css from "highlight.js/lib/languages/css"
import javascript from "highlight.js/lib/languages/javascript"
import json from "highlight.js/lib/languages/json"
import markdown from "highlight.js/lib/languages/markdown"
import python from "highlight.js/lib/languages/python"
import rust from "highlight.js/lib/languages/rust"
import sql from "highlight.js/lib/languages/sql"
import typescript from "highlight.js/lib/languages/typescript"
import xml from "highlight.js/lib/languages/xml"
import yaml from "highlight.js/lib/languages/yaml"

const LANG_MAP = {
  bash,
  sh: bash,
  shell: bash,
  css,
  js: javascript,
  javascript,
  ts: typescript,
  typescript,
  json,
  md: markdown,
  markdown,
  py: python,
  python,
  rs: rust,
  rust,
  sql,
  xml,
  html: xml,
  svg: xml,
  yaml,
  yml: yaml,
}

for (const [name, lang] of Object.entries(LANG_MAP)) {
  try {
    hljs.registerLanguage(name, lang)
  } catch {
    /* skip */
  }
}

import "highlight.js/styles/github-dark.css"

const props = defineProps({
  content: { type: String, default: "" },
  lang: { type: String, default: "text" },
})

const lineCount = computed(() => {
  if (!props.content) return 1
  return props.content.split("\n").length
})

const highlighted = computed(() => {
  const lang = (props.lang || "").toLowerCase()
  if (LANG_MAP[lang]) {
    try {
      return hljs.highlight(props.content, { language: lang }).value
    } catch {
      return escapeHtml(props.content)
    }
  }
  return escapeHtml(props.content)
})

function escapeHtml(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
}
</script>
