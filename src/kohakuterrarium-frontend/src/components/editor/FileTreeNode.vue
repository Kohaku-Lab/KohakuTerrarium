<template>
  <div>
    <!-- Directory -->
    <div
      v-if="node.type === 'directory'"
      class="flex items-center gap-1 px-2 py-1 cursor-pointer select-none hover:bg-warm-100 dark:hover:bg-warm-800 transition-colors"
      :style="{ paddingLeft: depth * 14 + 8 + 'px' }"
      @click="expanded = !expanded"
    >
      <div
        class="text-warm-400 text-[10px] w-3 shrink-0 transition-transform"
        :class="expanded ? 'i-carbon-chevron-down' : 'i-carbon-chevron-right'"
      />
      <div
        class="shrink-0"
        :class="expanded ? 'i-carbon-folder-open text-amber' : 'i-carbon-folder text-amber/70'"
      />
      <span class="truncate text-warm-600 dark:text-warm-300">{{ node.name }}</span>
    </div>

    <!-- Directory children -->
    <template v-if="node.type === 'directory' && expanded">
      <FileTreeNode
        v-for="child in sortedChildren"
        :key="child.path"
        :node="child"
        :depth="depth + 1"
        @select="$emit('select', $event)"
      />
    </template>

    <!-- File -->
    <div
      v-if="node.type === 'file'"
      class="flex items-center gap-1 px-2 py-1 cursor-pointer select-none transition-colors"
      :class="
        isActive
          ? 'bg-iolite/10 dark:bg-iolite/15 text-iolite dark:text-iolite-light'
          : 'hover:bg-warm-100 dark:hover:bg-warm-800 text-warm-600 dark:text-warm-400'
      "
      :style="{ paddingLeft: depth * 14 + 22 + 'px' }"
      @click="$emit('select', node.path)"
    >
      <div class="i-carbon-document text-warm-400 shrink-0" />
      <span class="truncate">{{ node.name }}</span>
    </div>
  </div>
</template>

<script setup>
import { useEditorStore } from "@/stores/editor"

const props = defineProps({
  node: { type: Object, required: true },
  depth: { type: Number, default: 0 },
})

defineEmits(["select"])

const editor = useEditorStore()
const expanded = ref(false)

const isActive = computed(() => editor.activeFilePath === props.node.path)

const sortedChildren = computed(() => {
  if (!props.node.children) return []
  return [...props.node.children].sort((a, b) => {
    // Directories first
    if (a.type !== b.type) return a.type === "directory" ? -1 : 1
    return a.name.localeCompare(b.name)
  })
})
</script>
