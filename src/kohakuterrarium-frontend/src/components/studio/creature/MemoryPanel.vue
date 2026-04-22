<template>
  <FoldOut :title="t('studio.creature.advanced.memory')" icon="i-carbon-data-base" :count="activeCount">
    <div class="flex flex-col gap-3">
      <KField :label="t('studio.memory.provider')" :hint="t('studio.memory.providerHint')">
        <KSelect :model-value="provider" :options="providerOptions" @update:model-value="onProvider($event)" />
      </KField>

      <KField v-if="provider !== 'none' && provider !== ''" :label="t('studio.memory.model')" :hint="modelHint">
        <div class="flex flex-col gap-1.5">
          <KInput :model-value="model" :placeholder="t('studio.memory.modelPlaceholder')" @update:model-value="onModel($event)" />
          <div v-if="availablePresets.length" class="flex flex-wrap gap-1">
            <button v-for="p in availablePresets" :key="p.key" type="button" :class="['text-[10px] px-1.5 py-0.5 rounded font-mono transition-colors', model === `@${p.key}` ? 'bg-iolite/20 text-iolite dark:text-iolite-light' : 'bg-warm-100 dark:bg-warm-800 text-warm-600 dark:text-warm-400 hover:bg-warm-200 dark:hover:bg-warm-700']" :title="p.model" @click="onModel(`@${p.key}`)">@{{ p.key }}</button>
          </div>
        </div>
      </KField>

      <KField v-if="provider === 'sentence-transformer'" :label="t('studio.memory.device')">
        <KSelect :model-value="device" :options="deviceOptions" @update:model-value="$emit('patch', 'embedding.device', $event)" />
      </KField>

      <KField v-if="provider !== 'none' && provider !== ''" :label="t('studio.memory.dimensions')" :hint="t('studio.memory.dimensionsHint')">
        <KInput :model-value="String(dimensions ?? '')" type="number" :placeholder="t('studio.memory.dimensionsPlaceholder')" @update:model-value="onDimensions($event)" />
      </KField>
    </div>
  </FoldOut>
</template>

<script setup>
import { computed, onMounted, ref } from "vue"

import KField from "@/components/studio/common/KField.vue"
import KInput from "@/components/studio/common/KInput.vue"
import KSelect from "@/components/studio/common/KSelect.vue"
import { catalogAPI } from "@/utils/studio/api"
import { useI18n } from "@/utils/i18n"

import FoldOut from "./FoldOut.vue"

const { t } = useI18n()

const props = defineProps({
  memory: { type: Object, default: () => ({}) },
})

const emit = defineEmits(["patch"])

const embedding = computed(() => props.memory?.embedding ?? {})
const provider = computed(() => embedding.value?.provider ?? "")
const model = computed(() => embedding.value?.model ?? "")
const dimensions = computed(() => embedding.value?.dimensions)
const device = computed(() => embedding.value?.device ?? "cpu")

const activeCount = computed(() => (provider.value ? 1 : 0))

const providerOptions = [
  { value: "", label: t("studio.memory.providerInherit") },
  { value: "auto", label: "auto (detect best)" },
  { value: "model2vec", label: "model2vec (no torch)" },
  { value: "sentence-transformer", label: "sentence-transformer" },
  { value: "api", label: "api (OpenAI-compatible)" },
  { value: "none", label: "none (disable)" },
]

const deviceOptions = [
  { value: "cpu", label: "cpu" },
  { value: "cuda", label: "cuda (GPU)" },
]

const presetsRaw = ref({ model2vec: {}, "sentence-transformer": {} })

onMounted(async () => {
  try {
    presetsRaw.value = await catalogAPI.embeddingPresets()
  } catch {
    /* ignore — inputs still usable without presets */
  }
})

const availablePresets = computed(() => {
  const p = provider.value
  if (p !== "model2vec" && p !== "sentence-transformer") return []
  const entries = presetsRaw.value?.[p] ?? {}
  return Object.entries(entries).map(([k, v]) => ({ key: k, model: v.model }))
})

const modelHint = computed(() => {
  if (provider.value === "model2vec" || provider.value === "sentence-transformer") {
    return t("studio.memory.modelHintPreset")
  }
  if (provider.value === "api") {
    return t("studio.memory.modelHintApi")
  }
  return ""
})

function onProvider(value) {
  // Empty string = "inherit runtime default" — drop the field entirely
  // so core's `auto` detection kicks in.
  if (!value) {
    emit("patch", "embedding.provider", undefined)
    return
  }
  emit("patch", "embedding.provider", value)
}

function onModel(value) {
  emit("patch", "embedding.model", value || undefined)
}

function onDimensions(value) {
  const v = value === "" ? undefined : Number(value)
  emit("patch", "embedding.dimensions", Number.isFinite(v) ? v : undefined)
}
</script>
