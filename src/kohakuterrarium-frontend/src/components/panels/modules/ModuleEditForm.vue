<template>
  <div class="grid grid-cols-2 gap-x-3 gap-y-2.5">
    <p v-if="!entries.length" class="col-span-2 text-[11px] text-warm-400 italic">This module has no runtime-mutable options.</p>

    <div v-for="entry in entries" :key="entry.key" class="flex flex-col gap-1 min-w-0" :class="isWide(entry) ? 'col-span-2' : 'col-span-1'">
      <label class="text-[11px] text-warm-500 font-medium flex items-center gap-1 min-w-0">
        <span class="font-mono truncate">{{ entry.key }}</span>
        <span class="text-warm-400 font-normal text-[10px] shrink-0">({{ entry.spec?.type || "string" }})</span>
        <el-tooltip v-if="entry.spec?.doc" :content="entry.spec.doc" placement="top" :show-after="300">
          <span class="i-carbon-information text-warm-400 hover:text-warm-600 dark:hover:text-warm-300 cursor-help text-[12px] shrink-0" />
        </el-tooltip>
      </label>

      <!-- enum -->
      <el-select v-if="entry.spec?.type === 'enum'" v-model="draft[entry.key]" size="small" :placeholder="String(entry.spec?.default ?? '')">
        <el-option v-for="v in entry.spec?.values || []" :key="v" :value="v" :label="v" />
      </el-select>

      <!-- bool -->
      <el-switch v-else-if="entry.spec?.type === 'bool'" v-model="draft[entry.key]" />

      <!-- int / float -->
      <el-input-number v-else-if="entry.spec?.type === 'int' || entry.spec?.type === 'float'" v-model="draft[entry.key]" :min="entry.spec?.min" :max="entry.spec?.max" :step="entry.spec?.type === 'float' ? 0.1 : 1" :precision="entry.spec?.type === 'float' ? undefined : 0" size="small" controls-position="right" class="!w-full" />

      <!-- list of strings (newline-separated textarea) -->
      <el-input v-else-if="entry.spec?.type === 'list' && (entry.spec?.item_type === 'string' || !entry.spec?.item_type)" :model-value="listToText(draft[entry.key])" type="textarea" :rows="3" size="small" placeholder="One value per line" @update:model-value="draft[entry.key] = textToList($event)" />

      <!-- dict (JSON textarea) -->
      <el-input v-else-if="entry.spec?.type === 'dict'" :model-value="objToJson(draft[entry.key])" type="textarea" :rows="3" size="small" placeholder='{"soft": 30, "hard": 50}' @update:model-value="onDictInput(entry.key, $event)" />

      <!-- string (default) -->
      <el-input v-else v-model="draft[entry.key]" size="small" :placeholder="String(entry.spec?.default ?? '')" />

      <p v-if="dictErrors[entry.key]" class="text-[10px] text-coral font-mono">{{ dictErrors[entry.key] }}</p>
    </div>

    <!-- status indicator (auto-sync) ─────────────────────────── -->
    <div v-if="entries.length" class="col-span-2 text-[10px] text-warm-400 italic h-4 flex items-center gap-1.5 pt-1">
      <span v-if="status === 'saving'" class="flex items-center gap-1">
        <span class="i-carbon-renew animate-spin text-[10px]" />
        saving…
      </span>
      <span v-else-if="status === 'saved'" class="text-aquamarine flex items-center gap-1">
        <span class="i-carbon-checkmark text-[10px]" />
        saved
      </span>
      <span v-else-if="status === 'error'" class="text-coral flex items-center gap-1 truncate">
        <span class="i-carbon-warning text-[10px] shrink-0" />
        <span class="truncate">{{ errorMsg }}</span>
      </span>
      <span v-else>auto-saves on change</span>
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
})

const emit = defineEmits(["applied"])

const draft = ref({})
const initial = ref({})
const dictErrors = ref({})
const status = ref("idle") // 'idle' | 'saving' | 'saved' | 'error'
const errorMsg = ref("")

let saveTimer = null
let savedFlashTimer = null
// Strict monotonic generation counter so a slower in-flight save can
// never overwrite the result of a more-recent save.
let saveGen = 0

const entries = computed(() => Object.entries(props.schema || {}).map(([key, spec]) => ({ key, spec })))

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
    dictErrors.value = { ...dictErrors.value, [key]: `Invalid JSON: ${e.message}` }
  }
}

function syncFromProps() {
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
  if (hasDictError.value) {
    // Don't try to save while a JSON dict field has a parse error;
    // the form will retry once the user fixes the input.
    return
  }
  const payload = changedKeys()
  if (!Object.keys(payload).length) return

  const myGen = ++saveGen
  status.value = "saving"
  errorMsg.value = ""
  try {
    const result = await props.submitFn(props.name, payload)
    // Drop the result if a newer save (or sync from props) has fired
    // since this call started.
    if (myGen !== saveGen) return
    const applied = result?.options || draft.value
    initial.value = deepClone(applied)
    // Don't blow away the draft — it might already have newer edits
    // on top of `applied`. Update only the keys we just persisted.
    for (const key of Object.keys(payload)) {
      if (JSON.stringify(draft.value[key]) === JSON.stringify(payload[key])) {
        draft.value[key] = applied[key] !== undefined ? applied[key] : draft.value[key]
      }
    }
    status.value = "saved"
    emit("applied", { name: props.name, options: initial.value })
    if (savedFlashTimer) clearTimeout(savedFlashTimer)
    savedFlashTimer = setTimeout(() => {
      if (status.value === "saved") status.value = "idle"
    }, SAVED_FLASH_MS)
  } catch (e) {
    if (myGen !== saveGen) return
    status.value = "error"
    errorMsg.value = e?.response?.data?.detail || e?.message || String(e)
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
    if (JSON.stringify(draft.value) === JSON.stringify(initial.value)) return
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
</script>
