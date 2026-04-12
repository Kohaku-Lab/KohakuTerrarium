<template>
  <div class="h-full flex flex-col bg-warm-50 dark:bg-warm-900">
    <!-- Tree body only — the header (title + refresh) is provided by
         the parent (FilesPanel or the old editor page). -->
    <div class="flex-1 overflow-y-auto py-1 text-xs">
      <template v-if="tree">
        <FileTreeNode
          v-for="child in tree.children || []"
          :key="child.path"
          :node="child"
          :depth="0"
          @select="onSelect"
        />
      </template>
      <div v-else-if="loading" class="px-3 py-4 text-warm-400 text-center">Loading...</div>
      <div v-else class="px-3 py-4 text-warm-400 text-center">No files</div>
    </div>
  </div>
</template>

<script setup>
import FileTreeNode from "@/components/editor/FileTreeNode.vue"
import { useEditorStore } from "@/stores/editor"

const props = defineProps({
  root: { type: String, required: true },
})

const emit = defineEmits(["select"])
const editor = useEditorStore()

const tree = computed(() => editor.treeData)
const loading = ref(false)

watch(
  () => props.root,
  (val) => {
    if (val) {
      editor.setTreeRoot(val)
    }
  },
  { immediate: true },
)

function onSelect(path) {
  emit("select", path)
}

function refresh() {
  editor.refreshTree()
}

defineExpose({ refresh })
</script>
