<template>
  <SectionCard :title="t('studio.creature.identity.title')" icon="i-carbon-id">
    <div class="grid grid-cols-2 gap-3">
      <KField :label="t('studio.creature.identity.name')">
        <KInput :model-value="config.name || ''" @update:model-value="patch('name', $event)" />
      </KField>
      <KField :label="t('studio.creature.identity.version')">
        <KInput :model-value="config.version || ''" @update:model-value="patch('version', $event)" />
      </KField>
      <KField :label="t('studio.creature.identity.baseConfig')" class="col-span-2">
        <KInput :model-value="config.base_config || ''" :placeholder="t('studio.creature.identity.noBase')" @update:model-value="patch('base_config', $event || null)" />
      </KField>
      <KField :label="t('studio.creature.identity.description')" class="col-span-2">
        <KInput :model-value="config.description || ''" :placeholder="t('studio.creature.identity.noDescription')" @update:model-value="patch('description', $event)" />
      </KField>
    </div>

    <div class="mt-4 pt-3 border-t border-warm-200/60 dark:border-warm-800/60">
      <div class="text-[11px] uppercase tracking-wider font-medium text-warm-500 dark:text-warm-400 mb-2">
        {{ t("studio.creature.identity.controller") }}
      </div>
      <div class="grid grid-cols-2 gap-3">
        <KField :label="t('studio.creature.identity.reasoning')" :hint="t('studio.creature.identity.reasoningHint')">
          <KSelect :model-value="reasoningValue" :options="reasoningOptions" @update:model-value="onReasoning($event)" />
        </KField>
        <KField :label="t('studio.creature.identity.toolFormat')" :hint="t('studio.creature.identity.toolFormatHint')">
          <KSelect :model-value="toolFormatValue" :options="toolFormatOptions" @update:model-value="onToolFormat($event)" />
        </KField>
        <KField :label="t('studio.creature.identity.temperature')" :hint="t('studio.creature.identity.temperatureHint')">
          <KInput :model-value="temperatureValue" type="number" :placeholder="t('studio.creature.identity.temperatureDefault')" @update:model-value="onTemperature($event)" />
        </KField>
        <KField :label="t('studio.creature.identity.maxMessages')">
          <KInput :model-value="String(config.max_messages ?? '')" type="number" :placeholder="t('studio.creature.identity.maxMessagesPlaceholder')" @update:model-value="onMaxMessages($event)" />
        </KField>
      </div>

      <!-- Model lives in the status footer. Info-only reminder here. -->
      <div class="mt-3 flex items-center gap-2 px-3 py-1.5 rounded bg-warm-100/60 dark:bg-warm-900/60 text-[11px] text-warm-500">
        <div class="i-carbon-information text-xs" />
        <span class="flex-1">
          {{ t("studio.creature.identity.modelHint") }}
        </span>
        <span class="font-mono text-warm-700 dark:text-warm-300">
          {{ modelSummary }}
        </span>
      </div>
    </div>
  </SectionCard>
</template>

<script setup>
import { computed } from "vue"

import KField from "@/components/studio/common/KField.vue"
import KInput from "@/components/studio/common/KInput.vue"
import KSelect from "@/components/studio/common/KSelect.vue"
import { useI18n } from "@/utils/i18n"

import SectionCard from "./SectionCard.vue"

const { t } = useI18n()

const props = defineProps({
  config: { type: Object, default: () => ({}) },
  effective: { type: Object, default: null },
})

const emit = defineEmits(["patch"])

function patch(path, value) {
  emit("patch", path, value)
}

const reasoningOptions = computed(() => [
  { value: "", label: t("studio.creature.identity.inheritDefault") },
  { value: "none", label: "none" },
  { value: "minimal", label: "minimal" },
  { value: "low", label: "low" },
  { value: "medium", label: "medium" },
  { value: "high", label: "high" },
  { value: "xhigh", label: "xhigh" },
])

const toolFormatOptions = computed(() => [
  { value: "", label: t("studio.creature.identity.inheritDefault") },
  { value: "native", label: "native" },
  { value: "bracket", label: "bracket" },
  { value: "xml", label: "xml" },
])

// Controller fields live under `config.controller.*` in kt-biome-style
// YAML. Fall back to top-level for legacy configs that put them at the
// root (core reads both locations).
const controller = computed(() => props.config.controller || {})

const reasoningValue = computed(() => controller.value.reasoning_effort ?? props.config.reasoning_effort ?? "")

const toolFormatValue = computed(() => {
  const v = controller.value.tool_format ?? props.config.tool_format
  if (!v || typeof v !== "string") return ""
  return v
})

const temperatureValue = computed(() => {
  const v = controller.value.temperature ?? props.config.temperature
  return v == null ? "" : String(v)
})

const modelSummary = computed(() => {
  const llm = controller.value.llm ?? props.config.llm_profile
  if (llm) return `@${llm}`
  const model = controller.value.model ?? props.config.model
  if (model) return model
  if (props.effective?.model) return `${props.effective.model} (resolved)`
  return t("studio.creature.identity.modelDefault")
})

function onReasoning(v) {
  patch("controller.reasoning_effort", v === "" ? undefined : v)
}

function onToolFormat(v) {
  patch("controller.tool_format", v === "" ? undefined : v)
}

function onTemperature(v) {
  if (v === "") {
    patch("controller.temperature", undefined)
    return
  }
  const n = Number(v)
  patch("controller.temperature", Number.isFinite(n) ? n : undefined)
}

function onMaxMessages(v) {
  if (v === "") {
    patch("max_messages", undefined)
    return
  }
  const n = Math.max(0, Math.floor(Number(v)))
  patch("max_messages", Number.isFinite(n) ? n : undefined)
}
</script>
