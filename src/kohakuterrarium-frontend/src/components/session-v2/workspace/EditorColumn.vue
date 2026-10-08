<template>
  <main class="h-full min-w-0 min-h-0 flex flex-col bg-white dark:bg-warm-950" data-test="v2-editor-column">
    <div class="h-9 shrink-0 flex items-center gap-0.5 px-2 border-b kt-v2-line overflow-x-auto scrollbar-none">
      <button v-for="path in editor.openFilePaths" :key="path" class="group flex items-center gap-1.5 h-7 px-2.5 rounded-md text-xs shrink-0 max-w-48" :class="!activeDiff && editor.activeFilePath === path ? 'bg-warm-100 dark:bg-warm-800 text-warm-800 dark:text-warm-100' : 'text-warm-500 hover:text-warm-800 dark:hover:text-warm-200'" :title="path" @click="selectFile(path)">
        <span v-if="editor.openFiles[path]?.dirty" class="w-1.5 h-1.5 rounded-full bg-amber shrink-0" />
        <span class="truncate">{{ baseName(path) }}</span>
        <span class="i-carbon-close text-[11px] text-warm-400 opacity-0 group-hover:opacity-100 hover:text-warm-700" :title="t('close')" @click.stop="closeFile(path)" />
      </button>
      <button v-for="d in diffs" :key="d.id" class="group flex items-center gap-1.5 h-7 px-2.5 rounded-md text-xs shrink-0 max-w-48" :class="activeDiff === d.id ? 'bg-iolite/10 text-iolite dark:text-iolite-light' : 'text-warm-500 hover:text-warm-800 dark:hover:text-warm-200'" :title="d.path" @click="activeDiff = d.id">
        <span class="i-carbon-compare shrink-0" />
        <span class="truncate">{{ baseName(d.path) }}</span>
        <span class="i-carbon-close text-[11px] text-warm-400 opacity-0 group-hover:opacity-100 hover:text-warm-700" :title="t('close')" @click.stop="closeDiff(d.id)" />
      </button>
      <span class="flex-1" />
      <template v-if="!activeDiff && editor.activeFile">
        <button v-if="isMarkdown" class="h-6 px-2 rounded text-[11px] flex items-center gap-1 shrink-0" :class="richMarkdown ? 'bg-iolite/15 text-iolite' : 'text-warm-500 hover:text-warm-800 dark:hover:text-warm-200'" @click="toggleRich"><span :class="richMarkdown ? 'i-carbon-document' : 'i-carbon-code'" />{{ richMarkdown ? t("ws.rich") : t("ws.code") }}</button>
        <button class="h-6 px-2 rounded text-[11px] text-warm-500 hover:text-warm-800 dark:hover:text-warm-200 disabled:opacity-40 shrink-0" :disabled="!editor.activeFile.dirty" @click="editor.revertFile(editor.activeFilePath)">{{ t("ws.revert") }}</button>
        <button class="h-6 px-2.5 rounded text-[11px] bg-iolite text-white disabled:opacity-40 shrink-0" :disabled="!editor.activeFile.dirty" @click="editor.saveFile(editor.activeFilePath)">{{ t("ws.save") }}</button>
      </template>
      <span v-else-if="currentDiff" class="text-[11px] text-warm-400 font-mono truncate max-w-72" :title="currentDiff.path">{{ currentDiff.path }}</span>
    </div>

    <div v-if="!activeDiff && editor.activeFile?.saveError" class="kt-v2-line shrink-0 flex items-center gap-2 px-3 py-1.5 border-b text-xs text-coral" role="alert">
      <span class="i-carbon-warning-alt shrink-0" />
      <span class="flex-1 min-w-0 truncate">{{ t("ws.saveFailed", { error: editor.activeFile.saveError }) }}</span>
    </div>
    <div v-if="!activeDiff && editor.activeFile?.conflict" class="kt-v2-line shrink-0 flex items-center gap-2 px-3 py-1.5 border-b text-xs bg-amber/10 text-amber-shadow dark:text-amber-light" role="alert">
      <span class="i-carbon-warning-alt shrink-0" />
      <span class="flex-1 min-w-0">{{ t("ws.conflict") }}</span>
      <button class="shrink-0 hover:underline" @click="editor.revertFile(editor.activeFilePath)">{{ t("ws.reload") }}</button>
    </div>
    <div class="flex-1 min-h-0">
      <DiffView v-if="currentDiff" :original="currentDiff.old" :modified="currentDiff.new" :language="languageOf(currentDiff.path)" />
      <template v-else-if="editor.activeFile">
        <VditorEditor v-if="isMarkdown && richMarkdown" :buffer="editor.activeFile" :file-path="editor.activeFilePath" :content="editor.activeFile.content" @edit="onEdit" @change="onChange" @save="onSave" />
        <MonacoEditor v-else :buffer="editor.activeFile" :file-path="editor.activeFilePath" :content="editor.activeFile.content" :language="editor.activeFile.language || languageOf(editor.activeFilePath)" @edit="onEdit" @change="onChange" @save="onSave" />
      </template>
      <div v-else class="h-full flex flex-col items-center justify-center gap-2 text-warm-400 text-sm">
        <span class="i-carbon-document text-3xl opacity-30" />
        <span>{{ editor.loading ? t("loading") : t("ws.pickFile") }}</span>
      </div>
    </div>
  </main>
</template>

<script setup>
import { ElMessageBox } from "element-plus"
import { computed, ref } from "vue"

import MonacoEditor from "@/components/editor/MonacoEditor.vue"
import VditorEditor from "@/components/editor/VditorEditor.vue"
import { baseName, languageOf } from "@/components/session-v2/model/workspace/languageOf"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import DiffView from "@/components/session-v2/workspace/DiffView.vue"
import { useEditorStore } from "@/stores/editor"

/**
 * The workspace editor: open-file tabs of the session's editor store,
 * Monaco (or the rich markdown editor), and read-only diff tabs for an
 * agent edit's before / after.
 */
const t = useV2T()
const editor = useEditorStore()
const diffs = ref([])
const activeDiff = ref(null)
const richFiles = ref(new Set())
let diffSeq = 0

const currentDiff = computed(() => diffs.value.find((d) => d.id === activeDiff.value) || null)
const isMarkdown = computed(() => /\.(md|markdown|mdx)$/i.test(editor.activeFilePath || ""))
const richMarkdown = computed(() => richFiles.value.has(editor.activeFilePath))

function selectFile(path) {
  activeDiff.value = null
  editor.selectFile(path)
}

async function closeFile(path) {
  if (editor.openFiles[path]?.dirty) {
    try {
      await ElMessageBox.confirm(t("ws.closeDirty", { name: baseName(path) }), t("ws.closeDirtyTitle"), {
        type: "warning",
        confirmButtonText: t("ws.discard"),
        cancelButtonText: t("ws.keep"),
      })
    } catch {
      return
    }
  }
  editor.closeFile(path)
}

function openFile(path) {
  activeDiff.value = null
  editor.openFile(path)
}

function showDiff({ path, old, new: next }) {
  const existing = diffs.value.find((d) => d.path === path && d.old === old && d.new === next)
  if (existing) {
    activeDiff.value = existing.id
    return
  }
  const id = `diff:${++diffSeq}`
  diffs.value = [...diffs.value, { id, path, old, new: next }]
  activeDiff.value = id
}

function closeDiff(id) {
  diffs.value = diffs.value.filter((d) => d.id !== id)
  if (activeDiff.value === id) activeDiff.value = diffs.value.at(-1)?.id || null
}

function toggleRich() {
  const next = new Set(richFiles.value)
  if (next.has(editor.activeFilePath)) next.delete(editor.activeFilePath)
  else next.add(editor.activeFilePath)
  richFiles.value = next
}

function onChange(content, buffer, path) {
  editor.updateContent(path, content, buffer)
}

function onEdit(buffer, path) {
  editor.markEdited(path, buffer)
}

function onSave(buffer, path) {
  if (editor.openFiles[path] === buffer) editor.saveFile(path)
}

defineExpose({ openFile, showDiff })
</script>
