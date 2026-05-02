<template>
  <div class="absolute top-9 right-2 z-30 w-56 bg-warm-50 dark:bg-warm-900 border border-warm-200 dark:border-warm-700 rounded shadow-lg py-1 text-xs">
    <button class="w-full text-left px-3 py-1.5 hover:bg-warm-100 dark:hover:bg-warm-800 flex items-center gap-2" :disabled="!canReopen" @click="reopen">
      <span class="i-carbon-undo" />
      Reopen last closed
      <span v-if="!canReopen" class="ml-auto text-warm-400">empty</span>
    </button>
    <button class="w-full text-left px-3 py-1.5 hover:bg-warm-100 dark:hover:bg-warm-800 flex items-center gap-2" @click="goDashboard">
      <span class="i-carbon-home" />
      Reset to Dashboard
    </button>
    <div class="border-t border-warm-200 dark:border-warm-700 my-1" />
    <button class="w-full text-left px-3 py-1.5 hover:bg-warm-100 dark:hover:bg-warm-800 flex items-center gap-2" @click="openCatalog">
      <span class="i-carbon-catalog" />
      Open Catalog
    </button>
    <button class="w-full text-left px-3 py-1.5 hover:bg-warm-100 dark:hover:bg-warm-800 flex items-center gap-2" @click="openSettings">
      <span class="i-carbon-settings" />
      Open Settings
    </button>
  </div>
</template>

<script setup>
import { computed } from "vue"
import { useTabsStore } from "@/stores/tabs"

const tabs = useTabsStore()
const emit = defineEmits(["close"])

const canReopen = computed(() => tabs.recentlyClosed.length > 0)

function reopen() {
  tabs.reopenLastClosed()
  emit("close")
}
function goDashboard() {
  tabs.openTab({ kind: "dashboard", id: "dashboard" })
  emit("close")
}
function openCatalog() {
  tabs.openTab({ kind: "catalog", id: "catalog" })
  emit("close")
}
function openSettings() {
  tabs.openTab({ kind: "settings", id: "settings" })
  emit("close")
}
</script>
