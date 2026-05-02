<template>
  <div class="h-full flex flex-col items-center justify-center p-8 text-center text-warm-500 text-sm gap-3">
    <span class="i-carbon-code text-4xl opacity-40" />
    <div class="text-warm-700 dark:text-warm-300 font-medium">Code editor is v1-only for now</div>
    <div class="max-w-md text-xs">
      The Monaco-backed editor surface lives in
      <code class="bg-warm-100 dark:bg-warm-900 px-1 rounded">/editor/&lt;id&gt;</code>
      under the Classic (v1) shell. Wiring it as a v2 tab requires the editor page to accept a prop instead of route params; deferred to Phase 6 polish.
    </div>
    <div class="flex gap-2 mt-2">
      <button class="btn-secondary text-xs px-3 py-1.5" @click="switchToV1">Switch to v1</button>
    </div>
    <div class="text-[10px] text-warm-400 mt-3 font-mono">tab id: {{ tab.id }}</div>
  </div>
</template>

<script setup>
import { setUIVersion } from "@/utils/uiVersion"

const props = defineProps({ tab: { type: Object, required: true } })

function switchToV1() {
  setUIVersion("v1")
  if (typeof window !== "undefined") {
    window.location.href = `/editor/${encodeURIComponent(props.tab?.slug ?? "")}`
  }
}
</script>
