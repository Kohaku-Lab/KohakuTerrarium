<template>
  <SectionCard :title="t('studio.creature.systemPrompt.title')" icon="i-carbon-quotes">
    <template #actions>
      <span class="text-[11px] font-mono text-warm-500 dark:text-warm-500">{{ fileName || "system_prompt" }}</span>
      <span class="text-[11px] text-warm-400">{{ t("studioApp.prompt.chars", { n: prompt.length }) }}</span>
    </template>

    <textarea ref="area" :value="prompt" class="kt-v2-edge w-full min-h-40 max-h-[60vh] rounded-lg border bg-[var(--v2-card)] px-3 py-2 font-mono text-xs leading-relaxed text-warm-800 dark:text-warm-100 outline-none focus:border-iolite resize-y" spellcheck="false" :placeholder="t('studio.creature.systemPrompt.none')" data-test="system-prompt" @input="onInput" />

    <div v-if="inheritanceHint" class="mt-2 flex items-center gap-1.5 text-[11px] text-warm-500 dark:text-warm-500">
      <div class="i-carbon-tree-view-alt text-xs" />
      <span>{{ inheritanceHint }}</span>
    </div>
  </SectionCard>
</template>

<script setup>
import { computed, nextTick, onMounted, ref, watch } from "vue"

import { useI18n } from "@/utils/i18n"

import SectionCard from "./SectionCard.vue"

/**
 * The creature's system prompt, edited in place: the prompt file it names
 * (`fileName`) or the inline `system_prompt`. Edits go to the draft and are
 * written with the rest of the creature on Save.
 */
const props = defineProps({
  fileName: { type: String, default: "" },
  prompt: { type: String, default: "" },
  effective: { type: Object, default: null },
  promptMode: { type: String, default: "concat" },
})
const emit = defineEmits(["edit"])

const MAX_AUTO_PX = 480

const { t } = useI18n()
const area = ref(null)

function fit() {
  const el = area.value
  if (!el) return
  el.style.height = "auto"
  el.style.height = `${Math.min(el.scrollHeight + 2, MAX_AUTO_PX)}px`
}

function onInput(e) {
  emit("edit", e.target.value)
  fit()
}

watch(
  () => props.prompt,
  () => nextTick(fit),
)
onMounted(fit)

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
