<template>
  <div ref="containerEl" class="w-full h-full" data-test="v2-diff-view" />
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue"

import { useThemeStore } from "@/stores/theme"

/** Read-only side-by-side diff of two texts (Monaco's diff editor, loaded on mount). */
const props = defineProps({
  original: { type: String, default: "" },
  modified: { type: String, default: "" },
  language: { type: String, default: "plaintext" },
})

const containerEl = ref(null)
const theme = useThemeStore()
const monacoTheme = computed(() => (theme.dark ? "vs-dark" : "vs"))
let monacoApi = null
let diffEditor = null
let models = []
let disposed = false

function setModels() {
  if (!monacoApi || !diffEditor) return
  const next = [monacoApi.editor.createModel(props.original, props.language), monacoApi.editor.createModel(props.modified, props.language)]
  diffEditor.setModel({ original: next[0], modified: next[1] })
  for (const model of models) model.dispose()
  models = next
}

onMounted(async () => {
  const monaco = await import("monaco-editor")
  if (disposed) return
  monacoApi = monaco
  diffEditor = monaco.editor.createDiffEditor(containerEl.value, {
    readOnly: true,
    automaticLayout: true,
    renderSideBySide: true,
    minimap: { enabled: false },
    fontSize: 13,
    scrollBeyondLastLine: false,
    theme: monacoTheme.value,
  })
  setModels()
})

watch(() => [props.original, props.modified, props.language], setModels)
watch(monacoTheme, (name) => monacoApi?.editor.setTheme(name))

onBeforeUnmount(() => {
  disposed = true
  diffEditor?.dispose()
  for (const model of models) model.dispose()
  models = []
})
</script>
