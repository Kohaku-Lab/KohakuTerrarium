<template>
  <span class="inline-flex items-center">
    <span class="inline-block w-2 h-2 rounded-full" :class="dotClass" :title="statusLabel" role="img" :aria-label="statusLabel" />
    <span class="sr-only">{{ statusLabel }}</span>
  </span>
</template>

<script setup>
import { useI18n } from "@/utils/i18n"

const props = defineProps({
  status: { type: String, default: "idle" },
})

const { t } = useI18n()

const dotClass = computed(() => {
  switch (props.status) {
    case "running":
    case "processing":
      return "bg-aquamarine kohaku-glow"
    case "idle":
    case "done":
      return "bg-amber"
    case "error":
      return "bg-coral"
    case "stopped":
      return "bg-warm-400"
    default:
      return "bg-warm-400"
  }
})

const statusLabel = computed(() => {
  const key = `common.status.${props.status}`
  const label = t(key)
  // Fall back to the raw status string when a locale is missing the key.
  return label === key ? props.status : label
})
</script>

<style scoped>
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
</style>
