<template>
  <div class="h-full min-h-0 flex flex-col" data-test="model-editor">
    <div class="flex-1 min-h-0 overflow-y-auto p-5 max-md:p-3 flex flex-col gap-5" data-editor-body>
      <header class="flex items-start gap-3 flex-wrap">
        <div class="flex-1 min-w-0">
          <div class="flex items-center gap-2 flex-wrap">
            <h3 class="text-lg font-semibold text-warm-800 dark:text-warm-100 break-all">{{ headerTitle }}</h3>
            <span v-if="builtin" class="chip-warm">{{ t("settings.models.builtin") }}</span>
            <span v-else-if="editing" class="chip-iolite">{{ t("settings.models.user") }}</span>
            <span v-else class="chip-amber">{{ t("settings.models.unsaved") }}</span>
            <span v-if="saved && preset?.is_default" class="chip-aqua" data-default-tag>{{ t("settings.models.isDefault") }}</span>
          </div>
          <p v-if="builtin" class="mt-1 kt-text-caption text-warm-500">{{ t("settings.models.builtinHint") }}</p>
        </div>
        <div class="flex items-center gap-2 flex-wrap">
          <KButton v-if="saved && !preset?.is_default" size="sm" :title="t('settings.models.setDefaultHint')" data-action="set-default" @click="emit('set-default', preset)"><span class="i-carbon-checkmark" />{{ t("settings.models.setAsDefault") }}</KButton>
          <KButton v-if="saved" size="sm" variant="primary" data-action="clone" @click="emit('clone', preset)"><span class="i-carbon-copy" />{{ t("settings.models.clone") }}</KButton>
          <KButton v-if="editing" size="sm" variant="ghost" data-action="delete" @click="emit('delete', preset)"><span class="i-carbon-trash-can text-coral" />{{ t("common.delete") }}</KButton>
        </div>
      </header>

      <SettingsGroup :title="t('settings.models.section.model')">
        <div class="p-4 grid gap-3 grid-cols-[repeat(auto-fit,minmax(10rem,1fr))]">
          <label class="flex flex-col gap-1">
            <span :class="LABEL">{{ t("settings.models.field.name") }}</span>
            <el-input v-model="form.name" size="small" :disabled="builtin || editing" placeholder="my-model" data-field="name" />
          </label>
          <label class="flex flex-col gap-1">
            <span :class="LABEL">{{ t("settings.models.field.model") }}</span>
            <el-input v-model="form.model" size="small" :disabled="builtin" placeholder="gpt-5.6" data-field="model" />
          </label>
          <label class="flex flex-col gap-1">
            <span :class="LABEL">{{ t("settings.models.field.provider") }}</span>
            <el-select v-model="form.provider" size="small" class="w-full" :disabled="builtin" :placeholder="t('settings.models.field.providerPlaceholder')" data-field="provider">
              <el-option v-for="backend in backends" :key="backend.name" :value="backend.name" :label="backendLabel(backend)" />
            </el-select>
          </label>
          <label class="flex flex-col gap-1">
            <span :class="LABEL">{{ t("settings.models.field.context") }}</span>
            <el-input-number v-model="form.max_context" size="small" :min="1024" :step="1024" :disabled="builtin" controls-position="right" class="!w-full" data-field="max_context" />
          </label>
          <label class="flex flex-col gap-1">
            <span :class="LABEL">{{ t("settings.models.field.output") }}</span>
            <el-input-number v-model="form.max_output" size="small" :min="1" :step="1024" :disabled="builtin" controls-position="right" class="!w-full" data-field="max_output" />
          </label>
        </div>
      </SettingsGroup>

      <SettingsGroup :title="t('settings.models.section.defaults')" :description="t('settings.models.section.defaultsHint')">
        <div class="p-4 grid gap-3 grid-cols-[repeat(auto-fit,minmax(10rem,1fr))]">
          <label class="flex flex-col gap-1">
            <span :class="LABEL">{{ t("settings.models.field.temperature") }}</span>
            <el-input-number v-model="form.temperature" size="small" :min="0" :max="2" :step="0.1" :precision="2" :disabled="builtin" controls-position="right" class="!w-full" :placeholder="t('settings.models.field.apiDefault')" />
          </label>
          <label class="flex flex-col gap-1">
            <span :class="LABEL">{{ t("settings.models.field.effort") }}</span>
            <el-input v-model="form.reasoning_effort" size="small" :disabled="builtin" placeholder="low / medium / high" />
          </label>
          <label class="flex flex-col gap-1">
            <span :class="LABEL">{{ t("settings.models.field.tier") }}</span>
            <el-input v-model="form.service_tier" size="small" :disabled="builtin" placeholder="default / priority / flex" />
          </label>
        </div>
      </SettingsGroup>

      <VariationEditor v-model:groups="form.variation_groups" :base="previewBase" :provider="form.provider" :name="form.name" :readonly="builtin" />

      <SettingsGroup :title="t('settings.models.section.advanced')">
        <template #action>
          <KButton size="sm" variant="ghost" :aria-expanded="advancedOpen" @click="advancedOpen = !advancedOpen">{{ advancedOpen ? t("software.advanced.hide") : t("software.advanced.show") }}<span :class="advancedOpen ? 'i-carbon-chevron-up' : 'i-carbon-chevron-down'" /></KButton>
        </template>
        <SettingsRow v-if="!advancedOpen" :title="t('settings.models.advancedHint')" />
        <div v-else class="p-4 flex flex-col gap-1">
          <div class="flex items-center justify-between">
            <span :class="LABEL">extra_body (JSON)</span>
            <KButton size="sm" variant="ghost" :disabled="builtin" @click="formatExtraBody">{{ t("settings.models.format") }}</KButton>
          </div>
          <el-input v-model="form.extra_body" type="textarea" :autosize="{ minRows: 4, maxRows: 16 }" :disabled="builtin" placeholder='{"reasoning":{"enabled":true,"effort":"high"}}' class="font-mono" data-field="extra_body" />
          <p v-if="extraBodyError" class="kt-text-caption text-coral font-mono">{{ extraBodyError }}</p>
        </div>
      </SettingsGroup>
    </div>

    <footer v-if="!builtin" class="kt-v2-panel kt-v2-line shrink-0 px-5 max-md:px-3 py-3 flex items-center gap-2 border-t" data-editor-footer>
      <KButton variant="primary" :disabled="!canSave" data-action="save" @click="save">{{ editing ? t("settings.models.update") : t("settings.models.create") }}</KButton>
      <KButton variant="ghost" @click="emit('cancel')">{{ t("common.cancel") }}</KButton>
      <span v-if="saveHint" class="kt-text-caption text-warm-500">{{ saveHint }}</span>
    </footer>
  </div>
</template>

<script setup>
import { computed, reactive, ref, watch } from "vue"

import KButton from "@/components/common/ActionButton.vue"
import VariationEditor from "@/components/settings/models/VariationEditor.vue"
import { deserializeGroups, isBlocking, serializeGroups, validateGroups } from "@/components/settings/models/variationModel"
import SettingsGroup from "@/components/settings/ui/SettingsGroup.vue"
import SettingsRow from "@/components/settings/ui/SettingsRow.vue"
import { useI18n } from "@/utils/i18n"

/** Edits one preset: model fields, defaults, variation groups and raw `extra_body`. Built-ins open read-only. */
const props = defineProps({
  preset: { type: Object, default: null },
  backends: { type: Array, default: () => [] },
  mode: { type: String, default: "new" }, // "new" | "edit" | "view"
})
const emit = defineEmits(["save", "cancel", "clone", "delete", "set-default"])

const { t } = useI18n()
const LABEL = "kt-text-caption text-warm-500"

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
const advancedOpen = ref(false)

const builtin = computed(() => props.mode === "view")
const editing = computed(() => props.mode === "edit")
// A "new" editor holds an unsaved copy, which can be neither the default nor cloned.
const saved = computed(() => props.mode !== "new")
const headerTitle = computed(() => form.name || t("settings.models.newPreset"))

function backendLabel(backend) {
  const parts = [backend.name]
  if (backend.backend_type) parts.push(`[${backend.backend_type}]`)
  if (backend.available === false) parts.push(`· ${t("settings.models.noKey")}`)
  return parts.join(" ")
}

const extraBodyError = computed(() => {
  if (!form.extra_body?.trim()) return ""
  try {
    const parsed = JSON.parse(form.extra_body)
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) return t("settings.models.extraBodyObject")
    return ""
  } catch (err) {
    return t("settings.models.extraBodyInvalid", { message: err.message })
  }
})

const variationErrors = computed(() => validateGroups(form.variation_groups).filter(isBlocking))

const canSave = computed(() => !!form.name && !!form.model && !!form.provider && !extraBodyError.value && !variationErrors.value.length)

const saveHint = computed(() => {
  if (!form.name) return t("settings.models.need.name")
  if (!form.model) return t("settings.models.need.model")
  if (!form.provider) return t("settings.models.need.provider")
  if (extraBodyError.value) return t("settings.models.need.extraBody")
  if (variationErrors.value.length) return t("settings.models.need.variations")
  return ""
})

const previewBase = computed(() => {
  let extra = {}
  try {
    extra = form.extra_body ? JSON.parse(form.extra_body) : {}
  } catch {
    extra = {}
  }
  const base = { model: form.model, provider: form.provider, max_context: form.max_context, max_output: form.max_output }
  if (form.temperature !== null && form.temperature !== undefined) base.temperature = form.temperature
  if (form.reasoning_effort) base.reasoning_effort = form.reasoning_effort
  if (form.service_tier) base.service_tier = form.service_tier
  if (extra && Object.keys(extra).length) base.extra_body = extra
  return base
})

function load(preset) {
  form.name = preset?.name || ""
  form.model = preset?.model || ""
  form.provider = preset?.provider || props.backends?.[0]?.name || ""
  form.max_context = preset?.max_context || 128000
  form.max_output = preset?.max_output || 16384
  form.temperature = preset?.temperature ?? null
  form.reasoning_effort = preset?.reasoning_effort || ""
  form.service_tier = preset?.service_tier || ""
  form.extra_body = JSON.stringify(preset?.extra_body || {}, null, 2)
  form.variation_groups = deserializeGroups(preset?.variation_groups)
  advancedOpen.value = Object.keys(preset?.extra_body || {}).length > 0
}

function formatExtraBody() {
  try {
    form.extra_body = JSON.stringify(JSON.parse(form.extra_body || "{}"), null, 2)
  } catch {
    // Left as typed; the error line says what is wrong.
  }
}

function save() {
  if (!canSave.value) return
  emit("save", {
    name: form.name,
    model: form.model,
    provider: form.provider,
    max_context: form.max_context,
    max_output: form.max_output,
    temperature: form.temperature,
    reasoning_effort: form.reasoning_effort,
    service_tier: form.service_tier,
    extra_body: form.extra_body ? JSON.parse(form.extra_body) : {},
    variation_groups: serializeGroups(form.variation_groups),
  })
}

watch(() => props.preset, load, { immediate: true })
</script>
