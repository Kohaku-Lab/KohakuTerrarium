<template>
  <div v-if="instance" class="h-full overflow-hidden">
    <WorkspaceShell :instance-id="route.params.id" @stop="showStopConfirm = true" />

    <!-- Stop confirmation dialog (triggered from the status bar or nav) -->
    <el-dialog v-model="showStopConfirm" :title="t('home.stopDialogTitle')" width="400px" :close-on-click-modal="true">
      <p class="text-warm-600 dark:text-warm-300">
        {{ t("home.stopDialogBody", { name: instance.config_name, type: instanceTypeLabel }) }}
      </p>
      <template #footer>
        <el-button size="small" @click="showStopConfirm = false">{{ t("common.cancel") }}</el-button>
        <el-button size="small" type="danger" :loading="stopping" @click="confirmStop">{{ t("common.stop") }}</el-button>
      </template>
    </el-dialog>
  </div>
  <div v-else class="h-full flex items-center justify-center text-secondary">{{ t("common.loading") }}</div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, provide, ref, watch } from "vue"

import WorkspaceShell from "@/components/layout/WorkspaceShell.vue"
import { createVisibilityInterval } from "@/composables/useVisibilityInterval"
import { useChatStore } from "@/stores/chat"
import { useEditorStore } from "@/stores/editor"
import { useInstancesStore } from "@/stores/instances"
import { useLayoutStore } from "@/stores/layout"
import { useI18n } from "@/utils/i18n"

const route = useRoute()
const router = useRouter()
const instances = useInstancesStore()
const chat = useChatStore()
const editor = useEditorStore()
const layout = useLayoutStore()
const { t } = useI18n()

const loadedInstance = ref(null)
const instance = computed(() => {
  const id = String(route.params.id || "")
  if (!id) return null
  if (loadedInstance.value?.id === id) return loadedInstance.value
  if (instances.current?.id === id) return instances.current
  return instances.list.find((item) => item.id === id) || null
})
const showStopConfirm = ref(false)
const stopping = ref(false)
let refreshTimer = null

const instanceTypeLabel = computed(() => {
  if (!instance.value?.type) return t("common.creature")
  return instance.value.type === "terrarium" ? t("common.terrarium") : t("common.creature")
})

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
  modules: { instance: instance.value },
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
  // Visibility-aware refresh: pauses polling while the tab is hidden
  // so backgrounded instance pages don't drive idle CPU / GPU load.
  refreshTimer = createVisibilityInterval(() => {
    loadInstance().catch((err) => console.error("Instance refresh failed:", err))
  }, 5000)
  refreshTimer.start()
})

watch(
  () => route.params.id,
  async () => {
    await loadInstance()
    applyPresetForInstance()
  },
)

async function loadInstance() {
  const id = String(route.params.id || "")
  if (!id) return
  // If the chat store is still wired to a different surface (most
  // commonly the session viewer, which sets ``_instanceId`` to
  // ``session:<name>``), wipe it synchronously before the awaited
  // fetch. Otherwise the WorkspaceShell mounts and renders the
  // previous surface's tabs/messages while ``initForInstance`` is
  // still in flight.
  if (chat._instanceId && chat._instanceId !== id) {
    chat.resetForRouteSwitch()
  }
  const loaded = await instances.fetchOne(id)
  if (!loaded) {
    loadedInstance.value = null
    router.replace("/")
    return
  }
  loadedInstance.value = loaded
  chat.initForInstance(loaded)
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
    refreshTimer.stop()
    refreshTimer = null
  }
})
</script>
