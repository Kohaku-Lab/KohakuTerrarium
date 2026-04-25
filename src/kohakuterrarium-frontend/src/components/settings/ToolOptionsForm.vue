<template>
  <div class="tool-options-form">
    <div v-if="!schemaEntries.length" class="text-warm-400 text-xs italic">This tool has no user-tunable options.</div>
    <div v-else class="grid gap-x-4 gap-y-3" :style="gridStyle">
      <div v-for="entry in schemaEntries" :key="entry.key" class="flex flex-col gap-1 min-w-0">
        <label class="flex items-center gap-2 text-[12px] font-medium text-warm-700 dark:text-warm-300">
          <span class="truncate">{{ entry.label }}</span>
          <span v-if="entry.kind !== 'enum'" class="text-[10px] uppercase tracking-wide text-warm-400">{{ entry.kind }}</span>
        </label>

        <!-- enum: strict dropdown -->
        <el-select v-if="entry.kind === 'enum'" :model-value="valueOf(entry.key)" size="small" class="!w-full" @update:model-value="(v) => set(entry.key, v)">
          <el-option v-for="opt in entry.values" :key="opt" :value="opt" :label="opt" />
        </el-select>

        <!-- bool -->
        <el-switch v-else-if="entry.kind === 'bool'" :model-value="!!valueOf(entry.key)" @update:model-value="(v) => set(entry.key, !!v)" />

        <!-- int / float -->
        <el-input-number v-else-if="entry.kind === 'int' || entry.kind === 'float'" :model-value="valueOf(entry.key)" :min="entry.min ?? null" :max="entry.max ?? null" :step="entry.step ?? (entry.kind === 'float' ? 0.1 : 1)" :precision="entry.kind === 'int' ? 0 : 2" size="small" class="!w-full" @update:model-value="(v) => set(entry.key, v)" />

        <!-- string with optional suggestions: free-form input + datalist -->
        <template v-else>
          <el-input :model-value="valueOf(entry.key) ?? ''" size="small" :placeholder="entry.placeholder || ''" :list="entry.suggestions?.length ? `tool-opt-${entry.key}` : undefined" @update:model-value="(v) => set(entry.key, v)" />
          <datalist v-if="entry.suggestions?.length" :id="`tool-opt-${entry.key}`">
            <option v-for="s in entry.suggestions" :key="s" :value="s" />
          </datalist>
        </template>

        <p v-if="entry.description" class="text-[11px] text-warm-400 leading-tight">{{ entry.description }}</p>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue"

/**
 * Schema-driven option editor for any provider-native tool.
 *
 * Layout:
 *   - Fields are laid out in a CSS grid with auto-fit columns so the
 *     form re-flows to 1 / 2 / N columns based on container width.
 *   - ``cols`` caps the maximum number of columns (default 2 — the
 *     tool-options panel is usually mounted in a side dock).
 *   - ``minColWidth`` (px) is the floor below which a column collapses
 *     and the next field wraps to a new row.
 *
 * Field types:
 *   - "enum"   → strict dropdown (no free-form input).
 *   - "string" → free-form input with optional datalist hints.
 *   - "int"/"float" → numeric input with min/max/step honoured.
 *   - "bool"   → toggle.
 *
 * Emits ``update:modelValue`` with the merged values dict whenever a
 * single field changes; clears keys whose value is empty/null so the
 * server never persists a phantom empty string.
 */
const props = defineProps({
  schema: { type: Object, default: () => ({}) },
  modelValue: { type: Object, default: () => ({}) },
  cols: { type: Number, default: 2 },
  minColWidth: { type: Number, default: 200 },
})
const emit = defineEmits(["update:modelValue"])

const schemaEntries = computed(() =>
  Object.entries(props.schema || {}).map(([key, spec]) => ({
    key,
    kind: spec?.type || "string",
    label: spec?.label || key,
    description: spec?.description || "",
    values: spec?.values || [],
    suggestions: spec?.suggestions || [],
    placeholder: spec?.placeholder || "",
    default: spec?.default,
    min: spec?.min,
    max: spec?.max,
    step: spec?.step,
  })),
)

const gridStyle = computed(() => {
  const cols = Math.max(1, props.cols | 0)
  const min = Math.max(80, props.minColWidth | 0)
  // ``auto-fit`` lets columns collapse when the container narrows.
  // The trick ``max(<min>px, calc(100% / cols))`` clamps the minimum
  // column width to at least ``container / cols``, so wide panels
  // never split into more than ``cols`` columns; narrow panels still
  // wrap to fewer columns once a column would shrink past ``min``.
  return {
    "grid-template-columns": `repeat(auto-fit, minmax(max(${min}px, calc(100% / ${cols} - 1rem)), 1fr))`,
  }
})

function valueOf(key) {
  const v = props.modelValue?.[key]
  if (v !== undefined) return v
  return props.schema?.[key]?.default
}

function set(key, value) {
  const next = { ...(props.modelValue || {}) }
  if (value === "" || value === null || value === undefined) {
    delete next[key]
  } else {
    next[key] = value
  }
  emit("update:modelValue", next)
}
</script>
