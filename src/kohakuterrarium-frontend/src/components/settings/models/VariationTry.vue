<template>
  <div class="px-4 py-3 flex flex-col gap-3" data-variation-preview>
    <div class="kt-text-caption uppercase tracking-wider text-warm-500">{{ t("settings.models.variations.try") }}</div>

    <div v-for="group in picks" :key="group.name" class="flex items-center gap-2 flex-wrap">
      <span class="kt-text-caption font-mono text-warm-500 w-28 shrink-0 truncate">{{ group.name }}</span>
      <div class="flex flex-wrap gap-1">
        <button type="button" class="chip" :class="chipClass(!selection[group.name])" :aria-pressed="!selection[group.name]" @click="delete selection[group.name]">{{ t("settings.models.variations.default") }}</button>
        <button v-for="option in group.options" :key="option.name" type="button" class="chip font-mono" :class="chipClass(selection[group.name] === option.name)" :aria-pressed="selection[group.name] === option.name" data-option-chip @click="selection[group.name] = option.name">{{ option.name }}</button>
      </div>
    </div>

    <div class="flex items-center gap-2 flex-wrap">
      <code class="kt-text-caption font-mono text-iolite dark:text-iolite-light break-all flex-1 min-w-0" data-selector>{{ selector }}</code>
      <KButton size="sm" variant="ghost" @click="copy"><span class="i-carbon-copy" />{{ t("settings.models.variations.copy") }}</KButton>
    </div>

    <div class="kt-v2-chrome kt-v2-line rounded-lg border divide-y divide-[var(--v2-line)]" data-diff>
      <p v-if="!changes.length" class="px-3 py-2 kt-text-caption text-warm-500">{{ t("settings.models.variations.noDiff") }}</p>
      <div v-for="change in changes" :key="change.path" class="px-3 py-1.5 flex items-baseline gap-2 flex-wrap kt-text-caption font-mono">
        <span class="text-warm-600 dark:text-warm-300">{{ change.path }}</span>
        <span class="text-warm-400">{{ change.before === undefined ? t("settings.models.variations.unset") : show(change.before) }}</span>
        <span class="i-carbon-arrow-right text-warm-400" />
        <span class="text-iolite dark:text-iolite-light font-medium">{{ show(change.after) }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, reactive, watch } from "vue"
import { ElMessage } from "element-plus"

import KButton from "@/components/common/ActionButton.vue"
import { diffOf, resolvePreview, selectorOf } from "@/components/settings/models/variationModel"
import { useI18n } from "@/utils/i18n"

/** Pick an option per knob and see the selector to type and the request fields it changes. */
const props = defineProps({
  groups: { type: Array, required: true },
  base: { type: Object, required: true },
  provider: { type: String, default: "" },
  name: { type: String, default: "" },
})

const { t } = useI18n()
const selection = reactive({})

const picks = computed(() => props.groups.map((group) => ({ name: group.name, options: group.options.filter((option) => option.name) })).filter((group) => group.name && group.options.length))
const selector = computed(() => selectorOf(props.provider, props.name, selection))
const changes = computed(() => diffOf(props.base, resolvePreview(props.base, props.groups, selection)))

watch(picks, (list) => {
  for (const key of Object.keys(selection)) {
    const group = list.find((item) => item.name === key)
    if (!group || !group.options.some((option) => option.name === selection[key])) delete selection[key]
  }
})

const chipClass = (active) => (active ? "border-iolite/40 bg-iolite/10 text-iolite dark:text-iolite-light" : "border-warm-200 dark:border-warm-700 text-warm-600 dark:text-warm-300 hover:border-iolite/40")
const show = (value) => JSON.stringify(value)

async function copy() {
  try {
    await navigator.clipboard.writeText(selector.value)
    ElMessage.success(t("settings.models.variations.copied"))
  } catch {
    ElMessage.error(t("settings.models.variations.copyFailed"))
  }
}
</script>
