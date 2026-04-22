<template>
  <KField :label="param.name" :hint="hint">
    <!-- bool → checkbox -->
    <KCheckbox v-if="kind === 'bool'" :model-value="boolValue" :label="boolLabel" @update:model-value="$emit('change', $event)" />
    <!-- int / float → number input -->
    <KInput v-else-if="kind === 'int' || kind === 'float'" :model-value="stringValue" type="number" :placeholder="placeholder" @update:model-value="onNumber($event)" />
    <!-- list of primitives → one-per-line textarea -->
    <textarea v-else-if="kind === 'list'" :value="listText" rows="3" class="w-full px-2.5 py-1.5 rounded-md text-xs font-mono bg-warm-50 dark:bg-warm-950 border border-warm-200 dark:border-warm-700 focus:outline-none focus:border-iolite resize-y" :placeholder="placeholder || 'one per line'" @change="onListText" />
    <!-- dict / Any / complex → JSON textarea -->
    <textarea v-else-if="kind === 'json'" :value="jsonText" rows="3" class="w-full px-2.5 py-1.5 rounded-md text-xs font-mono bg-warm-50 dark:bg-warm-950 border border-warm-200 dark:border-warm-700 focus:outline-none focus:border-iolite resize-y" placeholder="JSON value" @change="onJsonText" />
    <!-- default: string -->
    <KInput v-else :model-value="stringValue" :placeholder="placeholder" @update:model-value="onString($event)" />
  </KField>
  <div v-if="jsonError" class="text-[11px] text-coral mt-0.5 -mt-2">
    {{ jsonError }}
  </div>
</template>

<script setup>
import { computed, ref } from "vue"

import KCheckbox from "@/components/studio/common/KCheckbox.vue"
import KField from "@/components/studio/common/KField.vue"
import KInput from "@/components/studio/common/KInput.vue"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()

const props = defineProps({
  /** { name, type_hint, default, required, description } */
  param: { type: Object, required: true },
  /** Current value from the config entry (may be undefined = use default). */
  modelValue: { default: undefined },
})

const emit = defineEmits(["change"])

const jsonError = ref("")

/**
 * Classify the type hint into a renderable bucket:
 *   bool | int | float | list | json | string
 *
 * We're intentionally forgiving — anything with `bool` in the hint
 * is treated as bool (covers ``bool | None``, ``Optional[bool]``).
 * Same for ``list[...]`` → list editor.
 */
const kind = computed(() => {
  const h = (props.param.type_hint || "").toLowerCase()
  if (!h) {
    // No hint — infer from default.
    const d = props.param.default
    if (typeof d === "boolean") return "bool"
    if (typeof d === "number") return Number.isInteger(d) ? "int" : "float"
    if (Array.isArray(d)) return "list"
    if (d !== null && typeof d === "object") return "json"
    return "string"
  }
  if (h.includes("bool")) return "bool"
  if (/(^|[^a-z])int([^a-z]|$)/.test(h)) return "int"
  if (/(^|[^a-z])float([^a-z]|$)/.test(h)) return "float"
  if (h.startsWith("list") || h.includes("list[")) return "list"
  if (h.startsWith("dict") || h.includes("dict[") || h === "any") return "json"
  if (h.startsWith("str") || h.includes("str")) return "string"
  return "string"
})

const hint = computed(() => {
  const parts = []
  if (props.param.description) parts.push(props.param.description)
  if (props.param.type_hint) parts.push(`type: ${props.param.type_hint}`)
  if (props.param.default !== null && props.param.default !== undefined) {
    parts.push(`default: ${JSON.stringify(props.param.default)}`)
  } else if (!props.param.required) {
    parts.push(t("studio.schema.optional"))
  }
  return parts.join(" · ")
})

const placeholder = computed(() => {
  const d = props.param.default
  if (d === null || d === undefined) return ""
  if (typeof d === "object") return JSON.stringify(d)
  return String(d)
})

const stringValue = computed(() => {
  const v = props.modelValue
  return v == null ? "" : String(v)
})

const boolValue = computed(() => {
  const v = props.modelValue
  if (v == null) return !!props.param.default
  return !!v
})

const boolLabel = computed(() => props.param.description || props.param.name)

const listText = computed(() => {
  const v = props.modelValue
  if (!Array.isArray(v)) return ""
  return v.join("\n")
})

const jsonText = computed(() => {
  const v = props.modelValue
  if (v == null) return ""
  try {
    return JSON.stringify(v, null, 2)
  } catch {
    return String(v)
  }
})

function onString(v) {
  emit("change", v === "" ? undefined : v)
}

function onNumber(v) {
  if (v === "" || v == null) {
    emit("change", undefined)
    return
  }
  const n = Number(v)
  if (!Number.isFinite(n)) return
  emit("change", kind.value === "int" ? Math.trunc(n) : n)
}

function onListText(e) {
  const text = e.target.value
  const lines = text
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean)
  emit("change", lines.length ? lines : undefined)
}

function onJsonText(e) {
  const text = e.target.value.trim()
  if (!text) {
    jsonError.value = ""
    emit("change", undefined)
    return
  }
  try {
    const parsed = JSON.parse(text)
    jsonError.value = ""
    emit("change", parsed)
  } catch (err) {
    jsonError.value = err.message
  }
}
</script>
