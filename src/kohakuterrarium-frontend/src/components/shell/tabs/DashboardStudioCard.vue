<template>
  <div class="card p-4">
    <header class="flex items-baseline justify-between mb-2">
      <h2 class="text-sm font-medium text-warm-700 dark:text-warm-300">Studio</h2>
      <button class="text-xs text-iolite hover:underline" @click="open">Open →</button>
    </header>
    <div class="text-xs text-warm-500">Author creatures, modules, and workspaces. Open Studio from the rail or click here.</div>
  </div>
</template>

<script setup>
import { useTabsStore } from "@/stores/tabs"

const tabs = useTabsStore()

function open() {
  tabs.openTab({
    kind: "studio-editor",
    id: "studio:default:home",
    workspace: "default",
    entity: "home",
    entityKind: "workspace",
  })
}
</script>
