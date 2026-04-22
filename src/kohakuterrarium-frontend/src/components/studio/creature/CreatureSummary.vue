<template>
  <div class="flex flex-col gap-3 text-sm">
    <div>
      <div class="text-[11px] uppercase tracking-wider text-warm-500 mb-1">
        {{ t("studio.creature.detail.name") }}
      </div>
      <div class="font-mono text-warm-800 dark:text-warm-200 font-medium">
        {{ name || "—" }}
      </div>
    </div>

    <div v-if="effective?.model">
      <div class="text-[11px] uppercase tracking-wider text-warm-500 mb-1">
        {{ t("studio.creature.detail.resolvedModel") }}
      </div>
      <div class="font-mono text-warm-800 dark:text-warm-200 text-xs">
        {{ effective.model }}
      </div>
    </div>

    <div v-if="chain.length">
      <div class="text-[11px] uppercase tracking-wider text-warm-500 mb-1">
        {{ t("studio.creature.detail.inheritance") }}
      </div>
      <div class="flex flex-col gap-0.5">
        <div v-for="(c, i) in chain" :key="i" class="flex items-center gap-1.5 text-xs font-mono text-warm-700 dark:text-warm-300">
          <div class="i-carbon-chevron-right text-[10px] text-warm-400" />
          <span class="truncate">{{ c }}</span>
        </div>
      </div>
    </div>

    <div class="grid grid-cols-2 gap-2 pt-2 border-t border-warm-200/70 dark:border-warm-800/70">
      <StatCell :label="t('studio.creature.detail.tools')" :value="toolCount" />
      <StatCell :label="t('studio.creature.detail.subagents')" :value="subagentCount" />
      <StatCell :label="t('studio.creature.detail.triggers')" :value="triggerCount" />
      <StatCell :label="t('studio.creature.detail.plugins')" :value="pluginCount" />
    </div>

    <div class="pt-2 text-[11px] text-warm-500 italic">
      {{ t("studio.creature.detail.hoverHint") }}
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue"

import { useI18n } from "@/utils/i18n"

import StatCell from "./StatCell.vue"

const { t } = useI18n()

const props = defineProps({
  name: { type: String, default: "" },
  config: { type: Object, default: () => ({}) },
  effective: { type: Object, default: null },
})

const chain = computed(() => props.effective?.inheritance_chain || [])

const toolCount = computed(() => (props.effective?.tools || props.config.tools || []).length)
const subagentCount = computed(() => (props.effective?.subagents || props.config.subagents || []).length)
const triggerCount = computed(() => (props.config.triggers || []).length)
const pluginCount = computed(() => (props.config.plugins || []).length)
</script>
