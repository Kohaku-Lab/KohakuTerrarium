<template>
  <div ref="containerEl" class="w-full h-full" />
</template>

<script setup>
import { useThemeStore } from "@/stores/theme"

const props = defineProps({
  filePath: { type: String, default: "" },
  content: { type: String, default: "" },
  language: { type: String, default: "" },
})

const emit = defineEmits(["change", "save"])

const containerEl = ref(null)
const theme = useThemeStore()

let editor = null
let changeTimeout = null

const monacoTheme = computed(() => (theme.dark ? "vs-dark" : "vs"))

/** Map common language identifiers to Monaco language IDs */
function mapLanguage(lang) {
  const map = {
    py: "python",
    python: "python",
    js: "javascript",
    javascript: "javascript",
    ts: "typescript",
    typescript: "typescript",
    jsx: "javascript",
    tsx: "typescript",
    md: "markdown",
    markdown: "markdown",
    json: "json",
    yaml: "yaml",
    yml: "yaml",
    toml: "ini",
    html: "html",
    css: "css",
    scss: "scss",
    vue: "html",
    sh: "shell",
    bash: "shell",
    rs: "rust",
    go: "go",
    java: "java",
    cpp: "cpp",
    c: "c",
    rb: "ruby",
    xml: "xml",
    sql: "sql",
    dockerfile: "dockerfile",
  }
  return map[lang?.toLowerCase()] || lang || "plaintext"
}

onMounted(async () => {
  const monaco = await import("monaco-editor")

  editor = monaco.editor.create(containerEl.value, {
    value: props.content,
    language: mapLanguage(props.language),
    theme: monacoTheme.value,
    automaticLayout: true,
    minimap: { enabled: false },
    fontSize: 13,
    lineNumbers: "on",
    scrollBeyondLastLine: false,
    wordWrap: "on",
    tabSize: 2,
    renderWhitespace: "selection",
    bracketPairColorization: { enabled: true },
  })

  // Content change with debounce
  editor.onDidChangeModelContent(() => {
    if (changeTimeout) clearTimeout(changeTimeout)
    changeTimeout = setTimeout(() => {
      emit("change", editor.getValue())
    }, 300)
  })

  // Ctrl+S / Cmd+S save
  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
    emit("save")
  })
})

// Watch filePath changes -> update content
watch(
  () => props.filePath,
  () => {
    if (editor && props.content !== undefined) {
      const model = editor.getModel()
      if (model) {
        model.setValue(props.content)
        const monaco = window.monaco
        if (monaco) {
          // Try to set language from import cache (dynamic import already loaded)
          import("monaco-editor").then((m) => {
            m.editor.setModelLanguage(model, mapLanguage(props.language))
          })
        }
      }
    }
  },
)

// Watch content prop for external updates (e.g., revert)
watch(
  () => props.content,
  (newContent) => {
    if (editor && newContent !== editor.getValue()) {
      editor.setValue(newContent)
    }
  },
)

// Watch theme changes
watch(monacoTheme, (newTheme) => {
  if (editor) {
    import("monaco-editor").then((m) => {
      m.editor.setTheme(newTheme)
    })
  }
})

onUnmounted(() => {
  if (changeTimeout) clearTimeout(changeTimeout)
  if (editor) {
    editor.dispose()
    editor = null
  }
})
</script>
