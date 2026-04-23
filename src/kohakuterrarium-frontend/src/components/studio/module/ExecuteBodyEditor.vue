<template>
  <div class="flex flex-col rounded border border-warm-200 dark:border-warm-800 overflow-hidden" :style="{ height: height }">
    <div class="shrink-0 flex items-center gap-2 px-2 h-7 border-b border-warm-200 dark:border-warm-800 bg-warm-100/60 dark:bg-warm-900/60 text-[11px] text-warm-500">
      <div class="i-carbon-function text-sm" />
      <span class="font-mono"> def {{ methodName }}({{ methodSignature }}): </span>
      <div class="flex-1" />
      <span v-if="readOnly" class="uppercase tracking-wider text-[10px] px-1 rounded bg-warm-200 dark:bg-warm-800">read-only</span>
    </div>
    <div class="flex-1 min-h-0">
      <MonacoEditor language="python" :model-value="displayValue" :read-only="readOnly" :minimal="false" @update:model-value="onChange" @save="$emit('save')" />
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue"

import MonacoEditor from "@/components/studio/code/MonacoEditor.vue"

/**
 * Editor scoped to the body of a single function.
 *
 * The parent stores the body with no leading indent (the codegen layer
 * re-indents it when rendering the class). The editor displays it as-is;
 * we emit it back unchanged. Users can hit Ctrl/Cmd-S inside Monaco
 * to bubble a save event.
 *
 * Blank body → one-line placeholder so Monaco doesn't look empty; we
 * treat pure-whitespace as equivalent to "".
 */

const props = defineProps({
  modelValue: { type: String, default: "" },
  methodName: { type: String, default: "_execute" },
  methodSignature: { type: String, default: "self, args" },
  readOnly: { type: Boolean, default: false },
  height: { type: String, default: "280px" },
})

const emit = defineEmits(["update:modelValue", "save"])

const displayValue = computed(() => props.modelValue || "")

function onChange(next) {
  emit("update:modelValue", next ?? "")
}
</script>
