<template>
  <div class="h-full flex flex-col min-h-0" data-test="v2-debug-detail">
    <template v-if="mode === 'prompt'">
      <div class="h-9 shrink-0 flex items-center gap-2 px-3 border-b kt-v2-line text-xs">
        <span class="text-warm-400">{{ t("debug.target") }}</span>
        <span class="font-mono text-iolite dark:text-iolite-light">{{ promptTarget || "—" }}</span>
        <span v-if="promptFetchedAt" class="text-warm-400">· {{ t("debug.fetchedAt", { time: promptFetchedAt }) }}</span>
        <span class="flex-1" />
        <button v-if="previousText" class="px-2 py-0.5 rounded" :class="showDiff ? 'bg-iolite/15 text-iolite' : 'text-warm-500 hover:text-iolite'" @click="showDiff = !showDiff">{{ t("debug.diff") }}</button>
        <button class="px-2 py-0.5 rounded text-warm-500 hover:text-iolite" :disabled="!promptText" @click="copy(promptText)">{{ t("debug.copy") }}</button>
        <button class="px-2 py-0.5 rounded text-warm-500 hover:text-iolite" @click="$emit('refresh')"><span class="i-carbon-renew mr-1 align-[-2px]" />{{ t("debug.refresh") }}</button>
      </div>
      <div class="flex-1 min-h-0 overflow-auto p-4 font-mono text-[11px] leading-relaxed">
        <div v-if="promptLoading" class="text-warm-400">{{ t("loading") }}</div>
        <div v-else-if="promptError" class="text-coral">{{ promptError }}</div>
        <template v-else-if="showDiff && previousText">
          <div v-for="(line, i) in diffLines" :key="i" class="whitespace-pre-wrap break-words" :class="DIFF_CLASS[line.kind]">
            <span class="inline-block w-3 opacity-60">{{ DIFF_MARK[line.kind] }}</span
            >{{ line.text }}
          </div>
        </template>
        <pre v-else class="whitespace-pre-wrap break-words text-warm-700 dark:text-warm-300">{{ promptText }}</pre>
      </div>
    </template>
    <template v-else-if="row">
      <div class="h-9 shrink-0 flex items-center gap-2 px-3 border-b kt-v2-line text-xs">
        <span class="px-1.5 rounded bg-warm-100 dark:bg-warm-800 text-warm-600 dark:text-warm-300 font-mono">{{ row.kind }}</span>
        <span v-if="row.tab || row.module || row.name" class="font-mono text-iolite dark:text-iolite-light truncate">{{ row.tab || row.module || row.name }}</span>
        <span class="flex-1" />
        <button class="px-2 py-0.5 rounded text-warm-500 hover:text-iolite" @click="copy(json)">{{ t("debug.copy") }}</button>
      </div>
      <pre class="flex-1 min-h-0 overflow-auto p-4 font-mono text-[11px] leading-relaxed whitespace-pre-wrap break-words text-warm-700 dark:text-warm-300">{{ json }}</pre>
    </template>
    <div v-else class="h-full flex items-center justify-center text-xs text-warm-400">{{ t("debug.selectRow") }}</div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from "vue"

import { lineDiff, rowPayload, safeJson } from "@/components/session-v2/model/debug/debugModel"
import { useV2T } from "@/components/session-v2/model/v2Strings"

/** Detail pane of the Debug tab: the selected row as pretty JSON, or the system prompt with a diff against the previous fetch. */
const props = defineProps({
  mode: { type: String, default: "row" },
  row: { type: Object, default: null },
  promptText: { type: String, default: "" },
  previousText: { type: String, default: "" },
  promptTarget: { type: String, default: "" },
  promptFetchedAt: { type: String, default: "" },
  promptLoading: { type: Boolean, default: false },
  promptError: { type: String, default: "" },
})
defineEmits(["refresh"])

const t = useV2T()
const showDiff = ref(false)
const DIFF_CLASS = { add: "bg-aquamarine/10 text-aquamarine", del: "bg-coral/10 text-coral", same: "text-warm-600 dark:text-warm-400" }
const DIFF_MARK = { add: "+", del: "-", same: " " }

const json = computed(() => (props.row ? safeJson(rowPayload(props.row)) : ""))

const diffLines = computed(() => (showDiff.value && props.previousText && props.promptText ? lineDiff(props.previousText, props.promptText) : []))

watch(
  () => props.previousText,
  (prev) => {
    if (!prev) showDiff.value = false
  },
)

function copy(text) {
  if (text && typeof navigator !== "undefined" && navigator.clipboard) navigator.clipboard.writeText(text).catch(() => {})
}
</script>
