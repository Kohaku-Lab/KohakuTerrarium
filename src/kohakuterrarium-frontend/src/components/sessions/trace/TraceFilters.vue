<template>
  <div class="card px-3 py-2 flex flex-wrap items-center gap-3 text-[12px]">
    <!-- Agent picker -->
    <div class="flex items-center gap-1.5">
      <span class="text-warm-400">{{ t("sessionViewer.trace.filters.agent") }}:</span>
      <el-select v-model="agentModel" size="small" style="width: 140px" @change="onAgentChange">
        <el-option v-for="a in agents" :key="a" :value="a" :label="a" />
      </el-select>
    </div>

    <!-- Errors-only chip -->
    <button class="px-2 py-0.5 rounded border text-[11px] flex items-center gap-1" :class="modelValue.errorsOnly ? 'border-coral bg-coral/10 text-coral' : 'border-warm-300 dark:border-warm-700 text-warm-500 hover:text-warm-700'" @click="toggleErrorsOnly">
      <div class="i-carbon-warning-alt" />
      <span>{{ t("sessionViewer.trace.filters.errorsOnly") }}</span>
    </button>

    <!-- Type chips -->
    <div class="flex items-center gap-1 flex-wrap">
      <span class="text-warm-400 mr-1">{{ t("sessionViewer.trace.filters.types") }}:</span>
      <button v-for="t2 in TYPE_CHIPS" :key="t2.id" class="px-1.5 py-0.5 rounded text-[11px] border" :class="isTypeActive(t2.id) ? 'border-iolite bg-iolite/10 text-iolite' : 'border-warm-300 dark:border-warm-700 text-warm-500 hover:text-warm-700'" @click="toggleType(t2.id)">{{ t2.label }}</button>
    </div>

    <div class="flex-1" />

    <!-- Live attach -->
    <div class="flex items-center gap-1.5">
      <button class="px-2 py-0.5 rounded border text-[11px] flex items-center gap-1.5" :class="modelValue.live ? 'border-aquamarine bg-aquamarine/10 text-aquamarine' : 'border-warm-300 dark:border-warm-700 text-warm-500 hover:text-warm-700'" @click="toggleLive">
        <div class="w-1.5 h-1.5 rounded-full" :class="modelValue.live ? 'bg-aquamarine animate-pulse' : 'bg-warm-400'" />
        <span>{{ t("sessionViewer.trace.filters.live") }}</span>
      </button>
      <span v-if="modelValue.live && liveStatus" class="text-[10px] text-warm-400">{{ liveStatus }}</span>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue"

import { useI18n } from "@/utils/i18n"

const { t } = useI18n()

const props = defineProps({
  modelValue: {
    type: Object,
    required: true,
  },
  agents: { type: Array, default: () => [] },
  liveStatus: { type: String, default: "" },
})
const emit = defineEmits(["update:modelValue"])

const TYPE_CHIPS = [
  { id: "tool", label: "tool", matches: ["tool_call", "tool_result", "tool_error"] },
  { id: "subagent", label: "sub-agent", matches: ["subagent_call", "subagent_result", "subagent_error"] },
  { id: "plugin", label: "plugin", matches: ["plugin_hook_timing", "plugin_hook"] },
  { id: "compact", label: "compact", matches: ["compact_start", "compact_complete", "compact_decision", "compact_replace"] },
  { id: "tokens", label: "tokens", matches: ["token_usage", "turn_token_usage"] },
  { id: "text", label: "text", matches: ["text_chunk", "text"] },
]

const agentModel = computed({
  get: () => props.modelValue.agent || "",
  set: (v) => emit("update:modelValue", { ...props.modelValue, agent: v }),
})

function onAgentChange(value) {
  emit("update:modelValue", { ...props.modelValue, agent: value })
}

function isTypeActive(id) {
  return (props.modelValue.typeChips || []).includes(id)
}

function toggleType(id) {
  const cur = new Set(props.modelValue.typeChips || [])
  if (cur.has(id)) cur.delete(id)
  else cur.add(id)
  emit("update:modelValue", { ...props.modelValue, typeChips: [...cur] })
}

function toggleErrorsOnly() {
  emit("update:modelValue", {
    ...props.modelValue,
    errorsOnly: !props.modelValue.errorsOnly,
  })
}

function toggleLive() {
  emit("update:modelValue", { ...props.modelValue, live: !props.modelValue.live })
}

defineExpose({ TYPE_CHIPS })
</script>
