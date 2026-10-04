<template>
  <div ref="containerEl" class="w-full h-full" />
</template>

<script setup>
import { useThemeStore } from "@/stores/theme"

const props = defineProps({
  buffer: { type: Object, default: null },
  filePath: { type: String, default: "" },
  content: { type: String, default: "" },
  language: { type: String, default: "" },
})

const emit = defineEmits(["edit", "change", "save"])

const containerEl = ref(null)
const theme = useThemeStore()

let editor = null
let changeTimeout = null
let loadedBuffer = null
let loadedPath = ""
let suppressChange = false
let disposed = false

function flushChange() {
  if (changeTimeout === null) return
  clearTimeout(changeTimeout)
  changeTimeout = null
  if (editor) emit("change", editor.getValue(), loadedBuffer, loadedPath)
}

function replaceContent(content) {
  if (changeTimeout !== null) clearTimeout(changeTimeout)
  changeTimeout = null
  suppressChange = true
  try {
    editor.setValue(content)
  } finally {
    suppressChange = false
  }
}

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
  if (disposed) return
  loadedBuffer = props.buffer
  loadedPath = props.filePath

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
    if (suppressChange) return
    emit("edit", loadedBuffer, loadedPath)
    if (changeTimeout !== null) clearTimeout(changeTimeout)
    changeTimeout = setTimeout(flushChange, 300)
  })

  // Ctrl+S / Cmd+S save
  editor.addCommand(monaco.KeyMod.CtrlCmd | monaco.KeyCode.KeyS, () => {
    flushChange()
    emit("save", loadedBuffer, loadedPath)
  })
})

// Watch filePath changes -> update content
watch(
  () => [props.filePath, props.buffer],
  () => {
    if (editor && props.content !== undefined) {
      flushChange()
      loadedBuffer = props.buffer
      loadedPath = props.filePath
      const model = editor.getModel()
      if (model) {
        replaceContent(props.content)
        const language = mapLanguage(props.language)
        import("monaco-editor").then((m) => {
          if (!disposed && editor?.getModel() === model) m.editor.setModelLanguage(model, language)
        })
      }
    }
  },
)

// Watch content prop for external updates (e.g., revert)
watch(
  () => props.content,
  (newContent) => {
    if (editor && newContent !== editor.getValue()) {
      replaceContent(newContent)
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

onBeforeUnmount(() => {
  disposed = true
  flushChange()
  if (editor) {
    editor.dispose()
    editor = null
  }
})
</script>
