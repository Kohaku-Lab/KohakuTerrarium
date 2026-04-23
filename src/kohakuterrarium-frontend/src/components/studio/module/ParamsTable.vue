<template>
  <div class="flex flex-col gap-2">
    <div v-if="!params.length" class="text-xs text-warm-500 italic px-1">
      {{ t("studio.module.params.empty") }}
    </div>
    <div v-for="(p, i) in params" :key="i" class="flex flex-col gap-1 border border-warm-200 dark:border-warm-800 rounded-md px-2 py-2 bg-warm-50/40 dark:bg-warm-950/40">
      <div class="flex items-center gap-2">
        <KInput :model-value="p.name" class="flex-1" placeholder="param_name" @update:model-value="update(i, 'name', $event)" />
        <KSelect :model-value="p.type_hint || 'str'" :options="TYPE_HINT_OPTIONS" @update:model-value="update(i, 'type_hint', $event)" />
        <KCheckbox :model-value="!!p.required" :label="t('studio.module.params.required')" @update:model-value="update(i, 'required', $event)" />
        <button class="w-7 h-7 inline-flex items-center justify-center rounded text-warm-500 hover:text-coral hover:bg-coral/10" :title="t('studio.module.params.remove')" @click="remove(i)">
          <div class="i-carbon-trash-can text-sm" />
        </button>
      </div>
      <div class="flex items-center gap-2">
        <KInput :model-value="defaultAsString(p.default)" class="flex-1" :placeholder="t('studio.module.params.defaultPlaceholder')" :disabled="p.required" @update:model-value="updateDefault(i, $event)" />
        <div class="flex items-center gap-0.5">
          <button class="w-6 h-6 inline-flex items-center justify-center rounded text-warm-500 hover:bg-warm-200 dark:hover:bg-warm-800 disabled:opacity-30 disabled:cursor-not-allowed" :disabled="i === 0" :title="t('studio.module.params.moveUp')" @click="move(i, -1)">
            <div class="i-carbon-chevron-up text-xs" />
          </button>
          <button class="w-6 h-6 inline-flex items-center justify-center rounded text-warm-500 hover:bg-warm-200 dark:hover:bg-warm-800 disabled:opacity-30 disabled:cursor-not-allowed" :disabled="i === params.length - 1" :title="t('studio.module.params.moveDown')" @click="move(i, 1)">
            <div class="i-carbon-chevron-down text-xs" />
          </button>
        </div>
      </div>
      <input :value="p.description || ''" class="w-full px-2 py-1 rounded text-[11px] bg-transparent border-0 focus:outline-none text-warm-600 dark:text-warm-400 focus:bg-warm-100/60 dark:focus:bg-warm-900/60" :placeholder="t('studio.module.params.descriptionPlaceholder')" @input="update(i, 'description', $event.target.value)" />
    </div>
    <button class="self-start inline-flex items-center gap-1 text-xs text-iolite hover:underline px-1 py-1" @click="add">
      <div class="i-carbon-add text-sm" />
      {{ t("studio.module.params.add") }}
    </button>
  </div>
</template>

<script setup>
import KCheckbox from "@/components/studio/common/KCheckbox.vue"
import KInput from "@/components/studio/common/KInput.vue"
import KSelect from "@/components/studio/common/KSelect.vue"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()

const TYPE_HINT_OPTIONS = [
  { value: "str", label: "str" },
  { value: "int", label: "int" },
  { value: "float", label: "float" },
  { value: "bool", label: "bool" },
  { value: "list[str]", label: "list[str]" },
  { value: "dict", label: "dict" },
  { value: "Any", label: "Any" },
]

const props = defineProps({
  /** Array of { name, type_hint, default, required, description }. */
  params: { type: Array, default: () => [] },
})

const emit = defineEmits(["update:params"])

function emitReplace(next) {
  emit("update:params", next)
}

function add() {
  emitReplace([...props.params, { name: "", type_hint: "str", default: null, required: false, description: "" }])
}

function remove(idx) {
  emitReplace(props.params.filter((_, i) => i !== idx))
}

function move(idx, delta) {
  const next = [...props.params]
  const target = idx + delta
  if (target < 0 || target >= next.length) return
  const [x] = next.splice(idx, 1)
  next.splice(target, 0, x)
  emitReplace(next)
}

function update(idx, key, value) {
  const next = props.params.map((p, i) => (i === idx ? { ...p, [key]: value } : p))
  // Clearing required means an empty default should become null, not undefined.
  if (key === "required" && value === true) next[idx].default = null
  emitReplace(next)
}

function updateDefault(idx, raw) {
  const p = props.params[idx]
  const hint = (p.type_hint || "").toLowerCase()
  let parsed
  if (raw === "" || raw == null) {
    parsed = null
  } else if (hint.includes("bool")) {
    parsed = raw.toLowerCase() === "true"
  } else if (hint === "int" || hint.includes("int")) {
    const n = Number(raw)
    parsed = Number.isFinite(n) ? Math.trunc(n) : raw
  } else if (hint === "float" || hint.includes("float")) {
    const n = Number(raw)
    parsed = Number.isFinite(n) ? n : raw
  } else if (hint.startsWith("list") || hint === "dict" || hint === "any") {
    // Let the author write JSON-ish literals; keep as string otherwise.
    try {
      parsed = JSON.parse(raw)
    } catch {
      parsed = raw
    }
  } else {
    parsed = raw
  }
  update(idx, "default", parsed)
}

function defaultAsString(value) {
  if (value == null) return ""
  if (typeof value === "object") {
    try {
      return JSON.stringify(value)
    } catch {
      return ""
    }
  }
  return String(value)
}
</script>
