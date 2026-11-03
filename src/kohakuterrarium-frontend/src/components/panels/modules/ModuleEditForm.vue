<template>
  <div class="flex flex-col gap-4">
    <p v-if="!entries.length" class="col-span-full text-[11px] text-warm-400 italic">{{ layout.empty || "This module has no runtime-mutable options." }}</p>

    <component :is="group.advanced ? 'details' : 'div'" v-for="group in groups" :key="String(group.advanced)" :class="group.advanced ? 'border-t kt-v2-line pt-3' : ''">
      <summary v-if="group.advanced" class="cursor-pointer text-xs font-medium text-warm-500 py-1">{{ layout.advancedLabel }}</summary>
      <div class="grid grid-cols-1 sm:grid-cols-2" :class="[layout.spacious ? 'gap-x-5 gap-y-5' : 'gap-x-3 gap-y-2.5', group.advanced ? 'pt-4' : '']">
        <div v-for="entry in group.entries" :key="entry.key" class="flex flex-col min-w-0" :class="[layout.spacious ? 'gap-2' : 'gap-1', isWide(entry) || entry.ui.wide ? 'col-span-full' : 'col-span-1']" :data-field="entry.key">
          <label class="font-medium flex items-center gap-1 min-w-0" :class="layout.spacious ? 'text-xs text-warm-600 dark:text-warm-300' : 'text-[11px] text-warm-500'">
            <span :class="entry.ui.label ? '' : 'font-mono'" class="truncate">{{ entry.ui.label || entry.key }}</span>
            <span v-if="!entry.ui.label" class="text-warm-400 font-normal text-[10px] shrink-0">({{ entry.spec?.type || "string" }})</span>
            <el-tooltip v-if="entry.ui.doc || entry.spec?.doc || entry.spec?.description" :content="entry.ui.doc || entry.spec.doc || entry.spec.description" placement="top" :show-after="300" popper-class="kt-hint-tooltip">
              <span class="i-carbon-information text-warm-400 hover:text-warm-600 dark:hover:text-warm-300 cursor-help text-[12px] shrink-0" />
            </el-tooltip>
          </label>

          <!-- enum -->
          <el-select :disabled="disabled" v-if="entry.spec?.type === 'enum'" v-model="draft[entry.key]" :size="layout.spacious ? 'default' : 'small'" :placeholder="entry.ui.placeholder || String(entry.spec?.default ?? '')">
            <el-option v-for="v in entry.spec?.values || []" :key="v" :value="v" :label="enumLabel(entry, v)" :disabled="Boolean(entry.spec?.disabled_values?.[v])" />
          </el-select>

          <!-- bool -->
          <el-switch :disabled="disabled" v-else-if="entry.spec?.type === 'bool'" v-model="draft[entry.key]" />

          <!-- int / float -->
          <el-input-number :disabled="disabled" v-else-if="entry.spec?.type === 'int' || entry.spec?.type === 'float'" v-model="draft[entry.key]" :min="entry.spec?.min" :max="entry.spec?.max" :step="entry.spec?.type === 'float' ? 0.1 : 1" :precision="entry.spec?.type === 'float' ? undefined : 0" size="small" controls-position="right" class="!w-full" />

          <!-- list of strings (newline-separated textarea) -->
          <el-input :disabled="disabled" v-else-if="entry.spec?.type === 'list' && (entry.spec?.item_type === 'string' || !entry.spec?.item_type)" :model-value="listToText(draft[entry.key])" type="textarea" :autosize="{ minRows: 3, maxRows: 12 }" size="small" :placeholder="layout.listPlaceholder || 'One value per line'" @update:model-value="draft[entry.key] = textToList($event)" />

          <!-- dict (JSON textarea) -->
          <el-input :disabled="disabled" v-else-if="entry.spec?.type === 'dict'" :model-value="dictText[entry.key]" type="textarea" :autosize="{ minRows: 3, maxRows: 12 }" size="small" placeholder='{"soft": 30, "hard": 50}' @update:model-value="onDictInput(entry.key, $event)" />

          <!-- string (default) -->
          <el-input :disabled="disabled" v-else v-model="draft[entry.key]" :size="layout.spacious ? 'default' : 'small'" :placeholder="entry.ui.placeholder || String(entry.spec?.default ?? '')" />

          <p v-if="dictErrors[entry.key]" class="text-[10px] text-coral font-mono">{{ layout.invalidJson || "Invalid JSON" }}: {{ dictErrors[entry.key] }}</p>
          <p v-if="entry.ui.hint || entry.spec?.hint" :class="layout.spacious ? 'text-xs text-warm-500 leading-relaxed' : 'text-[10px] text-warm-400'">{{ entry.ui.hint || entry.spec.hint }}</p>
          <details v-if="entry.ui.availability && Object.keys(entry.ui.disabledValues || entry.spec?.disabled_values || {}).length" class="text-xs text-warm-500 leading-relaxed">
            <summary class="cursor-pointer">{{ layout.availabilityLabel }}</summary>
            <div v-for="notice in unavailableNotices(entry)" :key="notice.reason" class="mt-2 pl-3 border-l kt-v2-line">
              <p class="font-medium">{{ notice.labels.join(" / ") }}</p>
              <p class="mt-1 break-words">{{ notice.reason }}</p>
            </div>
          </details>
          <p v-for="(reason, value) in entry.ui.availability ? {} : entry.ui.disabledValues || entry.spec?.disabled_values || {}" :key="`${entry.key}-${value}`" class="text-[10px] text-warm-400">{{ entry.ui.labels?.[value] || value }} {{ layout.unavailable || "unavailable" }}: {{ reason }}</p>
        </div>
      </div>
    </component>

    <!-- status indicator (auto-sync) ─────────────────────────── -->
    <div v-if="entries.length" class="flex items-center gap-1.5 pt-1" :class="layout.spacious ? 'text-xs text-warm-500 min-h-4' : 'text-[10px] text-warm-400 italic h-4'">
      <span v-if="status === 'saving'" class="flex items-center gap-1">
        <span class="i-carbon-renew animate-spin text-[10px]" />
        {{ layout.saving || "saving…" }}
      </span>
      <span v-else-if="status === 'saved'" class="text-aquamarine flex items-center gap-1">
        <span class="i-carbon-checkmark text-[10px]" />
        {{ layout.saved || "saved" }}
      </span>
      <span v-else-if="status === 'error'" class="text-coral flex items-center gap-1 truncate">
        <span class="i-carbon-warning text-[10px] shrink-0" />
        <span class="truncate">{{ layout.saveError ? `${layout.saveError}: ` : "" }}{{ errorMsg }}</span>
      </span>
      <span v-else>{{ layout.autoSave || "auto-saves on change" }}</span>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref, watch } from "vue"

/**
 * Auto-sync schema-driven options form for a single module.
 *
 * No Apply / Reset / Cancel buttons. Edits commit automatically with
 * a short debounce after the user stops typing. The status line below
 * the form is the only feedback affordance ("saving…" / "saved" /
 * "<error>"). Field-level docs are hidden behind hover tooltips on
 * info-dot icons next to each label, so the form stays compact in the
 * narrow panel slot.
 *
 * Type-agnostic: parent (``ModulesPanel.vue``) supplies a ``submitFn``
 * that routes the save call to ``moduleAPI.setOptions(sessionId,
 * creatureId, type, name, values)``.
 */

const SAVE_DEBOUNCE_MS = 500
const SAVED_FLASH_MS = 1500

const props = defineProps({
  /** Module name. Forwarded back to the parent on save. */
  name: { type: String, required: true },
  /** Schema dict from option_schema(). Map of key → {type, default, doc, ...}. */
  schema: { type: Object, default: () => ({}) },
  /** Current option values (post-merge with defaults). */
  values: { type: Object, default: () => ({}) },
  /** Async callback: (name, changedValues) → server response. Throws on error. */
  submitFn: { type: Function, required: true },
  presentation: { type: Function, default: null },
  disabled: { type: Boolean, default: false },
})

const emit = defineEmits(["applied"])

const draft = ref({})
const initial = ref({})
const dictErrors = ref({})
const dictText = ref({})
const status = ref("idle") // 'idle' | 'saving' | 'saved' | 'error'
const errorMsg = ref("")

let savePromise = null
let echoedOptions = null
let saveTimer = null
let savedFlashTimer = null
// Strict monotonic generation counter so a slower in-flight save can
// never overwrite the result of a more-recent save.
let saveGen = 0

const layout = computed(() => props.presentation?.(draft.value) || {})
const entries = computed(() =>
  Object.entries(props.schema || {})
    .map(([key, spec]) => ({ key, spec, ui: layout.value.fields?.[key] || {} }))
    .filter((entry) => entry.ui.visible !== false),
)
const groups = computed(() => [false, true].map((advanced) => ({ advanced, entries: entries.value.filter((entry) => Boolean(entry.ui.advanced) === advanced) })).filter((group) => group.entries.length))

const hasDictError = computed(() => Object.values(dictErrors.value).some(Boolean))

/**
 * Whether a field needs the full row width.
 *
 * Multiline inputs (list-of-strings textarea, dict JSON textarea)
 * always span both columns — anything narrower is unusable. Compact
 * inputs (enum / bool / int / float / string) live in a single column
 * so two of them can sit side-by-side and the form fits more in less
 * vertical space.
 */
function isWide(entry) {
  const t = entry?.spec?.type
  return t === "list" || t === "dict"
}

function enumLabel(entry, value) {
  const label = entry.ui.labels?.[value] || entry.spec?.labels?.[value] || value
  return entry.spec?.disabled_values?.[value] ? `${label} — ${layout.value.unavailable || "unavailable"}` : label
}

function unavailableNotices(entry) {
  const notices = new Map()
  for (const [value, reason] of Object.entries(entry.ui.disabledValues || entry.spec?.disabled_values || {})) {
    if (!notices.has(reason)) notices.set(reason, { reason, labels: [] })
    notices.get(reason).labels.push(entry.ui.labels?.[value] || entry.spec?.labels?.[value] || value)
  }
  return [...notices.values()]
}

function deepClone(v) {
  return JSON.parse(JSON.stringify(v ?? null))
}

function listToText(v) {
  if (!Array.isArray(v)) return ""
  return v.join("\n")
}

function textToList(text) {
  if (!text) return []
  return text
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean)
}

function objToJson(v) {
  if (v === null || v === undefined) return ""
  try {
    return JSON.stringify(v, null, 2)
  } catch {
    return String(v)
  }
}

function onDictInput(key, raw) {
  dictText.value[key] = raw ?? ""
  if (!raw || !raw.trim()) {
    draft.value[key] = null
    dictErrors.value = { ...dictErrors.value, [key]: "" }
    return
  }
  try {
    const parsed = JSON.parse(raw)
    draft.value[key] = parsed
    dictErrors.value = { ...dictErrors.value, [key]: "" }
  } catch (e) {
    dictErrors.value = { ...dictErrors.value, [key]: e.message }
  }
}

function syncFromProps() {
  if (echoedOptions && JSON.stringify(props.values) === JSON.stringify(echoedOptions)) {
    echoedOptions = null
    return
  }
  // Rebuild draft + initial from incoming values + schema defaults.
  // Cancel any pending save — values may have changed underneath us
  // (toggle, external edit, reload).
  if (saveTimer) {
    clearTimeout(saveTimer)
    saveTimer = null
  }
  saveGen += 1
  const next = {}
  for (const [key, spec] of Object.entries(props.schema || {})) {
    if (key in (props.values || {})) {
      next[key] = props.values[key]
    } else {
      next[key] = spec?.default ?? null
    }
  }
  draft.value = deepClone(next)
  initial.value = deepClone(next)
  dictText.value = Object.fromEntries(
    Object.entries(props.schema || {})
      .filter(([, spec]) => spec?.type === "dict")
      .map(([key]) => [key, objToJson(next[key])]),
  )
  dictErrors.value = {}
  status.value = "idle"
  errorMsg.value = ""
}

watch(() => [props.name, props.schema, props.values], syncFromProps, { immediate: true, deep: true })

// Debounced auto-save ────────────────────────────────────────────

function changedKeys() {
  const out = {}
  for (const key of Object.keys(draft.value)) {
    if (JSON.stringify(draft.value[key]) !== JSON.stringify(initial.value[key])) {
      out[key] = draft.value[key]
    }
  }
  return out
}

async function flushSave() {
  while (savePromise) await savePromise
  const pending = saveChanges()
  savePromise = pending
  try {
    return await pending
  } finally {
    if (savePromise === pending) savePromise = null
  }
}

async function saveChanges() {
  if (hasDictError.value) {
    // Don't try to save while a JSON dict field has a parse error;
    // the form will retry once the user fixes the input.
    return false
  }
  const payload = changedKeys()
  if (!Object.keys(payload).length) return true

  const myGen = ++saveGen
  status.value = "saving"
  errorMsg.value = ""
  try {
    const result = await props.submitFn(props.name, payload)
    // Drop the result if a newer save (or sync from props) has fired
    // since this call started.
    if (myGen !== saveGen) return false
    const applied = { ...initial.value, ...payload, ...(result?.options || {}) }
    initial.value = deepClone(applied)
    // Don't blow away the draft — it might already have newer edits
    // on top of `applied`. Update only the keys we just persisted.
    for (const key of Object.keys(payload)) {
      if (JSON.stringify(draft.value[key]) === JSON.stringify(payload[key])) {
        draft.value[key] = applied[key] !== undefined ? applied[key] : draft.value[key]
      }
    }
    status.value = "saved"
    echoedOptions = deepClone(initial.value)
    emit("applied", { name: props.name, options: initial.value })
    if (savedFlashTimer) clearTimeout(savedFlashTimer)
    savedFlashTimer = setTimeout(() => {
      if (status.value === "saved") status.value = "idle"
    }, SAVED_FLASH_MS)
    return true
  } catch (e) {
    if (myGen !== saveGen) return false
    status.value = "error"
    errorMsg.value = e?.response?.data?.detail || e?.message || String(e)
    return false
  }
}

function scheduleSave() {
  if (saveTimer) clearTimeout(saveTimer)
  saveTimer = setTimeout(() => {
    saveTimer = null
    flushSave()
  }, SAVE_DEBOUNCE_MS)
}

watch(
  draft,
  () => {
    if (!savePromise && JSON.stringify(draft.value) === JSON.stringify(initial.value)) return
    scheduleSave()
  },
  { deep: true },
)

onBeforeUnmount(() => {
  if (saveTimer) {
    clearTimeout(saveTimer)
    saveTimer = null
    // Flush pending edits so the user doesn't lose work when leaving
    // edit mode mid-debounce. Fire-and-forget — there's no UI left to
    // surface errors.
    flushSave()
  }
  if (savedFlashTimer) {
    clearTimeout(savedFlashTimer)
    savedFlashTimer = null
  }
})
defineExpose({ flushSave })
</script>
