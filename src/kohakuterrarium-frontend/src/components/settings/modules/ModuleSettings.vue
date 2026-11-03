<template>
  <div class="flex flex-col gap-4" data-test="module-settings">
    <div class="flex items-center gap-2 flex-wrap">
      <button v-for="kind in ['tools', 'plugins']" :key="kind" type="button" class="px-3 py-1.5 rounded-lg text-sm" :class="tab === kind ? 'bg-iolite/10 text-iolite' : 'text-warm-500'" :aria-pressed="tab === kind" :disabled="busy" @click="changeView(() => (tab = kind))">{{ t(`settings.modules.${kind}`) }}</button>
      <el-input :model-value="query" :disabled="busy" @update:model-value="changeView(() => (query = $event))" size="small" clearable class="!w-48 ml-auto" :placeholder="t('settings.modules.filter')" />
      <button type="button" class="i-carbon-renew text-warm-500" :aria-label="t('settings.modules.refresh')" :disabled="busy" @click="load" />
    </div>
    <p v-if="error" class="text-sm text-coral" role="alert">{{ t("settings.moduleForm.operationError") }}: {{ error }}</p>
    <p v-if="loading" class="text-sm text-warm-500">{{ t("settings.modules.loading") }}</p>
    <p v-else-if="!visible.length" class="text-sm text-warm-500">{{ t("settings.modules.empty") }}</p>
    <div v-for="module in visible" :key="keyOf(module)" class="rounded-lg border kt-v2-line">
      <div class="flex items-center gap-3 px-3 py-3">
        <component :is="hasOptions(module) ? 'button' : 'div'" :type="hasOptions(module) ? 'button' : undefined" :disabled="hasOptions(module) ? busy : undefined" class="flex-1 min-w-0 text-left" :aria-expanded="hasOptions(module) ? selected === keyOf(module) : undefined" @click="hasOptions(module) && changeView(() => select(module))">
          <span class="font-mono text-sm text-warm-800 dark:text-warm-100">{{ module.name }}</span>
          <span v-if="module.type === 'native_tool'" class="ml-2 text-xs text-warm-500">{{ t("settings.modules.native") }}</span>
          <span v-if="descriptionOf(module)" :title="descriptionOf(module)" class="block text-xs text-warm-500 mt-1">{{ descriptionOf(module) }}</span>
        </component>
        <el-switch v-if="module.type === 'plugin'" :model-value="module.enabled" :loading="toggling === module.name" :disabled="busy" :aria-label="module.name" @change="toggle(module, $event)" />
        <span class="w-4 shrink-0" aria-hidden="true">
          <span v-if="hasOptions(module)" class="i-carbon-chevron-down text-warm-400" />
        </span>
      </div>
      <div v-if="hasOptions(module) && selected === keyOf(module)" class="border-t kt-v2-line px-3 py-4">
        <ModuleEditForm ref="editors" :name="module.name" :schema="module.schema" :values="editorValues" :disabled="busy" :presentation="presentationOf(module)" :submit-fn="submitter(module)" @applied="editorValues = $event.options" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref } from "vue"

import { moduleDescription, modulePresentation, moduleSearchText } from "./modulePresentation"

import ModuleEditForm from "@/components/panels/modules/ModuleEditForm.vue"
import { useSectionLoad } from "@/components/session-v2/settings/useSectionTarget"
import { moduleAPI, moduleDefaultsAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"

const props = defineProps({
  sessionId: { type: String, default: "" },
  creatureId: { type: String, default: "" },
})

const { t } = useI18n()
const descriptionOf = (module) => moduleDescription(module, t)
const presentationOf = (module) => (values) => modulePresentation(module, values, t)
const sessionId = props.sessionId
const creatureId = props.creatureId
const editors = ref([])
const modules = ref([])
const tab = ref("tools")
const query = ref("")
const selected = ref(null)
const editorValues = ref({})
const changingView = ref(false)
const loading = ref(false)
const error = ref("")
const toggling = ref("")
let generation = 0
const busy = computed(() => loading.value || changingView.value || Boolean(toggling.value))

const hasOptions = (module) => Object.keys(module.schema || {}).length > 0
const keyOf = (module) => `${module.type}:${module.name}`
const visible = computed(() =>
  modules.value.filter((module) => {
    const matchesType = tab.value === "plugins" ? module.type === "plugin" : module.type !== "plugin"
    return matchesType && moduleSearchText(module).toLowerCase().includes(query.value.toLowerCase())
  }),
)

function select(module) {
  selected.value = selected.value === keyOf(module) ? null : keyOf(module)
  editorValues.value = module.options
}

async function changeView(action) {
  if (busy.value) return
  changingView.value = true
  try {
    if ((await editors.value[0]?.flushSave()) !== false) action()
  } finally {
    changingView.value = false
  }
}

async function load() {
  if (busy.value) return
  const current = ++generation
  loading.value = true
  error.value = ""
  try {
    if ((await editors.value[0]?.flushSave()) === false) return
    const fresh = sessionId ? await moduleAPI.list(sessionId, creatureId) : await moduleDefaultsAPI.list()
    if (current === generation) {
      modules.value = fresh
      editorValues.value = fresh.find((module) => keyOf(module) === selected.value)?.options || {}
    }
  } catch (err) {
    if (current === generation) error.value = err?.response?.data?.detail || err.message
  } finally {
    if (current === generation) loading.value = false
  }
}

function submitter(module) {
  const kind = module.type
  return async (name, values) => {
    const patch = sessionId && kind === "native_tool" ? { ...module.options, ...values } : values
    const result = await (sessionId ? moduleAPI.setOptions(sessionId, creatureId, kind, name, patch) : moduleDefaultsAPI.patch(kind, name, { values }))
    module.options = { ...module.options, ...values, ...result?.options }
    return result
  }
}

async function toggle(module, enabled) {
  if (busy.value) return
  toggling.value = module.name
  error.value = ""
  try {
    const result = sessionId ? await moduleAPI.toggle(sessionId, creatureId, module.type, module.name) : await moduleDefaultsAPI.patch(module.type, module.name, { enabled })
    module.enabled = result.enabled
  } catch (err) {
    error.value = err?.response?.data?.detail || err.message
  } finally {
    toggling.value = ""
  }
}

useSectionLoad(load, [])
onBeforeUnmount(() => generation++)
</script>
