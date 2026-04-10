<template>
  <div ref="editorEl" class="h-full w-full overflow-hidden" />
</template>

<script setup>
import { onMounted, onUnmounted, ref, watch } from "vue";
import Vditor from "vditor";
import "vditor/dist/index.css";

import { useThemeStore } from "@/stores/theme";

const props = defineProps({
  content: { type: String, default: "" },
  filePath: { type: String, default: "" },
});

const emit = defineEmits(["change", "save"]);

const theme = useThemeStore();
const editorEl = ref(null);
let vd = null;
let suppressChange = false;

onMounted(() => {
  if (!editorEl.value) return;

  vd = new Vditor(editorEl.value, {
    mode: "ir", // instant rendering (WYSIWYG-ish)
    value: props.content,
    height: "100%",
    toolbarConfig: { pin: true },
    toolbar: [
      "headings", "bold", "italic", "strike", "|",
      "list", "ordered-list", "check", "|",
      "quote", "code", "inline-code", "|",
      "link", "table", "|",
      "undo", "redo", "|",
      "edit-mode", "outline", "fullscreen",
    ],
    cache: { enable: false },
    theme: theme.dark ? "dark" : "classic",
    preview: {
      theme: { current: theme.dark ? "dark" : "light" },
      hljs: { lineNumber: true },
      math: { engine: "KaTeX" },
    },
    input: (value) => {
      if (!suppressChange) {
        emit("change", value);
      }
    },
    ctrlEnter: () => {
      emit("save");
    },
    after: () => {
      vd?.focus();
    },
  });

  // Ctrl+S to save (Vditor doesn't have a native hook for this).
  editorEl.value.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "s") {
      e.preventDefault();
      emit("save");
    }
  });
});

// Sync external content changes (e.g. file revert).
watch(
  () => props.content,
  (newVal) => {
    if (!vd) return;
    const current = vd.getValue();
    if (current !== newVal) {
      suppressChange = true;
      vd.setValue(newVal);
      suppressChange = false;
    }
  },
);

// React to theme toggle.
watch(
  () => theme.dark,
  (dark) => {
    if (vd) {
      vd.setTheme(dark ? "dark" : "classic", dark ? "dark" : "light");
    }
  },
);

onUnmounted(() => {
  if (vd) {
    vd.destroy();
    vd = null;
  }
});
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
.vditor-ir pre.vditor-reset h1 { font-size: 1.4em !important; }
.vditor-ir pre.vditor-reset h2 { font-size: 1.25em !important; }
.vditor-ir pre.vditor-reset h3 { font-size: 1.1em !important; }
.vditor-toolbar {
  font-size: 12px !important;
  padding: 2px 4px !important;
}
.vditor-toolbar__item {
  padding: 2px 3px !important;
}
</style>
