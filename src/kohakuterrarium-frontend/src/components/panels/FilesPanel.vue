<template>
  <div class="h-full flex flex-col bg-warm-50 dark:bg-warm-900">
    <!-- Panel header -->
    <div
      class="flex items-center gap-2 px-3 py-2 border-b border-warm-200 dark:border-warm-700 shrink-0"
    >
      <div class="i-carbon-folder text-sm text-warm-500" />
      <span class="text-xs font-medium text-warm-500 dark:text-warm-400 truncate flex-1"
        >Files</span
      >
      <button
        class="w-6 h-6 flex items-center justify-center rounded text-warm-400 hover:text-warm-600 dark:hover:text-warm-300 transition-colors"
        title="Refresh"
        @click="treeRef?.refresh()"
      >
        <div class="i-carbon-renew text-sm" />
      </button>
    </div>

    <!-- Tree / Touched segmented control -->
    <div class="flex items-center gap-0.5 px-3 pt-2 pb-1 shrink-0 text-[10px]">
      <button
        class="px-2 py-0.5 rounded transition-colors"
        :class="view === 'tree' ? 'bg-iolite/15 text-iolite' : 'text-warm-400 hover:text-warm-600'"
        @click="view = 'tree'"
      >
        Tree
      </button>
      <button
        class="px-2 py-0.5 rounded transition-colors"
        :class="
          view === 'touched' ? 'bg-iolite/15 text-iolite' : 'text-warm-400 hover:text-warm-600'
        "
        @click="view = 'touched'"
      >
        Touched
        <span v-if="touchedCount > 0" class="ml-1 text-[9px] font-mono opacity-70">{{
          touchedCount
        }}</span>
      </button>
    </div>

    <!-- Body: tree or touched list -->
    <div class="flex-1 min-h-0">
      <FileTree v-if="view === 'tree'" ref="treeRef" :root="root" @select="onSelect" />
      <div v-else class="h-full overflow-y-auto px-3 py-2 text-[11px]">
        <div v-if="touchedCount === 0" class="text-warm-400 text-center py-6">
          No files touched yet
        </div>
        <template v-else>
          <template v-for="action in ['wrote', 'read', 'errored', 'exec']" :key="action">
            <div v-if="files.grouped[action].length > 0" class="mb-3">
              <div class="text-[9px] uppercase tracking-wider text-warm-400 mb-1">
                {{ actionLabel(action) }}
                <span class="opacity-60">({{ files.grouped[action].length }})</span>
              </div>
              <div class="flex flex-col gap-0.5">
                <button
                  v-for="entry in files.grouped[action]"
                  :key="entry.turn + entry.path"
                  class="flex items-center gap-2 px-2 py-1 rounded hover:bg-warm-100 dark:hover:bg-warm-800 transition-colors text-left"
                  @click="onSelect(entry.path)"
                >
                  <span class="w-3 shrink-0 text-center" :class="actionColor(action)">{{
                    actionSymbol(action)
                  }}</span>
                  <span class="font-mono text-warm-700 dark:text-warm-300 truncate">{{
                    shortPath(entry.path)
                  }}</span>
                  <span class="flex-1" />
                  <span class="text-warm-400 text-[9px] font-mono">
                    {{ entry.tool }}
                  </span>
                </button>
              </div>
            </div>
          </template>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from "vue"

import FileTree from "@/components/editor/FileTree.vue"
import { useChatStore } from "@/stores/chat"
import { useFilesStore } from "@/stores/files"

const props = defineProps({
  root: { type: String, required: true },
  onSelect: { type: Function, default: () => {} },
})

const chat = useChatStore()
const files = useFilesStore()

const view = ref("tree")
const treeRef = ref(null)

// Refresh tree when processing ends or jobs complete (agent wrote files).
watch(
  () => [chat.processing, Object.keys(chat.runningJobs).length],
  ([processing, jobs], [prevProcessing, prevJobs]) => {
    if ((!processing && prevProcessing) || jobs < (prevJobs ?? jobs)) {
      setTimeout(() => treeRef.value?.refresh(), 500)
    }
  },
)

const touchedCount = computed(() => files.touched.length)

function actionLabel(action) {
  return (
    {
      wrote: "Wrote",
      read: "Read",
      errored: "Errored",
      exec: "Exec",
    }[action] || action
  )
}

function actionSymbol(action) {
  return (
    {
      wrote: "✎",
      read: "●",
      errored: "✕",
      exec: "$",
    }[action] || "·"
  )
}

function actionColor(action) {
  return (
    {
      wrote: "text-iolite",
      read: "text-aquamarine",
      errored: "text-coral",
      exec: "text-amber",
    }[action] || ""
  )
}

function shortPath(p) {
  if (!p) return ""
  return p.replace(/\\/g, "/").split("/").slice(-3).join("/")
}
</script>
