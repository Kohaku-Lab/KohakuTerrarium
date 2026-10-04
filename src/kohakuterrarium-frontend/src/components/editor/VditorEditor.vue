<template>
  <div ref="editorEl" class="h-full w-full overflow-hidden" @input.capture="onInput" @keydown.capture="onKeydown" @click.capture="onClick" />
</template>

<script setup>
import { onBeforeUnmount, onMounted, ref, watch } from "vue"
import Vditor from "vditor"
import "vditor/dist/index.css"

import { useThemeStore } from "@/stores/theme"

const props = defineProps({
  buffer: { type: Object, default: null },
  content: { type: String, default: "" },
  filePath: { type: String, default: "" },
})

const emit = defineEmits(["edit", "change", "save"])

const theme = useThemeStore()
const editorEl = ref(null)
let vd = null
let suppressChange = false
let ready = false
let disposed = false
let loadedBuffer = props.buffer
let loadedPath = props.filePath
let syncedContent = props.content

function syncContent(value) {
  if (!ready || disposed || suppressChange || value === syncedContent) return
  syncedContent = value
  emit("change", value, loadedBuffer, loadedPath)
}

function flushChange() {
  if (ready && !disposed) syncContent(vd.getValue())
}

function replaceContent(content) {
  suppressChange = true
  try {
    vd.setValue(content)
    syncedContent = vd.getValue()
  } finally {
    suppressChange = false
  }
}

function onInput(event) {
  if (ready && !disposed && !suppressChange && event.target.closest?.('[contenteditable="true"]')) {
    emit("edit", loadedBuffer, loadedPath)
  }
}

function save() {
  if (!ready || disposed) return
  flushChange()
  emit("save", loadedBuffer, loadedPath)
}

function onKeydown(event) {
  if (!event.ctrlKey && !event.metaKey) return
  if (event.key.toLowerCase() === "s") {
    event.preventDefault()
    save()
  } else {
    // Vditor shortcuts can change the document without a DOM input event.
    Promise.resolve().then(() => {
      if (event.defaultPrevented) flushChange()
    })
  }
}

function onClick(event) {
  if (event.target.closest?.(".vditor-toolbar")) Promise.resolve().then(flushChange)
}

onMounted(() => {
  if (!editorEl.value) return
  const initialContent = props.content

  vd = new Vditor(editorEl.value, {
    mode: "ir", // instant rendering (WYSIWYG-ish)
    value: props.content,
    height: "100%",
    toolbarConfig: { pin: true },
    toolbar: ["headings", "bold", "italic", "strike", "|", "list", "ordered-list", "check", "|", "quote", "code", "inline-code", "|", "link", "table", "|", "undo", "redo", "|", "edit-mode", "outline", "fullscreen"],
    cache: { enable: false },
    theme: theme.dark ? "dark" : "classic",
    preview: {
      theme: { current: theme.dark ? "dark" : "light" },
      hljs: { lineNumber: true },
      math: { engine: "KaTeX" },
    },
    input: syncContent,
    ctrlEnter: save,
    after: () => {
      if (disposed) {
        vd.destroy()
        vd = null
        return
      }
      ready = true
      loadedBuffer = props.buffer
      loadedPath = props.filePath
      if (props.content !== initialContent) replaceContent(props.content)
      else syncedContent = vd.getValue()
      vd.focus()
    },
  })
})

// Sync external content changes (e.g. file revert).
watch(
  () => [props.content, props.buffer, props.filePath],
  ([newVal, buffer, path]) => {
    const changedBuffer = buffer !== loadedBuffer || path !== loadedPath
    if (changedBuffer) flushChange()
    loadedBuffer = buffer
    loadedPath = path
    if (ready && (changedBuffer || newVal !== syncedContent)) replaceContent(newVal)
  },
)

// React to theme toggle.
watch(
  () => theme.dark,
  (dark) => {
    if (ready && !disposed) {
      vd.setTheme(dark ? "dark" : "classic", dark ? "dark" : "light")
    }
  },
)

onBeforeUnmount(() => {
  flushChange()
  disposed = true
  if (ready) {
    vd.destroy()
    vd = null
  }
})
</script>

<style>
/* Override vditor to fill container */
.vditor {
  border: none !important;
  border-radius: 0 !important;
  font-size: 13px !important;
  max-width: 100% !important;
}
.vditor-ir pre.vditor-reset,
.vditor-sv pre.vditor-reset,
.vditor-wysiwyg pre.vditor-reset {
  font-size: 13px !important;
  line-height: 1.5 !important;
  padding: 8px 16px !important;
  word-wrap: break-word !important;
  overflow-wrap: break-word !important;
}
.vditor-ir pre.vditor-reset h1 {
  font-size: 1.4em !important;
}
.vditor-ir pre.vditor-reset h2 {
  font-size: 1.25em !important;
}
.vditor-ir pre.vditor-reset h3 {
  font-size: 1.1em !important;
}
.vditor-toolbar {
  font-size: 12px !important;
  padding: 2px 4px !important;
}
.vditor-toolbar__item {
  padding: 2px 3px !important;
}
</style>
