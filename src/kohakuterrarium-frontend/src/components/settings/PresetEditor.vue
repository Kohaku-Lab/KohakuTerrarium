<template>
  <div class="flex flex-col gap-4">
    <!-- Header / actions -->
    <div class="flex items-center gap-2">
      <div class="flex-1 min-w-0">
        <div class="flex items-center gap-2 flex-wrap">
          <h3 class="font-medium text-warm-800 dark:text-warm-200 text-sm">
            {{ headerTitle }}
          </h3>
          <el-tag v-if="isBuiltin" size="small" type="info" effect="plain">built-in</el-tag>
          <el-tag v-else-if="isEditing" size="small" effect="plain">user</el-tag>
          <el-tag v-else size="small" type="success" effect="plain">new</el-tag>
        </div>
        <p v-if="isBuiltin" class="text-[11px] text-warm-400 mt-0.5">Built-in presets are read-only. Click “Clone” to make a customizable copy.</p>
      </div>
      <el-button v-if="isBuiltin" size="small" type="primary" @click="$emit('clone')">Clone</el-button>
      <el-button v-if="isEditing && !isBuiltin" size="small" @click="$emit('cancel')">Close</el-button>
    </div>

    <!-- Core section -->
    <section class="card p-4">
      <div class="section-title">Core</div>
      <div class="grid grid-cols-2 gap-3">
        <div>
          <label class="field-label">Preset name</label>
          <el-input v-model="form.name" size="small" :disabled="isBuiltin || isEditing" :placeholder="isEditing ? '' : 'my-model'" />
        </div>
        <div>
          <label class="field-label">Model ID</label>
          <el-input v-model="form.model" size="small" :disabled="isBuiltin" placeholder="e.g. gpt-4o, claude-opus-4-6" />
        </div>
        <div>
          <label class="field-label">Provider</label>
          <el-select v-model="form.provider" size="small" class="w-full" :disabled="isBuiltin" placeholder="Select a provider">
            <el-option v-for="backend in backends" :key="backend.name" :value="backend.name" :label="backendLabel(backend)" />
          </el-select>
        </div>
        <div>
          <label class="field-label">Max context</label>
          <el-input-number v-model="form.max_context" size="small" :min="1024" :step="1024" :disabled="isBuiltin" class="!w-full" />
        </div>
        <div>
          <label class="field-label">Max output</label>
          <el-input-number v-model="form.max_output" size="small" :min="1" :step="1024" :disabled="isBuiltin" class="!w-full" />
        </div>
      </div>
    </section>

    <!-- Defaults section -->
    <section class="card p-4">
      <div class="section-title">Defaults <span class="text-[10px] text-warm-400 font-normal">applied unless a variation overrides</span></div>
      <div class="grid grid-cols-2 gap-3">
        <div>
          <label class="field-label">Temperature</label>
          <el-input-number v-model="form.temperature" size="small" :min="0" :max="2" :step="0.1" :precision="2" :disabled="isBuiltin" class="!w-full" placeholder="leave empty for API default" />
        </div>
        <div>
          <label class="field-label">Reasoning effort (top-level)</label>
          <el-input v-model="form.reasoning_effort" size="small" :disabled="isBuiltin" placeholder="codex shape: low/medium/high" />
        </div>
        <div>
          <label class="field-label">Service tier</label>
          <el-input v-model="form.service_tier" size="small" :disabled="isBuiltin" placeholder="e.g. default, priority, flex" />
        </div>
        <div class="col-span-2">
          <div class="flex items-center justify-between mb-1">
            <label class="field-label !mb-0">extra_body (JSON)</label>
            <el-button size="small" text :disabled="isBuiltin" @click="formatExtraBody"> Format </el-button>
          </div>
          <el-input v-model="form.extra_body" type="textarea" :rows="4" :disabled="isBuiltin" placeholder='{"reasoning":{"enabled":true,"effort":"high"}}' class="font-mono" />
          <div v-if="extraBodyError" class="text-[10px] text-coral mt-1 font-mono">
            {{ extraBodyError }}
          </div>
        </div>
      </div>
    </section>

    <!-- Variations section -->
    <section class="card p-4">
      <div class="flex items-center justify-between mb-3">
        <div class="section-title !mb-0">Variation groups</div>
        <el-button v-if="!isBuiltin" size="small" type="primary" plain @click="addGroup"> Add group </el-button>
      </div>
      <p class="text-[11px] text-warm-400 mb-3">Each group is one knob (e.g. <code class="font-mono">reasoning</code>). Each option inside a group is a selectable value (e.g. <code class="font-mono">low</code>). Users pick one option per group via <code class="font-mono">preset@group=option</code>.</p>
      <div v-if="form.variation_groups.length === 0" class="text-[11px] text-warm-400 italic">No variation groups. The preset has no user-selectable knobs.</div>
      <div v-for="(group, groupIndex) in form.variation_groups" :key="groupIndex" class="variation-group">
        <div class="flex items-center gap-2 mb-2">
          <label class="field-label !mb-0 shrink-0">Group</label>
          <el-input v-model="group.name" size="small" placeholder="e.g. reasoning, speed, thinking" :disabled="isBuiltin" class="!w-48" />
          <el-button v-if="!isBuiltin" size="small" @click="addOption(group)"> Add option </el-button>
          <div class="flex-1" />
          <el-button v-if="!isBuiltin" size="small" type="danger" plain @click="form.variation_groups.splice(groupIndex, 1)"> Remove group </el-button>
        </div>
        <div v-for="(option, optionIndex) in group.options" :key="optionIndex" class="variation-option">
          <div class="flex items-center gap-2 mb-2">
            <label class="field-label !mb-0 shrink-0">Option</label>
            <el-input v-model="option.name" size="small" placeholder="e.g. low, medium, high, fast" :disabled="isBuiltin" class="!w-40" />
            <el-button v-if="!isBuiltin" size="small" @click="addPatch(option)"> Add patch </el-button>
            <div class="flex-1" />
            <el-button v-if="!isBuiltin" size="small" type="danger" plain @click="group.options.splice(optionIndex, 1)"> Remove option </el-button>
          </div>
          <div v-for="(patch, patchIndex) in option.patches" :key="patchIndex" class="variation-patch">
            <el-select v-model="patch.root" size="small" class="!w-36 shrink-0" :disabled="isBuiltin" @change="onRootChange(patch)">
              <el-option v-for="root in PATCH_ROOTS" :key="root" :value="root" :label="root" />
            </el-select>
            <span class="text-warm-400 text-xs shrink-0">.</span>
            <el-input v-if="patch.root === 'extra_body'" v-model="patch.subpath" size="small" placeholder="e.g. reasoning.effort" :disabled="isBuiltin" class="!w-56 font-mono" />
            <span v-else class="text-warm-400 text-xs italic shrink-0">(no sub-path)</span>
            <span class="text-warm-400 text-xs shrink-0">=</span>
            <el-input v-model="patch.value" size="small" placeholder='e.g. "low", 0.5, true, {"foo":"bar"}' :disabled="isBuiltin" class="!flex-1 font-mono" />
            <el-button v-if="!isBuiltin" size="small" type="danger" plain :icon="Delete" @click="option.patches.splice(patchIndex, 1)" />
          </div>
          <div v-if="option.patches.length === 0" class="text-[10px] text-warm-400 italic pl-2">(no-op — selecting this option changes nothing)</div>
        </div>
        <div v-if="group.options.length === 0" class="text-[10px] text-warm-400 italic pl-2">No options — this group does nothing.</div>
      </div>
    </section>

    <!-- Request-body preview -->
    <section v-if="!isBuiltin || form.variation_groups.length" class="card p-4">
      <div class="flex items-center justify-between mb-2">
        <div class="section-title !mb-0">Request preview</div>
        <div class="flex items-center gap-2">
          <span class="text-[11px] text-warm-400">Preview with:</span>
          <div v-if="!hasAnyVariations" class="text-[11px] text-warm-400 italic">base preset</div>
          <template v-else>
            <div v-for="group in form.variation_groups.filter((g) => g.name && g.options.length)" :key="group.name" class="flex items-center gap-1">
              <span class="text-[10px] text-warm-500">{{ group.name }}</span>
              <el-select v-model="previewSelection[group.name]" size="small" class="!w-28">
                <el-option label="(base)" value="" />
                <el-option v-for="option in group.options.filter((o) => o.name)" :key="option.name" :value="option.name" :label="option.name" />
              </el-select>
            </div>
          </template>
        </div>
      </div>
      <pre class="preview-pre">{{ previewJSON }}</pre>
      <div v-if="previewError" class="text-[10px] text-coral mt-1 font-mono">
        {{ previewError }}
      </div>
    </section>

    <!-- Save bar -->
    <div v-if="!isBuiltin" class="flex items-center gap-2 sticky bottom-0 bg-warm-50/90 dark:bg-warm-900/90 backdrop-blur px-2 py-2 -mx-4 border-t border-warm-200 dark:border-warm-800">
      <el-button type="primary" size="small" :disabled="!canSave || !!extraBodyError" :loading="saving" @click="save">
        {{ isEditing ? "Update preset" : "Create preset" }}
      </el-button>
      <el-button v-if="isEditing" size="small" @click="$emit('cancel')">Cancel</el-button>
      <el-button v-if="isEditing" size="small" type="danger" plain @click="$emit('delete', form.name)"> Delete </el-button>
      <div class="flex-1" />
      <div v-if="saveHint" class="text-[11px] text-warm-400">{{ saveHint }}</div>
    </div>
  </div>
</template>

<script setup>
import { computed, reactive, ref, watch } from "vue"
import { Delete } from "@element-plus/icons-vue"

const props = defineProps({
  preset: { type: Object, default: null },
  backends: { type: Array, default: () => [] },
  mode: { type: String, default: "new" }, // "new" | "edit" | "view"
})
const emit = defineEmits(["save", "cancel", "clone", "delete"])

const PATCH_ROOTS = ["extra_body", "temperature", "reasoning_effort", "service_tier", "max_context", "max_output"]

const saving = ref(false)
const previewSelection = reactive({})

const form = reactive({
  name: "",
  model: "",
  provider: "",
  max_context: 128000,
  max_output: 16384,
  temperature: null,
  reasoning_effort: "",
  service_tier: "",
  extra_body: "{}",
  variation_groups: [],
})

const isBuiltin = computed(() => props.mode === "view")
const isEditing = computed(() => props.mode === "edit")

const headerTitle = computed(() => {
  if (!props.preset) return "New preset"
  return form.name || "(unnamed)"
})

const backendLabel = (backend) => {
  const parts = [backend.name]
  if (backend.backend_type) parts.push(`[${backend.backend_type}]`)
  if (!backend.available) parts.push("· no key")
  return parts.join(" ")
}

const extraBodyError = computed(() => {
  if (!form.extra_body || !form.extra_body.trim()) return ""
  try {
    const parsed = JSON.parse(form.extra_body)
    if (parsed !== null && typeof parsed !== "object") {
      return "extra_body must be a JSON object"
    }
    if (Array.isArray(parsed)) return "extra_body must be an object, not an array"
    return ""
  } catch (err) {
    return `Invalid JSON: ${err.message}`
  }
})

const hasAnyVariations = computed(() => form.variation_groups.some((g) => g.name && g.options.some((o) => o.name)))

const canSave = computed(() => {
  if (!form.name || !form.model || !form.provider) return false
  return true
})

const saveHint = computed(() => {
  if (!form.name) return "Name required"
  if (!form.model) return "Model required"
  if (!form.provider) return "Provider required"
  if (extraBodyError.value) return "Fix extra_body JSON"
  return ""
})

function parseValueInput(raw) {
  if (raw === "" || raw === undefined || raw === null) return ""
  try {
    return JSON.parse(raw)
  } catch {
    return raw
  }
}

function stringifyValueInput(value) {
  if (value === undefined || value === null) return ""
  if (typeof value === "string") return JSON.stringify(value)
  return JSON.stringify(value)
}

function resetForm() {
  form.name = ""
  form.model = ""
  form.provider = props.backends?.[0]?.name || ""
  form.max_context = 128000
  form.max_output = 16384
  form.temperature = null
  form.reasoning_effort = ""
  form.service_tier = ""
  form.extra_body = "{}"
  form.variation_groups = []
}

function loadPreset(preset) {
  if (!preset) {
    resetForm()
    return
  }
  form.name = preset.name || ""
  form.model = preset.model || ""
  form.provider = preset.provider || ""
  form.max_context = preset.max_context || 128000
  form.max_output = preset.max_output || 16384
  form.temperature = preset.temperature ?? null
  form.reasoning_effort = preset.reasoning_effort || ""
  form.service_tier = preset.service_tier || ""
  form.extra_body = JSON.stringify(preset.extra_body || {}, null, 2)
  form.variation_groups = deserializeGroups(preset.variation_groups || {})
  // reset preview to "base"
  Object.keys(previewSelection).forEach((k) => delete previewSelection[k])
}

function deserializeGroups(value) {
  return Object.entries(value || {}).map(([groupName, options]) => ({
    name: groupName,
    options: Object.entries(options || {}).map(([optionName, patch]) => ({
      name: optionName,
      patches: Object.entries(patch || {}).map(([path, val]) => {
        const dot = path.indexOf(".")
        if (dot < 0) {
          return { root: path, subpath: "", value: stringifyValueInput(val) }
        }
        const root = path.slice(0, dot)
        const subpath = path.slice(dot + 1)
        return { root, subpath, value: stringifyValueInput(val) }
      }),
    })),
  }))
}

function serializeGroups(groups) {
  const result = {}
  for (const group of groups) {
    if (!group.name) continue
    const options = {}
    for (const option of group.options || []) {
      if (!option.name) continue
      const patch = {}
      for (const row of option.patches || []) {
        if (!row.root) continue
        const path = row.root === "extra_body" && row.subpath ? `extra_body.${row.subpath}` : row.root
        patch[path] = parseValueInput(row.value)
      }
      options[option.name] = patch
    }
    result[group.name] = options
  }
  return result
}

function formatExtraBody() {
  if (!form.extra_body.trim()) {
    form.extra_body = "{}"
    return
  }
  try {
    const parsed = JSON.parse(form.extra_body)
    form.extra_body = JSON.stringify(parsed, null, 2)
  } catch {
    /* leave as-is */
  }
}

function addGroup() {
  form.variation_groups.push({ name: "", options: [{ name: "", patches: [] }] })
}

function addOption(group) {
  group.options.push({ name: "", patches: [] })
}

function addPatch(option) {
  option.patches.push({ root: "extra_body", subpath: "", value: "" })
}

function onRootChange(patch) {
  if (patch.root !== "extra_body") {
    patch.subpath = ""
  }
}

function deepMerge(base, override) {
  if (override === null || typeof override !== "object" || Array.isArray(override)) return override
  const out = { ...(base || {}) }
  for (const [k, v] of Object.entries(override)) {
    out[k] = typeof v === "object" && v !== null && !Array.isArray(v) && typeof out[k] === "object" && out[k] !== null && !Array.isArray(out[k]) ? deepMerge(out[k], v) : v
  }
  return out
}

function setDottedPath(obj, path, value) {
  const parts = path.split(".")
  const cur = obj
  let node = cur
  for (let i = 0; i < parts.length - 1; i++) {
    const key = parts[i]
    if (typeof node[key] !== "object" || node[key] === null || Array.isArray(node[key])) {
      node[key] = {}
    }
    node = node[key]
  }
  node[parts[parts.length - 1]] = value
}

// Preview is computed as a single {json, error} object; the template reads
// the two fields via separate computed wrappers. Writing to a ref inside a
// computed would be a lint error (vue/no-side-effects-in-computed-properties).
const preview = computed(() => {
  let error = ""
  let extra
  try {
    extra = form.extra_body ? JSON.parse(form.extra_body) : {}
  } catch (err) {
    error = `extra_body: ${err.message}`
    extra = {}
  }
  const resolved = {
    model: form.model,
    provider: form.provider,
    max_context: form.max_context,
    max_output: form.max_output,
  }
  if (form.temperature !== null) resolved.temperature = form.temperature
  if (form.reasoning_effort) resolved.reasoning_effort = form.reasoning_effort
  if (form.service_tier) resolved.service_tier = form.service_tier
  if (extra && Object.keys(extra).length) resolved.extra_body = JSON.parse(JSON.stringify(extra))

  // Apply selected variations (for preview only)
  for (const group of form.variation_groups) {
    if (!group.name) continue
    const optionName = previewSelection[group.name]
    if (!optionName) continue
    const option = group.options.find((o) => o.name === optionName)
    if (!option) continue
    for (const row of option.patches || []) {
      if (!row.root) continue
      const path = row.root === "extra_body" && row.subpath ? `extra_body.${row.subpath}` : row.root
      try {
        setDottedPath(resolved, path, parseValueInput(row.value))
      } catch (err) {
        error = `${path}: ${err.message}`
      }
    }
  }
  return { json: JSON.stringify(resolved, null, 2), error }
})

const previewJSON = computed(() => preview.value.json)
const previewError = computed(() => preview.value.error)

async function save() {
  if (!canSave.value) return
  saving.value = true
  try {
    let extraBody = {}
    try {
      extraBody = form.extra_body ? JSON.parse(form.extra_body) : {}
    } catch (err) {
      throw new Error(`extra_body JSON invalid: ${err.message}`)
    }
    const payload = {
      name: form.name,
      model: form.model,
      provider: form.provider,
      max_context: form.max_context,
      max_output: form.max_output,
      temperature: form.temperature,
      reasoning_effort: form.reasoning_effort,
      service_tier: form.service_tier,
      extra_body: extraBody,
      variation_groups: serializeGroups(form.variation_groups),
    }
    emit("save", payload)
  } finally {
    saving.value = false
  }
}

watch(
  () => props.preset,
  (preset) => loadPreset(preset),
  { immediate: true, deep: false },
)
</script>

<style scoped>
.field-label {
  display: block;
  font-size: 11px;
  color: var(--el-text-color-secondary, #909399);
  margin-bottom: 0.25rem;
}
.section-title {
  font-weight: 500;
  font-size: 12px;
  color: var(--el-text-color-primary);
  margin-bottom: 0.75rem;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
.variation-group {
  border: 1px solid rgba(120, 109, 98, 0.18);
  border-radius: 6px;
  padding: 0.75rem;
  margin-bottom: 0.75rem;
  background: rgba(120, 109, 98, 0.03);
}
.variation-option {
  padding-left: 0.75rem;
  border-left: 2px solid rgba(120, 109, 98, 0.15);
  margin-bottom: 0.5rem;
}
.variation-patch {
  display: flex;
  align-items: center;
  gap: 0.35rem;
  margin-bottom: 0.35rem;
  padding-left: 0.5rem;
}
.preview-pre {
  font-family: ui-monospace, monospace;
  font-size: 11px;
  background: rgba(0, 0, 0, 0.04);
  border: 1px solid rgba(120, 109, 98, 0.15);
  border-radius: 4px;
  padding: 0.5rem;
  max-height: 14rem;
  overflow: auto;
  white-space: pre;
  color: var(--el-text-color-regular, #606266);
}
:root.dark .preview-pre {
  background: rgba(255, 255, 255, 0.04);
  color: #d0cbc5;
}
</style>
