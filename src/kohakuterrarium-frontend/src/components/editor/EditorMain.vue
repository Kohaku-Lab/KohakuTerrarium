<template>
  <div class="h-full flex flex-col">
    <!-- File tabs + mode toggle -->
    <div
      v-if="editor.openFilePaths.length > 0"
      class="flex items-center gap-0.5 px-2 h-7 border-b border-warm-200 dark:border-warm-700 overflow-x-auto shrink-0"
    >
      <div
        v-for="filePath in editor.openFilePaths"
        :key="filePath"
        class="flex items-center gap-1 px-2 py-0.5 rounded text-[11px] cursor-pointer select-none transition-colors max-w-40 shrink-0"
        :class="editor.activeFilePath === filePath
          ? 'bg-iolite/10 dark:bg-iolite/15 text-iolite dark:text-iolite-light'
          : 'text-warm-400 hover:text-warm-600 dark:hover:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-700'"
        @click="editor.activeFilePath = filePath"
      >
        <span
          v-if="editor.openFiles[filePath]?.dirty"
          class="w-1.5 h-1.5 rounded-full bg-amber shrink-0"
        />
        <span class="truncate">{{ fileName(filePath) }}</span>
        <button
          class="ml-0.5 w-3.5 h-3.5 flex items-center justify-center rounded-sm text-warm-400 hover:text-warm-600 dark:hover:text-warm-300"
          @click.stop="editor.closeFile(filePath)"
        >
          <div class="i-carbon-close text-[9px]" />
        </button>
      </div>

      <div class="flex-1" />

      <!-- Mode toggle for markdown files -->
      <button
        v-if="isMarkdown"
        class="flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] transition-colors shrink-0"
        :class="useVditor
          ? 'bg-iolite/15 text-iolite'
          : 'text-warm-400 hover:text-warm-600 dark:hover:text-warm-300'"
        :title="useVditor ? 'Switch to code editor' : 'Switch to rich markdown editor'"
        @click="useVditor = !useVditor"
      >
        <span :class="useVditor ? 'i-carbon-document' : 'i-carbon-code'" class="text-[11px]" />
        <span>{{ useVditor ? 'Rich' : 'Code' }}</span>
      </button>
    </div>

    <!-- Editor body -->
    <div class="flex-1 min-h-0">
      <template v-if="editor.activeFile">
        <!-- Vditor for markdown when toggled on -->
        <VditorEditor
          v-if="isMarkdown && useVditor"
          :file-path="editor.activeFilePath"
          :content="editor.activeFile.content"
          @change="onChange"
          @save="onSave"
        />
        <!-- Monaco for everything else -->
        <MonacoEditor
          v-else
          :file-path="editor.activeFilePath"
          :content="editor.activeFile.content"
          :language="editor.activeFile.language"
          @change="onChange"
          @save="onSave"
        />
      </template>
      <div
        v-else
        class="h-full flex items-center justify-center text-warm-400 text-sm"
      >
        <div class="text-center">
          <div class="i-carbon-document text-3xl mb-2 mx-auto opacity-30" />
          <p>Select a file to edit</p>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from "vue";

import MonacoEditor from "@/components/editor/MonacoEditor.vue";
import VditorEditor from "@/components/editor/VditorEditor.vue";
import { useEditorStore } from "@/stores/editor";

const editor = useEditorStore();

// Per-file mode preference (persists while files are open).
const vditorFiles = ref(new Set());
const useVditor = computed({
  get: () => vditorFiles.value.has(editor.activeFilePath),
  set: (val) => {
    const next = new Set(vditorFiles.value);
    if (val) next.add(editor.activeFilePath);
    else next.delete(editor.activeFilePath);
    vditorFiles.value = next;
  },
});

const isMarkdown = computed(() => {
  const p = editor.activeFilePath || "";
  return /\.(md|markdown|mdx)$/i.test(p);
});

function fileName(path) {
  return path.split("/").pop() || path.split("\\").pop() || path;
}

function onChange(content) {
  if (editor.activeFilePath) {
    editor.updateContent(editor.activeFilePath, content);
  }
}

function onSave() {
  if (editor.activeFilePath) {
    editor.saveFile(editor.activeFilePath);
  }
}
</script>
