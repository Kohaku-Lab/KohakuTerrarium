<template>
  <button class="w-full text-left flex items-start gap-2 text-[12px] py-0.5 px-1 rounded hover:bg-warm-50 dark:hover:bg-warm-800/50 cursor-pointer" :class="selected ? 'bg-iolite/10 ring-1 ring-iolite/30' : ''" @click="onClick">
    <span class="font-mono text-warm-400 w-16 shrink-0">{{ formatTime(event.ts) }}</span>
    <span class="w-4 shrink-0 mt-0.5" :class="iconClass" />
    <span class="font-mono w-32 shrink-0 truncate" :class="typeClass">{{ event.type }}</span>
    <span class="flex-1 min-w-0 truncate text-warm-700 dark:text-warm-300">{{ summary }}</span>
    <span v-if="durationMs != null" class="font-mono text-warm-400 shrink-0">⏱ {{ formatDuration(durationMs) }}</span>
  </button>
</template>

<script setup>
import { computed } from "vue"

const props = defineProps({
  event: { type: Object, required: true },
  selected: { type: Boolean, default: false },
})
const emit = defineEmits(["select"])

function onClick() {
  emit("select", props.event)
}

const TYPE_TONE = {
  tool_call: "text-warm-700 dark:text-warm-300",
  tool_result: "text-warm-500",
  tool_error: "text-coral",
  subagent_call: "text-iolite",
  subagent_result: "text-iolite/70",
  subagent_error: "text-coral",
  plugin_hook_timing: "text-aquamarine",
  compact_start: "text-amber",
  compact_complete: "text-amber",
  compact_decision: "text-amber",
  compact_replace: "text-amber",
  user_input: "text-sage",
  user_message: "text-sage",
  text_chunk: "text-warm-500",
  turn_token_usage: "text-taaffeite",
  token_usage: "text-taaffeite",
  processing_error: "text-coral",
}

const ICON_MAP = {
  tool_call: "i-carbon-arrow-right",
  tool_result: "i-carbon-arrow-left",
  tool_error: "i-carbon-warning-alt",
  subagent_call: "i-carbon-bot",
  subagent_result: "i-carbon-bot",
  subagent_error: "i-carbon-warning-alt",
  plugin_hook_timing: "i-carbon-plug",
  compact_start: "i-carbon-compress",
  compact_complete: "i-carbon-compress",
  compact_decision: "i-carbon-compress",
  user_input: "i-carbon-user",
  user_message: "i-carbon-user",
  text_chunk: "i-carbon-text-creation",
  turn_token_usage: "i-carbon-chart-bar",
  token_usage: "i-carbon-chart-bar",
  processing_error: "i-carbon-warning-alt",
}

const typeClass = computed(() => TYPE_TONE[props.event.type] || "text-warm-500")
const iconClass = computed(() => ICON_MAP[props.event.type] || "i-carbon-circle-dash")

const summary = computed(() => {
  const e = props.event
  if (!e) return ""
  if (typeof e.content === "string") return e.content.slice(0, 200)
  if (typeof e.text === "string") return e.text.slice(0, 200)
  if (typeof e.output === "string") return e.output.slice(0, 200)
  if (e.tool) return String(e.tool)
  if (e.name) return String(e.name)
  if (e.error) return String(e.error)
  if (e.summary) return String(e.summary)
  return ""
})

const durationMs = computed(() => {
  const e = props.event
  if (e == null) return null
  if (typeof e.duration_ms === "number") return e.duration_ms
  if (typeof e.elapsed_ms === "number") return e.elapsed_ms
  return null
})

function formatTime(ts) {
  if (!ts) return "—"
  try {
    const d = new Date(Number(ts) * 1000)
    return d.toLocaleTimeString(undefined, { hour12: false })
  } catch {
    return String(ts)
  }
}

function formatDuration(ms) {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}
</script>
