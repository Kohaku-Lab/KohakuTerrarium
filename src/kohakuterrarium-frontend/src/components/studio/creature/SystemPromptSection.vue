<template>
  <SectionCard :title="t('studio.creature.systemPrompt.title')" icon="i-carbon-quotes">
    <template #actions>
      <span v-if="fileName" class="text-[11px] font-mono text-warm-500 dark:text-warm-500">
        {{ fileName }}
      </span>
      <KButton size="sm" variant="secondary" icon="i-carbon-edit" :disabled="true" :title="t('studio.creature.systemPrompt.editComingSoon')">
        {{ t("studio.creature.systemPrompt.edit") }}
      </KButton>
    </template>

    <div v-if="!prompt" class="text-xs text-warm-500 italic px-1 py-2">
      {{ t("studio.creature.systemPrompt.none") }}
    </div>
    <div v-else class="rounded border border-warm-200/80 dark:border-warm-800/80 bg-warm-50 dark:bg-warm-950 px-3 py-2 font-mono text-xs text-warm-700 dark:text-warm-300 max-h-40 overflow-y-auto whitespace-pre-wrap leading-relaxed">
      {{ preview }}
    </div>

    <div v-if="inheritanceHint" class="mt-2 flex items-center gap-1.5 text-[11px] text-warm-500 dark:text-warm-500">
      <div class="i-carbon-tree-view-alt text-xs" />
      <span>{{ inheritanceHint }}</span>
    </div>
  </SectionCard>
</template>

<script setup>
import { computed } from "vue"

import KButton from "@/components/studio/common/KButton.vue"
import { useI18n } from "@/utils/i18n"

import SectionCard from "./SectionCard.vue"

const { t } = useI18n()

const props = defineProps({
  fileName: { type: String, default: "" },
  prompt: { type: String, default: "" },
  effective: { type: Object, default: null },
  promptMode: { type: String, default: "concat" },
})

const PREVIEW_LIMIT = 600

const preview = computed(() => {
  const s = props.prompt || ""
  if (s.length <= PREVIEW_LIMIT) return s
  return s.slice(0, PREVIEW_LIMIT) + "\n…"
})

const inheritanceHint = computed(() => {
  const chain = props.effective?.inheritance_chain || []
  if (!chain.length) return ""
  if (props.promptMode === "replace") {
    return t("studio.creature.systemPrompt.modeReplace")
  }
  return t("studio.creature.systemPrompt.modeConcat", {
    base: chain[chain.length - 1],
  })
})
</script>
