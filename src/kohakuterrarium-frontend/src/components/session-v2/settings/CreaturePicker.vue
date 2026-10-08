<template>
  <label v-if="names.length > 1" class="flex items-center gap-2 text-sm text-warm-500 shrink-0">
    <span>{{ t("set.creature") }}</span>
    <el-select :model-value="modelValue" size="small" class="!w-44" data-test="v2-settings-creature" @change="$emit('update:modelValue', $event)">
      <el-option v-for="name in names" :key="name" :label="name" :value="name" />
    </el-select>
  </label>
</template>

<script setup>
import { computed } from "vue"

import { creatureNames } from "@/components/session-v2/model/settings/settingsModel"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { useV2T } from "@/components/session-v2/model/v2Strings"

/** Which creature a per-creature settings section edits; hidden for a solo session. */
defineProps({ modelValue: { type: String, default: null } })
defineEmits(["update:modelValue"])

const t = useV2T()
const session = useSessionV2()
const names = computed(() => creatureNames(session.instance.value))
</script>
