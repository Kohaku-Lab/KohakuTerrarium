<template>
  <NewSessionDialog v-if="workspace" :key="workspace" :initial-pwd="workspace" @close="workspace = ''" />
</template>

<script setup>
import { ElMessage } from "element-plus"
import { onBeforeUnmount, onMounted, ref } from "vue"

import NewSessionDialog from "@/components/shell/newSession/NewSessionDialog.vue"
import { OPEN_WORKSPACE_EVENT, useDesktopOpen } from "@/composables/useDesktopOpen"
import { useI18n } from "@/utils/i18n"

/**
 * Receives the files and folders the desktop shell is asked to open and shows
 * the one thing that needs a view of its own: the new-session dialog in a folder.
 */
const { t } = useI18n()
const workspace = ref("")

useDesktopOpen({
  onError: (error, path) => {
    const reason = error?.response?.data?.detail || error?.message || String(error)
    ElMessage.error(t("desktop.openFailed", { path, reason }))
  },
})

function onWorkspace(event) {
  workspace.value = event.detail?.path || ""
}

onMounted(() => window.addEventListener(OPEN_WORKSPACE_EVENT, onWorkspace))
onBeforeUnmount(() => window.removeEventListener(OPEN_WORKSPACE_EVENT, onWorkspace))
</script>
