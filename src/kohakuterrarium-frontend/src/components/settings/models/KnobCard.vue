<template>
  <div class="px-4 py-3 flex flex-col gap-3" data-variation-group>
    <div class="flex items-center gap-2 min-w-0">
      <button type="button" class="btn-icon !w-7 !h-7 shrink-0" :aria-expanded="group.open" :aria-label="group.name" data-knob-toggle @click="group.open = !group.open"><span :class="group.open ? 'i-carbon-chevron-down' : 'i-carbon-chevron-right'" /></button>
      <span v-if="readonly" class="kt-text-body font-medium font-mono text-warm-800 dark:text-warm-100">{{ group.name }}</span>
      <el-input v-else v-model="group.name" size="small" class="!w-44 font-mono" :placeholder="t('settings.models.variations.groupName')" data-group-name />
      <span class="kt-text-caption text-warm-500 truncate flex-1 min-w-0 max-md:hidden">{{ summary }}</span>
      <span v-if="readonly" class="flex-1 md:hidden" />
      <KButton v-if="!readonly" size="sm" variant="ghost" :aria-label="t('settings.models.variations.removeGroup')" data-knob-remove @click="emit('remove')"><span class="i-carbon-trash-can" /></KButton>
    </div>

    <div v-if="!group.open" class="flex flex-wrap gap-1 pl-9">
      <span v-for="option in named" :key="option.name" class="chip-warm font-mono">{{ option.name }}</span>
      <span v-if="!named.length" class="kt-text-caption text-warm-400">{{ t("settings.models.variations.noOptions") }}</span>
    </div>

    <div v-else class="pl-9 flex flex-col gap-3">
      <div v-if="simple" class="flex flex-col gap-3">
        <div v-if="!readonly" class="grid gap-3 grid-cols-[repeat(auto-fit,minmax(12rem,1fr))]">
          <label class="flex flex-col gap-1">
            <span class="kt-text-caption text-warm-500">{{ t("settings.models.variations.target") }}</span>
            <el-autocomplete :model-value="group.target" size="small" class="w-full" :fetch-suggestions="suggest" placeholder="reasoning_effort" data-knob-target @update:model-value="(value) => setTarget(group, value)" />
          </label>
          <label class="flex flex-col gap-1">
            <span class="kt-text-caption text-warm-500">{{ t("settings.models.variations.valueType") }}</span>
            <el-select :model-value="group.type" size="small" class="w-full" @change="(value) => setType(group, value)">
              <el-option v-for="type in VALUE_TYPES" :key="type" :value="type" :label="t(`settings.models.variations.type.${type}`)" />
            </el-select>
          </label>
        </div>
        <p v-else class="kt-text-caption font-mono text-warm-600 dark:text-warm-300 break-all">{{ group.target || "—" }}</p>

        <div class="kt-v2-line border rounded-lg divide-y divide-[var(--v2-line)] overflow-hidden">
          <div v-for="(option, o) in group.options" :key="o" class="px-3 py-2 grid items-center gap-2 grid-cols-[minmax(0,9rem)_minmax(0,1fr)_auto] max-md:grid-cols-[minmax(0,1fr)_auto]" data-variation-option>
            <span v-if="readonly" class="kt-text-body font-mono">{{ option.name }}</span>
            <el-input v-else v-model="option.name" size="small" class="font-mono" :placeholder="t('settings.models.variations.optionName')" data-option-name />
            <span v-if="readonly" class="kt-text-caption font-mono text-warm-500 max-md:hidden">{{ optionValue(option) || t("settings.models.variations.noChange") }}</span>
            <template v-else>
              <el-select v-if="group.type === 'bool'" :model-value="optionValue(option)" size="small" clearable class="max-md:order-last max-md:col-span-2" :placeholder="t('settings.models.variations.noChange')" @update:model-value="(value) => setOptionValue(group, option, value || '')">
                <el-option value="true" :label="t('settings.models.variations.on')" />
                <el-option value="false" :label="t('settings.models.variations.off')" />
              </el-select>
              <el-input v-else :model-value="optionValue(option)" size="small" class="font-mono max-md:order-last max-md:col-span-2" :placeholder="t('settings.models.variations.noChange')" @update:model-value="(value) => setOptionValue(group, option, value)" />
              <KButton size="sm" variant="ghost" :aria-label="t('settings.models.variations.removeOption')" @click="group.options.splice(o, 1)"><span class="i-carbon-close" /></KButton>
            </template>
            <p v-for="(issue, i) in issuesOf(o)" :key="i" class="kt-text-caption text-coral col-span-full" data-variation-issue>{{ issueText(issue) }}</p>
          </div>
          <p v-if="!group.options.length" class="px-3 py-4 text-center kt-text-caption text-warm-400">{{ t("settings.models.variations.noOptions") }}</p>
        </div>

        <div v-if="!readonly" class="flex items-center gap-2 flex-wrap">
          <KButton size="sm" @click="group.options.push({ name: '', patches: [] })"><span class="i-carbon-add" />{{ t("settings.models.variations.addOption") }}</KButton>
          <el-input v-model="scaleText" size="small" class="!w-56" :placeholder="t('settings.models.variations.scalePlaceholder')" data-knob-scale @keyup.enter="applyScale" />
          <KButton size="sm" variant="ghost" :disabled="!scaleText.trim()" @click="applyScale">{{ t("settings.models.variations.addScale") }}</KButton>
        </div>
      </div>

      <div v-else class="flex flex-col gap-3">
        <div v-for="(option, o) in group.options" :key="o" class="kt-v2-line border rounded-lg p-3 flex flex-col gap-2" data-variation-option>
          <div class="flex items-center gap-2">
            <span v-if="readonly" class="kt-text-body font-mono flex-1">{{ option.name }}</span>
            <el-input v-else v-model="option.name" size="small" class="!w-44 font-mono" :placeholder="t('settings.models.variations.optionName')" data-option-name />
            <span v-if="!readonly" class="flex-1" />
            <template v-if="!readonly">
              <KButton size="sm" variant="ghost" @click="option.patches.push({ path: '', type: 'text', value: '' })"><span class="i-carbon-add" />{{ t("settings.models.variations.addPatch") }}</KButton>
              <KButton size="sm" variant="ghost" :aria-label="t('settings.models.variations.removeOption')" @click="group.options.splice(o, 1)"><span class="i-carbon-close" /></KButton>
            </template>
          </div>
          <p v-if="!option.patches.length" class="kt-text-caption text-warm-500">{{ t("settings.models.variations.noChange") }}</p>
          <template v-for="(patch, p) in option.patches" :key="p">
            <p v-if="readonly" class="kt-text-caption font-mono text-warm-600 dark:text-warm-300 break-all">{{ describePatch(patch) }}</p>
            <div v-else class="flex items-center gap-2 flex-wrap" data-variation-patch>
              <el-autocomplete v-model="patch.path" size="small" class="!w-64 max-md:!w-full" :fetch-suggestions="suggest" placeholder="extra_body.reasoning.effort" />
              <span class="text-warm-400">=</span>
              <el-select v-model="patch.type" size="small" class="!w-24" @change="patch.value = patch.type === 'bool' ? 'true' : ''">
                <el-option v-for="type in VALUE_TYPES" :key="type" :value="type" :label="t(`settings.models.variations.type.${type}`)" />
              </el-select>
              <el-select v-if="patch.type === 'bool'" v-model="patch.value" size="small" class="!w-24">
                <el-option value="true" :label="t('settings.models.variations.on')" />
                <el-option value="false" :label="t('settings.models.variations.off')" />
              </el-select>
              <el-input v-else v-model="patch.value" size="small" class="!flex-1 !min-w-32 font-mono" />
              <KButton size="sm" variant="ghost" :aria-label="t('settings.models.variations.removePatch')" @click="option.patches.splice(p, 1)"><span class="i-carbon-trash-can" /></KButton>
            </div>
          </template>
          <p v-for="(issue, i) in issuesOf(o)" :key="i" class="kt-text-caption text-coral" data-variation-issue>{{ issueText(issue) }}</p>
        </div>
        <div v-if="!readonly" class="flex items-center gap-2">
          <KButton size="sm" @click="group.options.push({ name: '', patches: [] })"><span class="i-carbon-add" />{{ t("settings.models.variations.addOption") }}</KButton>
        </div>
      </div>

      <div v-if="!readonly" class="flex items-center gap-2">
        <KButton v-if="simple" size="sm" variant="ghost" data-knob-mode @click="forceAdvanced = true">{{ t("settings.models.variations.perOption") }}</KButton>
        <KButton v-else-if="canSimplify" size="sm" variant="ghost" data-knob-mode @click="toSimple">{{ t("settings.models.variations.asTable") }}</KButton>
        <span v-else class="kt-text-caption text-warm-400">{{ t("settings.models.variations.needsAdvanced") }}</span>
      </div>
    </div>

    <p v-for="(issue, i) in groupIssues" :key="i" class="kt-text-caption text-coral pl-9" data-variation-issue>{{ issueText(issue) }}</p>
  </div>
</template>

<script setup>
import { computed, ref } from "vue"

import KButton from "@/components/common/ActionButton.vue"
import { adoptShape, describePatch, isSimple, optionValue, PATCH_TARGETS, setOptionValue, setTarget, setType, VALUE_TYPES, addScale } from "@/components/settings/models/variationModel"
import { useI18n } from "@/utils/i18n"

/** One variation knob: a table of values when it has a single target, per-option changes otherwise. */
const group = defineModel("group", { type: Object, required: true })
const props = defineProps({
  issues: { type: Array, default: () => [] },
  readonly: { type: Boolean, default: false },
})
const emit = defineEmits(["remove"])

const { t } = useI18n()
const scaleText = ref("")
const forceAdvanced = ref(false)

const canSimplify = computed(() => isSimple(group.value))
const simple = computed(() => canSimplify.value && !forceAdvanced.value)
const named = computed(() => group.value.options.filter((option) => option.name))
const groupIssues = computed(() => props.issues.filter((issue) => issue.option === undefined))
const issuesOf = (o) => props.issues.filter((issue) => issue.option === o)

const summary = computed(() => {
  const count = t("settings.models.variations.optionCount", { count: group.value.options.length })
  return canSimplify.value && group.value.target ? `${group.value.target} · ${count}` : count
})

function issueText(issue) {
  return t(`settings.models.variations.issue.${issue.code}`, issue.params || {})
}

function toSimple() {
  adoptShape(group.value)
  forceAdvanced.value = false
}

function applyScale() {
  addScale(group.value, scaleText.value)
  scaleText.value = ""
}

function suggest(text, done) {
  const needle = (text || "").toLowerCase()
  done(PATCH_TARGETS.filter((target) => target.includes(needle)).map((value) => ({ value })))
}
</script>
