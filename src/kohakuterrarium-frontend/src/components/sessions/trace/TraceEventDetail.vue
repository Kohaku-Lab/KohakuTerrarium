<template>
  <div v-if="event" class="flex flex-col gap-3 text-[12px] h-full min-h-0 overflow-hidden">
    <!-- Header: type + time + duration -->
    <div class="shrink-0 flex flex-col gap-1.5 pb-2 border-b border-warm-200 dark:border-warm-700">
      <div class="flex items-center gap-2">
        <span class="i-carbon-circle-dash" :class="iconClass" />
        <span class="font-mono font-medium" :class="typeClass">{{ event.type }}</span>
        <span v-if="event.event_id != null" class="font-mono text-[10px] text-warm-400 ml-auto">#{{ event.event_id }}</span>
      </div>
      <div class="flex items-center gap-3 text-[11px] text-warm-500">
        <span v-if="event.ts" class="font-mono">{{ formatTime(event.ts) }}</span>
        <span v-if="event.turn_index != null">turn {{ event.turn_index }}</span>
        <span v-if="durationMs != null" class="font-mono">⏱ {{ formatDuration(durationMs) }}</span>
      </div>
    </div>

    <!-- Sub-agent navigation -->
    <div v-if="subagentRef" class="shrink-0 card p-2 flex items-center gap-2 bg-iolite/5 border-iolite/20">
      <div class="i-carbon-bot text-iolite" />
      <div class="flex flex-col flex-1 min-w-0">
        <div class="text-[11px] text-warm-400 uppercase tracking-wider">{{ t("sessionViewer.tree.attached") }}</div>
        <div class="font-mono text-warm-700 dark:text-warm-300 truncate">{{ subagentRef.label }}</div>
      </div>
      <button v-if="subagentRef.namespace" class="text-[11px] px-2 py-1 rounded border border-iolite/40 text-iolite hover:bg-iolite/10" @click="$emit('open-agent', subagentRef.namespace)">{{ t("sessionViewer.detail.openSubagent") }}</button>
    </div>

    <!-- Primary content (text-y fields) -->
    <div v-if="primaryText" class="shrink-0 flex flex-col gap-1">
      <div class="text-[10px] uppercase tracking-wider text-warm-400">{{ t("sessionViewer.detail.content") }}</div>
      <div class="font-mono text-warm-700 dark:text-warm-300 whitespace-pre-wrap break-words bg-warm-50 dark:bg-warm-800 rounded p-2 max-h-48 overflow-y-auto">{{ primaryText }}</div>
    </div>

    <!-- Image artifact preview (single-image legacy fields) -->
    <div v-if="imageUrl" class="shrink-0 flex flex-col gap-1">
      <div class="text-[10px] uppercase tracking-wider text-warm-400">{{ t("sessionViewer.detail.image") }}</div>
      <img :src="imageUrl" class="max-h-64 object-contain rounded border border-warm-200 dark:border-warm-700" alt="event artifact" />
    </div>

    <!-- Multi-modal images parsed out of structured content -->
    <div v-if="contentImages.length" class="shrink-0 flex flex-col gap-1">
      <div class="text-[10px] uppercase tracking-wider text-warm-400">{{ t("sessionViewer.detail.image") }} ({{ contentImages.length }})</div>
      <div class="flex flex-wrap gap-2">
        <img v-for="(url, i) in contentImages" :key="i" :src="url" class="max-h-32 object-contain rounded border border-warm-200 dark:border-warm-700" :alt="'attachment ' + (i + 1)" />
      </div>
    </div>

    <!-- Token usage breakdown -->
    <div v-if="tokens" class="shrink-0 flex flex-col gap-1">
      <div class="text-[10px] uppercase tracking-wider text-warm-400">{{ t("sessionViewer.detail.tokens") }}</div>
      <div class="grid grid-cols-2 gap-2 font-mono">
        <div v-if="tokens.in != null"><span class="text-warm-400">in:</span> {{ tokens.in }}</div>
        <div v-if="tokens.out != null"><span class="text-warm-400">out:</span> {{ tokens.out }}</div>
        <div v-if="tokens.cached != null"><span class="text-warm-400">cached:</span> {{ tokens.cached }}</div>
        <div v-if="tokens.total != null"><span class="text-warm-400">total:</span> {{ tokens.total }}</div>
      </div>
    </div>

    <!-- Raw JSON (full) -->
    <div class="flex-1 min-h-0 flex flex-col gap-1 overflow-hidden">
      <div class="text-[10px] uppercase tracking-wider text-warm-400 shrink-0">{{ t("sessionViewer.detail.rawJson") }}</div>
      <pre class="flex-1 min-h-0 font-mono text-[11px] text-warm-700 dark:text-warm-300 whitespace-pre-wrap break-words bg-warm-50 dark:bg-warm-800 rounded p-2 overflow-auto">{{ rawJson }}</pre>
    </div>
  </div>
  <div v-else class="text-secondary text-sm p-4 text-center">{{ t("sessionViewer.detail.empty") }}</div>
</template>

<script setup>
import { computed } from "vue"
import { useRoute } from "vue-router"

import { extractTextPreview, listAttachments } from "@/utils/multimodal"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()
const route = useRoute()

const props = defineProps({
  event: { type: Object, default: null },
})
defineEmits(["open-agent"])

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

const typeClass = computed(() => TYPE_TONE[props.event?.type] || "text-warm-500")
const iconClass = computed(() => ICON_MAP[props.event?.type] || "i-carbon-circle-dash")

const primaryText = computed(() => {
  const e = props.event
  if (!e) return ""
  // Multi-modal events ship ``content`` as a list of parts; flatten
  // via extractTextPreview (no length cap so the detail pane shows
  // the full text).
  const fromContent = extractTextPreview(e.content, Number.MAX_SAFE_INTEGER)
  if (fromContent) return fromContent
  const fromText = extractTextPreview(e.text, Number.MAX_SAFE_INTEGER)
  if (fromText) return fromText
  const fromOutput = extractTextPreview(e.output, Number.MAX_SAFE_INTEGER)
  if (fromOutput) return fromOutput
  if (typeof e.error === "string" && e.error) return e.error
  return ""
})

// Image attachments parsed out of multi-modal content so the detail
// pane can render them alongside the text. Falls back to legacy
// single-image fields handled below.
const contentImages = computed(() => {
  const e = props.event
  const out = []
  for (const field of ["content", "text", "output"]) {
    const list = listAttachments(e?.[field])
    for (const a of list) {
      if (a.kind === "image" && a.url) out.push(a.url)
    }
  }
  return out
})

const imageUrl = computed(() => {
  const e = props.event
  if (!e) return ""
  // assistant_image / image_gen events may carry an artifact URL or
  // data URL on a few possible field names; try each in turn.
  if (typeof e.url === "string" && e.url) return e.url
  if (typeof e.image_url === "string" && e.image_url) return e.image_url
  if (typeof e.artifact_url === "string" && e.artifact_url) return e.artifact_url
  // Resolve relative artifact paths against the session artifacts route.
  const sessionName = route.params?.name
  if (typeof e.artifact === "string" && e.artifact && sessionName) {
    return `/api/sessions/${encodeURIComponent(sessionName)}/artifacts/${e.artifact}`
  }
  return ""
})

const tokens = computed(() => {
  const e = props.event
  if (!e) return null
  const tin = e.prompt_tokens ?? e.tokens_in ?? null
  const tout = e.completion_tokens ?? e.tokens_out ?? null
  const cached = e.cached_tokens ?? e.tokens_cached ?? null
  const total = e.total_tokens ?? null
  if (tin == null && tout == null && cached == null && total == null) return null
  return { in: tin, out: tout, cached, total }
})

const subagentRef = computed(() => {
  const e = props.event
  if (!e) return null
  const isSub = String(e.type || "").startsWith("subagent_")
  if (!isSub) return null
  // Best-effort namespace recovery — backend writes nested-agent
  // namespaces under ``<host>:<name>:<run>:e<seq>`` in the events
  // table. We have parts of that here but not always; offer the
  // navigate button only when we can construct the param.
  const name = e.name || e.subagent_name || ""
  const run = e.run ?? e.subagent_run ?? null
  if (!name) return null
  const label = run != null ? `${name} (run ${run})` : String(name)
  // Namespace is best-effort; the trace tab agent filter accepts any
  // string from /tree's attached-agent list.
  const namespace = e.namespace || (run != null ? `${name}:${run}` : name)
  return { label, namespace }
})

const durationMs = computed(() => {
  const e = props.event
  if (e == null) return null
  if (typeof e.duration_ms === "number") return e.duration_ms
  if (typeof e.elapsed_ms === "number") return e.elapsed_ms
  return null
})

const rawJson = computed(() => {
  if (!props.event) return ""
  try {
    return JSON.stringify(props.event, null, 2)
  } catch {
    return String(props.event)
  }
})

function formatTime(ts) {
  if (!ts) return "—"
  try {
    const d = new Date(Number(ts) * 1000)
    return d.toLocaleString()
  } catch {
    return String(ts)
  }
}

function formatDuration(ms) {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(2)}s`
}
</script>
