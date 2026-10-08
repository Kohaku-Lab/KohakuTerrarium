<template>
  <div ref="scrollEl" class="h-full overflow-y-auto text-xs" data-test="v2-file-tree" @scroll="onScroll">
    <div v-if="!tree && treeError" class="px-3 py-4 flex flex-col items-center gap-2 text-center" role="alert">
      <span class="text-coral break-words">{{ t("ws.treeFailed", { error: treeError }) }}</span>
      <button class="text-iolite dark:text-iolite-light hover:underline" @click="refresh">{{ t("ws.retry") }}</button>
    </div>
    <div v-else-if="!tree" class="px-3 py-4 text-warm-500 text-center">{{ root ? t("loading") : t("ws.noRoot") }}</div>
    <div v-else-if="!rows.length" class="px-3 py-4 text-warm-400 text-center">{{ t("ws.empty") }}</div>
    <div v-else :style="{ height: `${rows.length * ROW}px`, position: 'relative' }">
      <div :style="{ transform: `translateY(${range[0] * ROW}px)` }">
        <button v-for="row in slice" :key="row.node.path" class="w-full flex items-center gap-1 pr-2 text-left select-none transition-colors" :class="row.node.path === editor.activeFilePath ? 'bg-iolite/10 text-iolite dark:text-iolite-light' : 'text-warm-600 dark:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800'" :style="{ height: `${ROW}px`, paddingLeft: `${row.depth * 14 + 8}px` }" :title="row.node.path" @click="onRow(row.node)">
          <template v-if="row.node.type === 'directory'">
            <span class="w-3 shrink-0 text-[10px] text-warm-400" :class="loading.has(row.node.path) ? 'i-carbon-circle-dash animate-spin' : canExpand(row.node) ? (expanded.has(row.node.path) ? 'i-carbon-chevron-down' : 'i-carbon-chevron-right') : ''" />
            <span class="shrink-0" :class="expanded.has(row.node.path) ? 'i-carbon-folder-open text-amber' : 'i-carbon-folder text-amber/70'" />
          </template>
          <span v-else class="i-carbon-document shrink-0 ml-4 text-warm-400" />
          <span class="truncate">{{ row.node.name }}</span>
        </button>
      </div>
    </div>
  </div>
</template>

<script>
// Expanded folders per tree root, shared by every mount of the tree.
const EXPANDED_BY_ROOT = new Map()
</script>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue"

import { canExpand, flattenTree, visibleRange } from "@/components/session-v2/model/workspace/treeRows"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import { useEditorStore } from "@/stores/editor"

/** The workspace file tree: lazy-expanding directories, only the rows in view rendered; expanded folders persist per root for the page's life. */
const props = defineProps({ root: { type: String, default: "" } })
const emit = defineEmits(["select"])

const ROW = 24
const t = useV2T()
const editor = useEditorStore()
const scrollEl = ref(null)
const scrollTop = ref(0)
const viewport = ref(480)
const expanded = ref(new Set(EXPANDED_BY_ROOT.get(props.root) || []))
watch(expanded, (set) => props.root && EXPANDED_BY_ROOT.set(props.root, set))
const loading = ref(new Set())
let frame = null
let resizeObserver = null

const tree = computed(() => (editor.treeRoot === props.root ? editor.treeData : null))
const treeError = computed(() => (editor.treeRoot === props.root ? editor.treeError : ""))
const rows = computed(() => flattenTree(tree.value, expanded.value))
const range = computed(() => visibleRange(rows.value.length, scrollTop.value, viewport.value, ROW))
const slice = computed(() => rows.value.slice(range.value[0], range.value[1]))

watch(
  () => props.root,
  (root, previous) => {
    if (previous !== undefined) expanded.value = new Set(EXPANDED_BY_ROOT.get(root) || [])
    if (!root) return
    if (editor.treeRoot !== root) {
      editor.setTreeRoot(root)
      if (expanded.value.size) refresh()
    } else refresh()
  },
  { immediate: true },
)

function onScroll() {
  if (frame !== null) return
  frame = requestAnimationFrame(() => {
    frame = null
    scrollTop.value = scrollEl.value?.scrollTop || 0
  })
}

function setOpen(path, open) {
  const next = new Set(expanded.value)
  if (open) next.add(path)
  else next.delete(path)
  expanded.value = next
}

async function onRow(node) {
  if (node.type !== "directory") {
    emit("select", node.path)
    return
  }
  if (expanded.value.has(node.path)) return setOpen(node.path, false)
  if (canExpand(node) && !(node.children || []).length) {
    loading.value = new Set(loading.value).add(node.path)
    try {
      await editor.expandTreeNode(node.path)
    } finally {
      const next = new Set(loading.value)
      next.delete(node.path)
      loading.value = next
    }
  }
  setOpen(node.path, true)
}

/**
 * Reload the tree, then reload every still-present expanded directory,
 * parents before children. Folders the user opens or closes meanwhile keep
 * that state.
 */
async function refresh() {
  const wanted = new Set(expanded.value)
  await editor.refreshTree()
  const byDepth = [...wanted].sort((a, b) => a.length - b.length)
  const reloaded = new Set()
  const gone = new Set()
  for (const path of byDepth) {
    if (!flattenTree(editor.treeData, reloaded).some((row) => row.node.path === path)) {
      gone.add(path)
      continue
    }
    await editor.expandTreeNode(path)
    reloaded.add(path)
  }
  if (gone.size) expanded.value = new Set([...expanded.value].filter((p) => !gone.has(p)))
}

onMounted(() => {
  if (typeof ResizeObserver === "undefined" || !scrollEl.value) return
  resizeObserver = new ResizeObserver(([entry]) => {
    viewport.value = entry.contentRect.height
  })
  resizeObserver.observe(scrollEl.value)
})
onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  if (frame !== null) cancelAnimationFrame(frame)
})

defineExpose({ refresh })
</script>
