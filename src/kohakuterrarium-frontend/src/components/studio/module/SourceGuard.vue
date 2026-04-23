<template>
  <div class="h-full flex flex-col items-center justify-center gap-3 text-center px-6">
    <div class="i-carbon-locked text-4xl text-warm-400" />
    <div class="text-sm text-warm-700 dark:text-warm-300">
      {{ t("studio.module.guard.notEditable", { kind, name }) }}
    </div>
    <div class="text-xs text-warm-500 font-mono bg-warm-100 dark:bg-warm-900 px-2 py-0.5 rounded">
      {{ sourceLabel }}
    </div>
    <div class="text-xs text-warm-500 max-w-sm">
      {{ t("studio.module.guard.hint") }}
    </div>
    <KButton variant="secondary" size="sm" icon="i-carbon-arrow-left" @click="$emit('back')">
      {{ t("studio.frame.back") }}
    </KButton>
  </div>
</template>

<script setup>
import { computed } from "vue"

import KButton from "@/components/studio/common/KButton.vue"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()

const props = defineProps({
  kind: { type: String, required: true },
  name: { type: String, required: true },
  source: { type: String, required: true },
})

defineEmits(["back"])

const sourceLabel = computed(() => {
  if (props.source === "workspace-manifest") return t("studio.dashboard.sourceManifest")
  if (props.source.startsWith("package:")) {
    return t("studio.dashboard.sourcePackage", {
      name: props.source.slice("package:".length),
    })
  }
  return props.source
})
</script>
