<template>
  <NewSessionDialog v-if="open" :key="dialogKey" :initial-pwd="workspace" @close="open = false" />
</template>

<script setup>
import { ElMessage } from "element-plus"
import { onBeforeUnmount, onMounted, ref } from "vue"

import NewSessionDialog from "@/components/shell/newSession/NewSessionDialog.vue"
import { OPEN_WORKSPACE_EVENT, useDesktopOpen } from "@/composables/useDesktopOpen"
import { useI18n } from "@/utils/i18n"

/**
 * Receives the files and folders the desktop shell is asked to open and shows
 * the one thing that needs a view of its own: the new-session dialog, in the
 * given folder or (an empty path, from the menu) in the default one.
 */
const { t } = useI18n()
const open = ref(false)
const workspace = ref("")
const dialogKey = ref(0)

useDesktopOpen({
  onError: (error, path) => {
    const reason = error?.response?.data?.detail || error?.message || String(error)
    ElMessage.error(t("desktop.openFailed", { path, reason }))
  },
})

function onWorkspace(event) {
  workspace.value = event.detail?.path || ""
  dialogKey.value += 1
  open.value = true
}

onMounted(() => window.addEventListener(OPEN_WORKSPACE_EVENT, onWorkspace))
onBeforeUnmount(() => window.removeEventListener(OPEN_WORKSPACE_EVENT, onWorkspace))
</script>
