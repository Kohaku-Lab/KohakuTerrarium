<template>
  <button type="button" class="inline-flex items-center gap-1.5 h-7 px-2 rounded text-[11px] transition-colors shrink-0 border" :class="isRemote ? 'bg-iolite/10 border-iolite/30 text-iolite dark:text-iolite-light hover:bg-iolite/15' : 'bg-transparent border-warm-200 dark:border-warm-700 text-warm-500 dark:text-warm-400 hover:text-warm-700 dark:hover:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800'" :title="t('hostPicker.chipTitle', { name: label })" @click="emit('open')">
    <span class="i-carbon-network-3 text-[12px] shrink-0" :class="isRemote ? '' : 'text-warm-400'" aria-hidden="true" />
    <span v-if="showLabel" class="font-medium truncate max-w-32">{{ label }}</span>
  </button>
</template>

<script setup>
import { computed } from "vue"

import { useHostsStore } from "@/stores/hosts"
import { useI18n } from "@/utils/i18n"

defineProps({
  showLabel: { type: Boolean, default: true },
})
const emit = defineEmits(["open"])

const hosts = useHostsStore()
const { t } = useI18n()

const label = computed(() => {
  if (hosts.isSameOrigin) return t("hostPicker.sameOrigin")
  return hosts.activeHost?.name || hosts.activeHost?.url || ""
})

const isRemote = computed(() => !hosts.isSameOrigin)
</script>
