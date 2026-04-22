<template>
  <div class="flex flex-col gap-3 text-sm">
    <div class="flex items-center gap-2">
      <div :class="[kindIcon, 'text-lg text-iolite dark:text-iolite-light']" />
      <div class="flex-1 min-w-0">
        <div class="font-mono font-semibold text-warm-800 dark:text-warm-200 truncate">
          {{ name }}
        </div>
        <div class="text-[11px] text-warm-500 uppercase tracking-wider">
          {{ kind }}
        </div>
      </div>
      <span v-if="inherited" class="text-[10px] font-medium px-1.5 py-0.5 rounded bg-warm-200/60 dark:bg-warm-800/60 text-warm-500 dark:text-warm-400"> inherit </span>
    </div>

    <p v-if="description" class="text-sm text-warm-700 dark:text-warm-300 leading-relaxed">
      {{ description }}
    </p>
    <p v-else class="text-xs text-warm-500 italic">
      {{ t("studio.creature.detail.noDescription") }}
    </p>

    <div class="pt-2 border-t border-warm-200/70 dark:border-warm-800/70 flex gap-2">
      <KButton size="sm" variant="secondary" icon="i-carbon-settings-adjust" :disabled="true" :title="t('studio.creature.detail.optionsComingSoon')">
        {{ t("studio.creature.detail.options") }}
      </KButton>
      <KButton v-if="!inherited" size="sm" variant="ghost" icon="i-carbon-trash-can" @click="onRemove">
        {{ t("studio.creature.detail.remove") }}
      </KButton>
      <KButton v-else size="sm" variant="ghost" icon="i-carbon-edit" :disabled="true" :title="t('studio.creature.modules.convertOverride')">
        {{ t("studio.creature.modules.convertOverride") }}
      </KButton>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue"

import KButton from "@/components/studio/common/KButton.vue"
import { useStudioCatalogStore } from "@/stores/studio/catalog"
import { useStudioCreatureStore } from "@/stores/studio/creature"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()
const catalog = useStudioCatalogStore()
const creature = useStudioCreatureStore()

const props = defineProps({
  kind: { type: String, required: true }, // tool | subagent | trigger | plugin
  name: { type: String, required: true },
  inherited: { type: Boolean, default: false },
})

const description = computed(() => {
  const e = props.kind === "tool" ? catalog.toolByName(props.name) : props.kind === "subagent" ? catalog.subagentByName(props.name) : props.kind === "trigger" ? catalog.triggerByName(props.name) : null
  return e?.description || ""
})

const kindIcon = computed(() => {
  switch (props.kind) {
    case "tool":
      return "i-carbon-tool-kit"
    case "subagent":
      return "i-carbon-bot"
    case "trigger":
      return "i-carbon-alarm"
    case "plugin":
      return "i-carbon-plug"
    default:
      return "i-carbon-document"
  }
})

function onRemove() {
  creature.removeModule(props.kind, props.name)
}
</script>
