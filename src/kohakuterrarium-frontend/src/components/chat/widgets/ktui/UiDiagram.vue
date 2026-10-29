<template>
  <!-- kt.ui diagram: Mermaid source drawn as SVG; the source shows while loading or when it cannot be drawn. -->
  <div class="ktui-diagram" data-test="ktui-diagram">
    <div v-if="title" class="ktui-section-title">{{ title }}</div>
    <!-- eslint-disable-next-line vue/no-v-html -->
    <div v-if="svg" class="ktui-diagram-svg" data-test="ktui-diagram-svg" v-html="svg" />
    <template v-else>
      <pre class="ktui-code"><code>{{ source }}</code></pre>
      <div v-if="error" class="ktui-note" data-test="ktui-diagram-error">diagram could not be drawn: {{ error }}</div>
    </template>
  </div>
</template>

<script setup>
import { ref, watch } from "vue"

import { cachedMermaid, pageTheme, renderMermaid } from "../../../../public/chat/mermaidRender.js"

const props = defineProps({
  source: { type: String, default: "" },
  title: { type: String, default: "" },
})

const svg = ref(cachedMermaid(props.source, pageTheme(globalThis.document)))
const error = ref("")
let current = 0

async function draw(source) {
  const ticket = ++current
  error.value = ""
  svg.value = cachedMermaid(source, pageTheme(globalThis.document))
  if (svg.value || !source.trim()) return
  try {
    const out = await renderMermaid(source, globalThis.document)
    if (ticket === current) svg.value = out
  } catch (err) {
    if (ticket === current) error.value = err?.message || String(err)
  }
}

watch(() => props.source, draw, { immediate: true })
</script>
