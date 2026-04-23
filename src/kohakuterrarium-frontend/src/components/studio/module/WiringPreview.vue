<template>
  <div class="border border-warm-200 dark:border-warm-800 rounded-md overflow-hidden">
    <div class="shrink-0 flex items-center gap-2 px-2 py-1.5 border-b border-warm-200 dark:border-warm-800 bg-warm-100/60 dark:bg-warm-900/60 text-[11px] text-warm-600 dark:text-warm-300">
      <div class="i-carbon-document text-sm" />
      <span class="font-medium">{{ t("studio.module.wiring.title") }}</span>
      <span class="font-mono opacity-70">config.yaml</span>
      <div class="flex-1" />
      <button class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded hover:bg-warm-200/60 dark:hover:bg-warm-800/60 transition-colors" :title="t('studio.module.wiring.copy')" @click="copy">
        <div :class="[copied ? 'i-carbon-checkmark text-sage' : 'i-carbon-copy', 'text-xs']" />
        <span>{{ copied ? t("studio.module.wiring.copied") : t("studio.module.wiring.copy") }}</span>
      </button>
    </div>
    <pre class="m-0 px-3 py-2 text-[11px] font-mono text-warm-700 dark:text-warm-300 bg-warm-50 dark:bg-warm-950 overflow-auto whitespace-pre leading-relaxed">{{ yamlText }}</pre>
  </div>
</template>

<script setup>
import { computed, ref } from "vue"

import { useI18n } from "@/utils/i18n"

const { t } = useI18n()

const props = defineProps({
  kind: { type: String, required: true },
  toolName: { type: String, default: "" },
  params: { type: Array, default: () => [] },
})

const copied = ref(false)

// Top-level YAML key depends on kind.
const listKey = computed(() => {
  switch (props.kind) {
    case "tools":
      return "tools"
    case "subagents":
      return "subagents"
    case "triggers":
      return "tools" // universal setup-tool triggers share the tools list
    case "plugins":
      return "plugins"
    case "inputs":
      return "input"
    case "outputs":
      return "output"
    default:
      return props.kind
  }
})

const yamlText = computed(() => {
  const name = props.toolName || "<name>"
  const lines = [`${listKey.value}:`, `  - name: ${name}`]
  for (const p of props.params) {
    if (!p?.name) continue
    const value = p.default
    const rendered = renderScalar(value)
    const line = `    ${p.name}: ${rendered}`
    if (p.required) {
      // required: no default to show, but hint the key name
      lines.push(`    # ${p.name}: ${rendered || "<value>"}  # required`)
    } else {
      lines.push(line)
    }
  }
  return lines.join("\n")
})

function renderScalar(v) {
  if (v == null) return "null"
  if (typeof v === "boolean") return v ? "true" : "false"
  if (typeof v === "number") return String(v)
  if (typeof v === "string") {
    // Quote if contains special chars. Plain strings pass through.
    if (/^[\w./-]+$/.test(v)) return v
    return JSON.stringify(v)
  }
  try {
    return JSON.stringify(v)
  } catch {
    return String(v)
  }
}

async function copy() {
  try {
    await navigator.clipboard.writeText(yamlText.value)
    copied.value = true
    setTimeout(() => {
      copied.value = false
    }, 1200)
  } catch {
    /* clipboard API may be blocked in non-secure contexts */
  }
}
</script>
