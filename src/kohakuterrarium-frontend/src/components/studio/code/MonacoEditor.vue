<template>
  <div ref="containerEl" class="w-full h-full" />
</template>

<script setup>
import { onMounted, onUnmounted, ref, watch } from "vue"

import { useThemeStore } from "@/stores/theme"

/**
 * Studio-local Monaco wrapper.
 *
 * Deliberately a fresh component, not an import of the runner's
 * MonacoEditor — studio wants different defaults (body-only mode,
 * readonly toggle, studio-tuned language dispatch) and the isolation
 * contract forbids cross-tree imports.
 *
 * Languages: "python" | "yaml" | "json" | "markdown" | "plaintext"
 * — anything Monaco recognises works. We let the caller pass the
 * identifier directly and fall back to plaintext on miss.
 */

const props = defineProps({
  modelValue: { type: String, default: "" },
  language: { type: String, default: "plaintext" },
  readOnly: { type: Boolean, default: false },
  /** Hide the left gutter (line numbers + folding). Useful for tight embeds. */
  minimal: { type: Boolean, default: false },
  /** Autofocus after mount. */
  autofocus: { type: Boolean, default: false },
})

const emit = defineEmits(["update:modelValue", "save"])

const containerEl = ref(null)
const theme = useThemeStore()

let editor = null
let suppressEmit = false
let changeDebounce = null

onMounted(async () => {
  const monaco = await import("monaco-editor")

  editor = monaco.editor.create(containerEl.value, {
    value: props.modelValue,
    language: mapLanguage(props.language),
    theme: theme.dark ? "vs-dark" : "vs",
    automaticLayout: true,
    minimap: { enabled: false },
    fontSize: 13,
    lineNumbers: props.minimal ? "off" : "on",
    folding: !props.minimal,
    glyphMargin: !props.minimal,
    lineDecorationsWidth: props.minimal ? 0 : 10,
    scrollBeyondLastLine: false,
    wordWrap: "on",
    tabSize: 4,
    insertSpaces: true,
    renderWhitespace: "selection",
    bracketPairColorization: { enabled: true },
    readOnly: props.readOnly,
    contextmenu: true,
  })

  editor.onDidChangeModelContent(() => {
    if (suppressEmit) return
    if (changeDebounce) clearTimeout(changeDebounce)
    changeDebounce = setTimeout(() => {
      emit("update:modelValue", editor.getValue())
    }, 180)
  })

  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
    emit("save")
  })

  if (props.autofocus) editor.focus()
})

watch(
  () => props.modelValue,
  (next) => {
    if (!editor) return
    const current = editor.getValue()
    if (current === next) return
    suppressEmit = true
    editor.setValue(next ?? "")
    suppressEmit = false
  },
)

watch(
  () => props.language,
  async (next) => {
    if (!editor) return
    const monaco = await import("monaco-editor")
    const model = editor.getModel()
    if (model) monaco.editor.setModelLanguage(model, mapLanguage(next))
  },
)

watch(
  () => props.readOnly,
  (next) => {
    if (editor) editor.updateOptions({ readOnly: !!next })
  },
)

watch(
  () => theme.dark,
  async (dark) => {
    const monaco = await import("monaco-editor")
    monaco.editor.setTheme(dark ? "vs-dark" : "vs")
  },
)

onUnmounted(() => {
  if (changeDebounce) clearTimeout(changeDebounce)
  if (editor) {
    editor.dispose()
    editor = null
  }
})

function mapLanguage(lang) {
  if (!lang) return "plaintext"
  const l = String(lang).toLowerCase()
  if (l === "py") return "python"
  if (l === "yml") return "yaml"
  if (l === "md") return "markdown"
  return l
}
</script>
