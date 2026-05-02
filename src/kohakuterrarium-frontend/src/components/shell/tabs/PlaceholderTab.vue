<template>
  <div class="h-full flex items-center justify-center text-secondary">
    <div class="text-center">
      <div class="i-carbon-cube-view text-4xl mb-3 opacity-50" />
      <div class="text-sm">{{ tab?.kind ?? "no tab" }}</div>
      <div class="text-xs text-warm-400 mt-1">{{ tab?.id ?? "" }}</div>
      <div class="text-xs text-warm-400 mt-3 max-w-xs">Tab content for this kind is not yet wired up — Phase {{ phaseFor }} adds it.</div>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue"

const props = defineProps({ tab: { type: Object, default: null } })
const phaseFor = computed(() => {
  switch (props.tab?.kind) {
    case "inspector":
      return "3 (Inspector)"
    case "dashboard":
      return "4 (Dashboard)"
    case "attach":
    case "session-viewer":
    case "studio-editor":
    case "catalog":
    case "settings":
    case "code-editor":
      return "5 (AttachTab + thin embeds)"
    default:
      return "later"
  }
})
</script>
