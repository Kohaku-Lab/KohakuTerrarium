<template>
  <StudioEditorTab v-if="appMode === 'studio'" :tab="studioHome" as-home />
  <LabPage v-else />
</template>

<script setup>
import { computed } from "vue"

import LabPage from "@/components/lab/LabPage.vue"
import { useAppMode } from "@/components/shell/rail/useAppMode"
import { useStudioWorkspaceRoot } from "@/components/shell/rail/useOpenStudio"
import StudioEditorTab from "@/components/shell/tabs/StudioEditorTab.vue"

/** The fixed home tab: the lab in Terrarium mode, Studio's home in Studio mode. */
defineProps({ tab: { type: Object, required: true } })

const { appMode } = useAppMode()
const workspaceRoot = useStudioWorkspaceRoot()
const studioHome = computed(() => ({ kind: "studio-editor", id: "dashboard", entityKind: "home", workspace: workspaceRoot() }))
</script>
