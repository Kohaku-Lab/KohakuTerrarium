<template>
  <div class="flex flex-col gap-4" data-test="module-settings">
    <div class="flex items-center gap-2 flex-wrap">
      <button v-for="kind in ['tools', 'plugins']" :key="kind" type="button" class="px-3 py-1.5 rounded-lg text-sm" :class="tab === kind ? 'bg-iolite/10 text-iolite' : 'text-warm-500'" :aria-pressed="tab === kind" @click="tab = kind">{{ t(`settings.modules.${kind}`) }}</button>
      <el-input v-model="query" size="small" clearable class="!w-48 ml-auto" :placeholder="t('settings.modules.filter')" />
      <button type="button" class="i-carbon-renew text-warm-500" :aria-label="t('settings.modules.refresh')" @click="load" />
    </div>
    <p v-if="error" class="text-sm text-coral" role="alert">{{ error }}</p>
    <p v-if="loading" class="text-sm text-warm-500">{{ t("settings.modules.loading") }}</p>
    <p v-else-if="!visible.length" class="text-sm text-warm-500">{{ t("settings.modules.empty") }}</p>
    <div v-for="module in visible" :key="keyOf(module)" class="rounded-lg border kt-v2-line">
      <div class="flex items-center gap-3 px-3 py-3">
        <button type="button" class="flex-1 min-w-0 text-left" :aria-expanded="selected === keyOf(module)" @click="selected = selected === keyOf(module) ? null : keyOf(module)">
          <span class="font-mono text-sm text-warm-800 dark:text-warm-100">{{ module.name }}</span>
          <span v-if="module.type === 'native_tool'" class="ml-2 text-xs text-warm-500">{{ t("settings.modules.native") }}</span>
          <span v-if="module.description" :title="module.description" class="block text-xs text-warm-500 mt-1">{{ module.name === "web_search" && module.type === "tool" ? t("settings.search.description") : module.description }}</span>
        </button>
        <el-switch v-if="module.type === 'plugin'" :model-value="module.enabled" :loading="toggling === module.name" :disabled="Boolean(toggling)" :aria-label="module.name" @change="toggle(module, $event)" />
        <span class="i-carbon-chevron-down text-warm-400" />
      </div>
      <div v-if="selected === keyOf(module)" class="border-t kt-v2-line px-3 py-4">
        <ModuleEditForm ref="editors" :name="module.name" :schema="module.schema" :values="module.options" :presentation="module.type === 'tool' && module.name === 'web_search' ? searchPresentation : undefined" :submit-fn="submitter(module)" @applied="module.options = $event.options" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, ref } from "vue"

import { webSearchPresentation } from "./searchPresentation"

import ModuleEditForm from "@/components/panels/modules/ModuleEditForm.vue"
import { useSectionLoad } from "@/components/session-v2/settings/useSectionTarget"
import { moduleAPI, moduleDefaultsAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"

const props = defineProps({
  sessionId: { type: String, default: "" },
  creatureId: { type: String, default: "" },
})

const { t } = useI18n()
const searchPresentation = (values) => webSearchPresentation(values, t)
const sessionId = props.sessionId
const creatureId = props.creatureId
const editors = ref([])
const modules = ref([])
const tab = ref("tools")
const query = ref("")
const selected = ref(null)
const loading = ref(false)
const error = ref("")
const toggling = ref("")
let generation = 0

const keyOf = (module) => `${module.type}:${module.name}`
const visible = computed(() =>
  modules.value.filter((module) => {
    const matchesType = tab.value === "plugins" ? module.type === "plugin" : module.type !== "plugin"
    return matchesType && `${module.name} ${module.description || ""}`.toLowerCase().includes(query.value.toLowerCase())
  }),
)

async function load() {
  const current = ++generation
  loading.value = true
  error.value = ""
  try {
    if ((await editors.value[0]?.flushSave()) === false) return
    const fresh = sessionId ? await moduleAPI.list(sessionId, creatureId) : await moduleDefaultsAPI.list()
    if (current === generation) modules.value = fresh
  } catch (err) {
    if (current === generation) error.value = err?.response?.data?.detail || err.message
  } finally {
    if (current === generation) loading.value = false
  }
}

function submitter(module) {
  const kind = module.type
  return (name, values) => {
    const patch = sessionId && kind === "native_tool" ? { ...module.options, ...values } : values
    return sessionId ? moduleAPI.setOptions(sessionId, creatureId, kind, name, patch) : moduleDefaultsAPI.patch(kind, name, { values })
  }
}

async function toggle(module, enabled) {
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
