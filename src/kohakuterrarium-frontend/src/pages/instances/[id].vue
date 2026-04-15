<template>
  <div v-if="instance" class="h-full overflow-hidden">
    <WorkspaceShell :instance-id="route.params.id" @stop="showStopConfirm = true" />

    <!-- Stop confirmation dialog (triggered from the status bar or nav) -->
    <el-dialog v-model="showStopConfirm" title="Stop Instance" width="400px" :close-on-click-modal="true">
      <p class="text-warm-600 dark:text-warm-300">
        Stop <strong>{{ instance.config_name }}</strong
        >? This will terminate the {{ instance.type }} and all its processes.
      </p>
      <template #footer>
        <el-button size="small" @click="showStopConfirm = false">Cancel</el-button>
        <el-button size="small" type="danger" :loading="stopping" @click="confirmStop">Stop</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, provide, ref, watch } from "vue"

import WorkspaceShell from "@/components/layout/WorkspaceShell.vue"
import { useChatStore } from "@/stores/chat"
import { useEditorStore } from "@/stores/editor"
import { useInstancesStore } from "@/stores/instances"
import { useLayoutStore } from "@/stores/layout"

const route = useRoute()
const router = useRouter()
const instances = useInstancesStore()
const chat = useChatStore()
const editor = useEditorStore()
const layout = useLayoutStore()

const instance = computed(() => instances.current)
const showStopConfirm = ref(false)
const stopping = ref(false)
let refreshTimer = null

// Runtime prop map for panels mounted inside the shell's zones.
const panelProps = computed(() => ({
  chat: { instance: instance.value },
  "status-dashboard": {
    instance: instance.value,
    onOpenTab: handleOpenTab,
  },
  activity: { instance: instance.value },
  state: { instance: instance.value },
  creatures: { instance: instance.value },
  files: {
    root: instance.value?.pwd || "",
    onSelect: (path) => editor.openFile(path),
  },
  "file-tree": {
    root: instance.value?.pwd || "",
    onSelect: (path) => editor.openFile(path),
  },
  settings: { instance: instance.value },
  debug: { instance: instance.value },
  terminal: { instance: instance.value },
  "status-tab": {
    instance: instance.value,
    onOpenTab: handleOpenTab,
  },
}))
provide("panelProps", panelProps)

onMounted(async () => {
  await loadInstance()
  applyPresetForInstance()
  refreshTimer = setInterval(() => {
    loadInstance().catch((err) => console.error("Instance refresh failed:", err))
  }, 5000)
})

watch(
  () => route.params.id,
  async () => {
    await loadInstance()
    applyPresetForInstance()
  },
)

async function loadInstance() {
  const id = route.params.id
  if (!id) return
  const loaded = await instances.fetchOne(id)
  if (!loaded) {
    router.replace("/")
    return
  }
  if (instance.value) {
    chat.initForInstance(instance.value)
  }
}

function applyPresetForInstance() {
  const id = route.params.id
  if (!id) return
  layout.loadInstanceOverrides(id)
  const remembered = layout.getInstancePresetId(id)
  if (remembered && layout.allPresets[remembered]) {
    layout.switchPreset(remembered)
    return
  }
  const fallback = instance.value?.type === "terrarium" ? "multi-creature" : "chat-focus"
  layout.switchPreset(fallback)
}

// Persist preset changes against this instance id.
watch(
  () => layout.activePresetId,
  (id) => {
    const instId = route.params.id
    if (id && instId && !id.startsWith("legacy-")) {
      layout.rememberInstancePreset(instId, id)
    }
  },
)

function handleOpenTab(tabKey) {
  chat.openTab(tabKey)
}

async function confirmStop() {
  stopping.value = true
  try {
    await instances.stop(route.params.id)
    showStopConfirm.value = false
    router.push("/")
  } catch (err) {
    console.error("Stop failed:", err)
  } finally {
    stopping.value = false
  }
}

onUnmounted(() => {
  if (refreshTimer) {
    clearInterval(refreshTimer)
    refreshTimer = null
  }
})
</script>
