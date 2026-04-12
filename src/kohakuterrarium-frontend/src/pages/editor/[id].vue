<template>
  <div v-if="instance" class="h-full overflow-hidden">
    <WorkspaceShell :instance-id="route.params.id" />
  </div>
</template>

<script setup>
import { computed, onMounted, provide, watch } from "vue"

import WorkspaceShell from "@/components/layout/WorkspaceShell.vue"
import { useChatStore } from "@/stores/chat"
import { useEditorStore } from "@/stores/editor"
import { useInstancesStore } from "@/stores/instances"
import { useLayoutStore } from "@/stores/layout"

const route = useRoute()
const instances = useInstancesStore()
const chat = useChatStore()
const editor = useEditorStore()
const layout = useLayoutStore()

const instance = computed(() => instances.current)
const treeRoot = computed(() => instance.value?.pwd || "")

const panelProps = computed(() => ({
  "file-tree": {
    root: treeRoot.value,
    onSelect: onFileSelect,
  },
  files: {
    root: treeRoot.value,
    onSelect: onFileSelect,
  },
  chat: { instance: instance.value },
  "editor-status": { instance: instance.value },
  activity: { instance: instance.value },
  state: { instance: instance.value },
  debug: { instance: instance.value },
  settings: { instance: instance.value },
}))
provide("panelProps", panelProps)

onMounted(async () => {
  await loadInstance()
  applyPreset()
})

watch(
  () => route.params.id,
  async () => {
    await loadInstance()
    applyPreset()
  },
)

function applyPreset() {
  const id = route.params.id
  if (!id) return
  layout.loadInstanceOverrides(id)
  const remembered = layout.getInstancePresetId(id)
  const target = remembered && layout.allPresets[remembered] ? remembered : "workspace"
  layout.switchPreset(target)
}

async function loadInstance() {
  const id = route.params.id
  if (!id) return
  await instances.fetchOne(id)
  if (instance.value) {
    chat.initForInstance(instance.value)
  }
}

function onFileSelect(path) {
  editor.openFile(path)
}

// Refresh tree + reload active file when tool_done events indicate a write.
watch(
  () => chat.currentMessages,
  (msgs) => {
    if (!msgs.length) return
    const last = msgs[msgs.length - 1]
    if (!last.tool_calls) return
    for (const tc of last.tool_calls) {
      if (
        tc.status === "done" &&
        (tc.name === "write" || tc.name === "edit" || tc.name === "bash")
      ) {
        editor.refreshTree()
        if (editor.activeFilePath) {
          editor.revertFile(editor.activeFilePath)
        }
        break
      }
    }
  },
  { deep: true },
)

// Persist preset changes.
watch(
  () => layout.activePresetId,
  (id) => {
    const instId = route.params.id
    if (id && instId && !id.startsWith("legacy-")) {
      layout.rememberInstancePreset(instId, id)
    }
  },
)
</script>
