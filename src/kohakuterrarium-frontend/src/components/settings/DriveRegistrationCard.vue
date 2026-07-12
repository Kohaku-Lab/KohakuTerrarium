<template>
  <div class="card p-3 flex flex-col gap-2" :class="borderClass">
    <!-- Header: identity + state label (icon + text, never color alone) -->
    <div class="flex items-start gap-2">
      <div class="flex-1 min-w-0">
        <div class="flex items-center gap-2 flex-wrap">
          <span class="font-medium text-sm text-warm-700 dark:text-warm-300 font-mono truncate">{{ reg.name }}</span>
          <el-tag size="small" effect="plain">{{ reg.kind }}</el-tag>
          <span class="inline-flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded" :class="stateChip.class">
            <span :class="stateChip.icon" />
            {{ stateChip.label }}
          </span>
        </div>
        <div v-if="reg.description" class="text-[11px] text-warm-500 dark:text-warm-400 mt-1">
          {{ reg.description }}
        </div>
        <div class="text-[10px] text-warm-400 font-mono mt-1 flex items-center gap-2 flex-wrap">
          <span>{{ sourceLabel }}</span>
          <span
            >· schema v{{ reg.schema_version }}<template v-if="reg.min_schema_version && reg.min_schema_version !== reg.schema_version">–{{ reg.min_schema_version }} min</template></span
          >
          <span v-if="reg.verifier_mode && reg.verifier_mode !== 'none'">· verifier: {{ reg.verifier_mode }}</span>
          <span v-if="reg.has_prompt" class="text-iolite dark:text-iolite-light">· contributes prompt</span>
        </div>
      </div>
      <el-switch :model-value="reg.enabled" :disabled="disabled || reg.conflict" @change="$emit('toggle', reg.name, $event)" />
    </div>

    <!-- Conflict / load-error surfaced prominently, never as success -->
    <div v-if="reg.conflict" class="text-[11px] text-coral bg-coral/10 rounded px-2 py-1 flex items-start gap-1">
      <span class="i-carbon-warning-alt mt-0.5 shrink-0" />
      <span>{{ reg.conflict_reason || "Kind is claimed by more than one registration; resolve before enabling." }}</span>
    </div>
    <div v-else-if="reg.enabled && reg.loaded === false" class="text-[11px] text-coral bg-coral/10 rounded px-2 py-1 flex items-start gap-1">
      <span class="i-carbon-error mt-0.5 shrink-0" />
      <span>Failed to load on this node: {{ reg.error || "unknown import error" }}</span>
    </div>

    <!-- Prompt-preview summary (bounded), collapsed by default -->
    <div v-if="reg.has_prompt && promptPreview" class="text-[11px]">
      <button type="button" class="text-warm-500 hover:text-iolite inline-flex items-center gap-1" @click="showPrompt = !showPrompt">
        <span :class="showPrompt ? 'i-carbon-chevron-down' : 'i-carbon-chevron-right'" />
        Prompt contribution
      </button>
      <pre v-if="showPrompt" class="mt-1 whitespace-pre-wrap bg-warm-100 dark:bg-warm-800 rounded p-2 text-[10px] text-warm-600 dark:text-warm-400 max-h-40 overflow-auto">{{ promptPreview }}</pre>
    </div>

    <!-- Schema-driven options (only when enabled) -->
    <div v-if="reg.enabled" class="border-t border-warm-100 dark:border-warm-800 pt-2 mt-1">
      <div class="text-[10px] uppercase tracking-wide text-warm-400 mb-1.5">Options</div>
      <template v-if="schemaFields.length">
        <div v-for="field in schemaFields" :key="field.key" class="flex items-center gap-2 mb-1.5">
          <label class="text-[11px] text-warm-500 w-40 shrink-0 truncate" :title="field.description || field.key">{{ field.key }}</label>
          <el-switch v-if="field.type === 'boolean'" :model-value="optionValue(field)" @change="$emit('update-option', reg.name, field.key, $event)" />
          <el-select v-else-if="field.type === 'enum'" :model-value="optionValue(field)" size="small" class="flex-1" @change="$emit('update-option', reg.name, field.key, $event)">
            <el-option v-for="opt in field.enum" :key="opt" :value="opt" :label="String(opt)" />
          </el-select>
          <el-input-number v-else-if="field.type === 'number' || field.type === 'integer'" :model-value="optionValue(field)" size="small" controls-position="right" @change="$emit('update-option', reg.name, field.key, $event)" />
          <el-input v-else :model-value="optionValue(field)" size="small" class="flex-1" @update:model-value="$emit('update-option', reg.name, field.key, $event)" />
        </div>
      </template>
      <template v-else>
        <!-- No declared schema: raw JSON options so nothing is silently dropped. -->
        <el-input :model-value="rawJson" type="textarea" :rows="3" size="small" :class="{ 'options-invalid': jsonError }" placeholder="{}" @update:model-value="onRawJson" />
        <div v-if="jsonError" class="text-[10px] text-coral mt-1">{{ jsonError }}</div>
        <div v-else class="text-[10px] text-warm-400 mt-1">Registration exposes no option schema — edit options as JSON.</div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from "vue"

const props = defineProps({
  /** Merged registration view: descriptor fields + draft enabled/options. */
  reg: { type: Object, required: true },
  disabled: { type: Boolean, default: false },
})

const emit = defineEmits(["toggle", "update-option", "set-options"])

const showPrompt = ref(false)

// The catalog DTO exposes `has_prompt`; the full text arrives only when the
// backend enriches the endpoint with `prompt_preview`. Render whichever is
// present.
const promptPreview = computed(() => props.reg.prompt_preview || props.reg.prompt || "")

const sourceLabel = computed(() => {
  if (props.reg.source === "builtin" || !props.reg.package) return "builtin"
  const pkg = props.reg.package || props.reg.source_package
  return props.reg.package_version ? `${pkg} v${props.reg.package_version}` : pkg
})

const borderClass = computed(() => {
  if (props.reg.conflict) return "border-l-3 border-l-coral"
  if (props.reg.enabled && props.reg.loaded === false) return "border-l-3 border-l-coral"
  if (props.reg.enabled) return "border-l-3 border-l-aquamarine"
  return ""
})

const stateChip = computed(() => {
  switch (props.reg.displayState) {
    case "enabled":
      return { label: "Enabled", icon: "i-carbon-checkmark-filled", class: "bg-aquamarine/15 text-aquamarine" }
    case "conflict":
      return { label: "Conflict", icon: "i-carbon-warning-alt", class: "bg-coral/15 text-coral" }
    case "load-error":
      return { label: "Load error", icon: "i-carbon-error", class: "bg-coral/15 text-coral" }
    default:
      return { label: "Available", icon: "i-carbon-circle-dash", class: "bg-warm-200/60 dark:bg-warm-700/60 text-warm-500 dark:text-warm-400" }
  }
})

// Normalise an option schema (flat `{key: {type, enum, description}}`) into a
// field list. Guards every access so a malformed schema can't crash the card.
const schemaFields = computed(() => {
  const schema = props.reg.option_schema
  if (!schema || typeof schema !== "object") return []
  const props_ = schema.properties && typeof schema.properties === "object" ? schema.properties : schema
  return Object.keys(props_)
    .map((key) => {
      const spec = props_[key] || {}
      const enumVals = Array.isArray(spec.enum) ? spec.enum : null
      return {
        key,
        type: enumVals ? "enum" : spec.type || "string",
        enum: enumVals,
        description: spec.description || "",
        default: spec.default,
      }
    })
    .filter((f) => typeof f.key === "string")
})

function optionValue(field) {
  const opts = props.reg.options || {}
  const defaults = props.reg.option_defaults || {}
  const fallback = field.default ?? defaults[field.key]
  return opts[field.key] ?? fallback ?? (field.type === "boolean" ? false : "")
}

// ── Raw-JSON options editor (fallback when no schema) ──
const rawJson = ref(stringifyOptions(props.reg.options))
const jsonError = ref("")

function stringifyOptions(opts) {
  try {
    return JSON.stringify(opts || {}, null, 2)
  } catch {
    return "{}"
  }
}

watch(
  () => props.reg.options,
  (opts) => {
    // Sync only on a genuine EXTERNAL change (reset / adopt-server). The
    // registrationView getter rebuilds ``reg`` (and ``reg.options``) on every
    // recompute, so without the value-equality guard this would re-stringify
    // and clobber the textarea on the user's own keystrokes. Skip when the
    // incoming options already equal what the textarea holds, and never
    // overwrite an in-progress invalid edit.
    if (jsonError.value) return
    if (sameJson(opts, rawJson.value)) return
    rawJson.value = stringifyOptions(opts)
  },
)

function sameJson(obj, text) {
  try {
    return JSON.stringify(obj ?? {}) === JSON.stringify(JSON.parse(text || "{}"))
  } catch {
    return false
  }
}

function onRawJson(text) {
  rawJson.value = text
  try {
    const parsed = text.trim() === "" ? {} : JSON.parse(text)
    if (typeof parsed !== "object" || Array.isArray(parsed)) {
      jsonError.value = "Options must be a JSON object."
      return
    }
    jsonError.value = ""
    emit("set-options", props.reg.name, parsed)
  } catch {
    jsonError.value = "Invalid JSON."
  }
}
</script>

<style scoped>
.options-invalid :deep(textarea) {
  border-color: var(--el-color-danger, #f56c6c);
}
</style>
