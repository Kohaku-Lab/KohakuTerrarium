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
      <span v-if="isWired" class="text-[10px] font-medium px-1.5 py-0.5 rounded bg-iolite/15 text-iolite dark:text-iolite-light">
        {{ t("studio.creature.detail.wired") }}
      </span>
    </div>

    <div v-if="loading" class="text-xs text-warm-500">
      {{ t("studio.common.loading") }}
    </div>
    <div v-else-if="!entry" class="text-xs text-warm-500 italic">
      {{ t("studio.common.empty") }}
    </div>
    <template v-else>
      <p class="text-sm text-warm-700 dark:text-warm-300 leading-relaxed">
        {{ entry.description || t("studio.creature.detail.noDescription") }}
      </p>

      <div class="grid grid-cols-2 gap-2">
        <Tag v-if="entry.execution_mode" :label="t('studio.creature.detail.executionMode')" :value="entry.execution_mode" />
        <Tag v-if="entry.needs_context != null" :label="t('studio.creature.detail.needsContext')" :value="entry.needs_context ? 'yes' : 'no'" />
      </div>

      <div class="pt-2 border-t border-warm-200/70 dark:border-warm-800/70 flex gap-2">
        <KButton v-if="!isWired" size="sm" variant="primary" icon="i-carbon-add" @click="onAdd">
          {{ t("studio.creature.detail.add") }}
        </KButton>
        <KButton v-else size="sm" variant="secondary" icon="i-carbon-subtract" @click="onRemove">
          {{ t("studio.creature.detail.remove") }}
        </KButton>
        <KButton size="sm" variant="ghost" icon="i-carbon-settings-adjust" :disabled="true" :title="t('studio.creature.detail.customizeComingSoon')">
          {{ t("studio.creature.detail.customize") }}
        </KButton>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed } from "vue"

import KButton from "@/components/studio/common/KButton.vue"
import { useStudioCatalogStore } from "@/stores/studio/catalog"
import { useStudioCreatureStore } from "@/stores/studio/creature"
import { useI18n } from "@/utils/i18n"

import Tag from "./Tag.vue"

const { t } = useI18n()
const catalog = useStudioCatalogStore()
const creature = useStudioCreatureStore()

const props = defineProps({
  kind: { type: String, required: true }, // "tools" | "subagents" | "triggers"
  name: { type: String, required: true },
})

const loading = computed(() => catalog.loading && !catalog.loaded)

const entry = computed(() => {
  switch (props.kind) {
    case "tools":
      return catalog.toolByName(props.name)
    case "subagents":
      return catalog.subagentByName(props.name)
    case "triggers":
      return catalog.triggerByName(props.name)
    default:
      return null
  }
})

function storeKind(poolKind) {
  switch (poolKind) {
    case "tools":
      return "tool"
    case "subagents":
      return "subagent"
    case "triggers":
      return "trigger"
    default:
      return poolKind
  }
}

const isWired = computed(() => creature.isWired(storeKind(props.kind), props.name))

const kindIcon = computed(() => {
  switch (props.kind) {
    case "tools":
      return "i-carbon-tool-kit"
    case "subagents":
      return "i-carbon-bot"
    case "triggers":
      return "i-carbon-alarm"
    default:
      return "i-carbon-document"
  }
})

function onAdd() {
  creature.addModule(storeKind(props.kind), props.name)
}

function onRemove() {
  creature.removeModule(storeKind(props.kind), props.name)
}
</script>
