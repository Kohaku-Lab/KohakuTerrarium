<template>
  <div class="flex items-start gap-2 px-3 py-2 text-xs border-b bg-amber/10 dark:bg-amber/15 border-amber/30 text-amber-shadow dark:text-amber">
    <div class="i-carbon-warning-alt text-sm shrink-0 mt-0.5" />
    <div class="flex-1 min-w-0">
      <div class="font-medium">{{ title || t("studio.module.raw.title") }}</div>
      <div v-if="message" class="mt-0.5 font-mono text-[11px] break-all opacity-80">
        {{ message }}
      </div>
    </div>
    <button v-if="retry" class="shrink-0 px-2 py-0.5 rounded text-[11px] border border-amber/40 hover:bg-amber/20 transition-colors" @click="$emit('retry')">
      {{ t("studio.module.raw.retry") }}
    </button>
  </div>
</template>

<script setup>
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()

defineProps({
  title: { type: String, default: "" },
  message: { type: String, default: "" },
  /** Show the "retry simple mode" button. */
  retry: { type: Boolean, default: false },
})

defineEmits(["retry"])
</script>
