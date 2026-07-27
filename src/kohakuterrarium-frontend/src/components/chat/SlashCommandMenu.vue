<template>
  <div v-if="open" id="slash-command-menu" class="absolute inset-x-0 bottom-full z-40 mb-2 overflow-hidden rounded-xl border border-warm-200 bg-white shadow-xl dark:border-warm-700 dark:bg-warm-850" role="listbox" :aria-label="t('chat.slash.available')">
    <div v-if="loading" class="px-3 py-2 text-xs text-warm-500">{{ t("chat.slash.loading") }}</div>
    <div v-else-if="!visualEntries.length" class="px-3 py-2 text-xs text-warm-500">{{ t("chat.slash.empty") }}</div>
    <template v-for="item in visualEntries" :key="item.key">
      <div v-if="item.heading" class="bg-warm-50 px-3 py-1 text-[10px] font-semibold uppercase tracking-wide text-warm-500 dark:bg-warm-900/60">
        {{ item.type === "command" ? t("chat.slash.commands") : t("chat.slash.skills") }}
      </div>
      <button v-else :id="`slash-option-${item.visualIndex}`" type="button" role="option" :aria-selected="item.visualIndex === selectedIndex" class="flex w-full items-start gap-3 px-3 py-2 text-left hover:bg-primary-50 dark:hover:bg-primary-950/30" :class="item.visualIndex === selectedIndex ? 'bg-primary-50 dark:bg-primary-950/30' : ''" @mouseenter="$emit('select-index', item.visualIndex)" @click="$emit('choose', item.entry)" @mousedown.prevent>
        <code class="shrink-0 text-xs font-semibold text-primary-700 dark:text-primary-300">/{{ item.entry.name }}</code>
        <span class="min-w-0 text-xs text-warm-600 dark:text-warm-300">{{ item.entry.description || t("chat.slash.noDescription") }}</span>
      </button>
    </template>
  </div>
</template>

<script setup>
import { computed } from "vue"

import { useI18n } from "@/utils/i18n"

const props = defineProps({
  open: { type: Boolean, default: false },
  loading: { type: Boolean, default: false },
  entries: { type: Array, default: () => [] },
  selectedIndex: { type: Number, default: 0 },
})

defineEmits(["choose", "select-index"])

const { t } = useI18n()
const visualEntries = computed(() => {
  const items = []
  let visualIndex = 0
  for (const type of ["command", "skill"]) {
    const entries = props.entries.filter((entry) => (entry.type || entry.kind) === type)
    if (!entries.length) continue
    items.push({ heading: true, type, key: `heading:${type}` })
    for (const entry of entries) {
      items.push({ entry, type, visualIndex, key: `${type}:${entry.name}` })
      visualIndex += 1
    }
  }
  return items
})
</script>
