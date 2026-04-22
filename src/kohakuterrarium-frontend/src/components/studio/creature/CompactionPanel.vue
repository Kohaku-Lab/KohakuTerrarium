<template>
  <FoldOut :title="t('studio.creature.advanced.compact')" icon="i-carbon-compress" :count="active ? 1 : 0">
    <div class="flex flex-col gap-3">
      <KField :label="t('studio.compact.enable')">
        <KCheckbox :model-value="enabled" :label="t('studio.compact.enableHint')" @update:model-value="onEnableToggle" />
      </KField>

      <template v-if="enabled">
        <div class="grid grid-cols-2 gap-3">
          <KField :label="t('studio.compact.maxTokens')" :hint="t('studio.compact.maxTokensHint')">
            <KInput :model-value="numStr(compact?.max_tokens)" type="number" placeholder="auto" @update:model-value="onNum('max_tokens', $event)" />
          </KField>
          <KField :label="t('studio.compact.threshold')" :hint="t('studio.compact.thresholdHint')">
            <KInput :model-value="numStr(compact?.threshold)" type="number" placeholder="0.75" @update:model-value="onNum('threshold', $event)" />
          </KField>
          <KField :label="t('studio.compact.target')" :hint="t('studio.compact.targetHint')">
            <KInput :model-value="numStr(compact?.target)" type="number" placeholder="0.5" @update:model-value="onNum('target', $event)" />
          </KField>
          <KField :label="t('studio.compact.keepRecentTurns')">
            <KInput :model-value="numStr(compact?.keep_recent_turns)" type="number" placeholder="6" @update:model-value="onNum('keep_recent_turns', $event)" />
          </KField>
        </div>
      </template>
    </div>
  </FoldOut>
</template>

<script setup>
import { computed } from "vue"

import KCheckbox from "@/components/studio/common/KCheckbox.vue"
import KField from "@/components/studio/common/KField.vue"
import KInput from "@/components/studio/common/KInput.vue"
import { useI18n } from "@/utils/i18n"

import FoldOut from "./FoldOut.vue"

const { t } = useI18n()

const props = defineProps({
  compact: { type: Object, default: null },
})

const emit = defineEmits(["patch"])

const enabled = computed(() => props.compact != null)
const active = computed(() => props.compact && Object.keys(props.compact).length > 0)

function numStr(v) {
  if (v == null) return ""
  return String(v)
}

function onEnableToggle(checked) {
  if (!checked) {
    emit("patch", undefined, undefined) // clear whole compact section
  } else {
    // Seed with an empty object — the runtime default will fill in.
    emit("patch", "", {})
  }
}

function onNum(key, value) {
  if (value === "") {
    emit("patch", key, undefined)
    return
  }
  const n = Number(value)
  emit("patch", key, Number.isFinite(n) ? n : undefined)
}
</script>
