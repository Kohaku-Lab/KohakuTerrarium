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

const yamlText = computed(() => {
  const name = props.toolName || "<name>"
  switch (props.kind) {
    case "tools":
      return renderTool(name, props.params)
    case "triggers":
      // Universal setup-tool triggers share the `tools:` list with
      // ``type: trigger`` — this is what a creature writes.
      return renderTrigger(name)
    case "subagents":
      return renderSubagent(name)
    case "plugins":
      return renderPlugin(name)
    case "inputs":
      return renderIo("input", name)
    case "outputs":
      return renderIo("output", name)
    default:
      return `${props.kind}:\n  - name: ${name}`
  }
})

function renderTool(name, params) {
  const lines = ["tools:", `  - name: ${name}`]
  for (const p of params || []) {
    if (!p?.name) continue
    const value = renderScalar(p.default)
    if (p.required) {
      lines.push(`    # ${p.name}: ${value || "<value>"}  # required`)
    } else {
      lines.push(`    ${p.name}: ${value}`)
    }
  }
  return lines.join("\n")
}

function renderTrigger(name) {
  return ["tools:", `  - name: ${name}`, "    type: trigger", "    # args passed to the setup tool at runtime"].join("\n")
}

function renderSubagent(name) {
  return ["subagents:", `  - name: ${name}`].join("\n")
}

function renderPlugin(name) {
  return ["plugins:", `  - name: ${name}`, "    type: custom", `    module: modules.plugins.${name}`, "    options: {}"].join("\n")
}

function renderIo(kind, name) {
  return [`${kind}:`, `  name: ${name}`, "  type: custom", `  module: modules.${kind}s.${name}`].join("\n")
}

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
