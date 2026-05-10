<template>
  <div class="h-full flex flex-col items-center justify-center p-8 text-center text-warm-500 text-sm gap-3">
    <span class="i-carbon-code text-4xl opacity-40" />
    <div class="text-warm-700 dark:text-warm-300 font-medium">Open this in Studio</div>
    <div class="max-w-md text-xs">The standalone code-editor tab kind retired with the v1 shell. The Monaco-backed editor lives inside Studio now — open Studio from the rail to edit any file under your workspace.</div>
    <div class="flex gap-2 mt-2">
      <button class="btn-secondary text-xs px-3 py-1.5" @click="openStudio">Open Studio</button>
    </div>
    <div class="text-[10px] text-warm-400 mt-3 font-mono">tab id: {{ tab.id }}</div>
  </div>
</template>

<script setup>
import { useTabsStore } from "@/stores/tabs"
import { buildStudioTabId } from "@/utils/tabsUrl"

const props = defineProps({ tab: { type: Object, required: true } })

const tabs = useTabsStore()

function openStudio() {
  tabs.openTab({
    kind: "studio-editor",
    id: buildStudioTabId({ entityKind: "home" }),
    workspace: "",
    entity: "home",
    entityKind: "home",
  })
  tabs.closeTab(props.tab.id)
}
</script>
