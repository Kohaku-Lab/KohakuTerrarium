<template>
  <div class="flex items-center gap-2 w-full min-w-0">
    <button class="h-8 px-2 rounded-lg inline-flex items-center gap-1.5 text-[13px] text-warm-600 dark:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800" :title="t('studio.frame.back')" data-test="module-back" @click="$emit('back')"><span class="i-carbon-arrow-left" />{{ t("studioApp.nav.overview") }}</button>
    <span class="text-warm-400">/</span>
    <span :class="[meta.icon, meta.accent]" class="shrink-0" />
    <h1 class="text-[14px] font-semibold text-warm-800 dark:text-warm-100 truncate">{{ name }}</h1>
    <span class="text-[11px] text-warm-500 shrink-0">{{ t(`studioApp.kind.${kind}.noun`) }}</span>
    <span v-if="dirty" class="flex items-center gap-1 text-[11px] text-iolite dark:text-iolite-light shrink-0" :title="t('studio.frame.unsaved')">
      <span class="w-1.5 h-1.5 rounded-full bg-iolite" />
      {{ t("studio.frame.unsaved") }}
    </span>

    <div class="flex-1" />

    <span v-if="check" class="flex items-center gap-1 text-[12px] min-w-0" :class="check.ok ? 'text-sage' : 'text-coral'" :title="checkText" data-test="module-check-result">
      <span :class="check.ok ? 'i-carbon-checkmark-filled' : 'i-carbon-warning-filled'" class="shrink-0" />
      <span class="truncate max-w-72">{{ checkText }}</span>
    </span>
    <KButton size="sm" variant="secondary" :icon="checking ? 'i-carbon-circle-dash animate-spin' : 'i-carbon-task-approved'" :disabled="checking || saving" :title="dirty ? t('studioApp.check.saveFirst') : t('studioApp.check.hint')" data-test="module-check" @click="$emit('check')">
      {{ t("studioApp.check.label") }}
    </KButton>
    <KButton size="sm" variant="secondary" :disabled="!dirty || saving" @click="$emit('discard')">
      {{ t("studio.frame.discard") }}
    </KButton>
    <KButton size="sm" variant="primary" :icon="saving ? 'i-carbon-circle-dash animate-spin' : 'i-carbon-save'" :disabled="!dirty || saving" @click="$emit('save')">
      {{ saving ? t("studio.frame.saving") : t("studio.frame.save") }}
    </KButton>
  </div>
</template>

<script setup>
import { computed } from "vue"

import { kindMeta } from "@/components/studio/app/studioKinds"
import KButton from "@/components/studio/common/KButton.vue"
import { useI18n } from "@/utils/i18n"

/** The module editor's head: what it is, unsaved state, the last Check result, and Check / Discard / Save. */
const { t } = useI18n()

const props = defineProps({
  kind: { type: String, required: true },
  name: { type: String, default: "" },
  dirty: { type: Boolean, default: false },
  saving: { type: Boolean, default: false },
  checking: { type: Boolean, default: false },
  /** The last check: `{ok, errors, loaded}` or null. */
  check: { type: Object, default: null },
})

defineEmits(["back", "save", "discard", "check"])

const meta = computed(() => kindMeta(props.kind))
const checkText = computed(() => {
  if (!props.check) return ""
  if (props.check.ok) return t("studioApp.check.ok", { name: props.check.loaded })
  const e = props.check.errors?.[0] || {}
  return e.line ? t("studioApp.check.atLine", { line: e.line, message: e.message }) : e.message || ""
})
</script>
