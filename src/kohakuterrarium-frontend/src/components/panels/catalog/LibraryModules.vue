<template>
  <div class="kt-v2 flex flex-col gap-2" data-test="library-modules">
    <div v-if="kinds.length > 1" class="flex flex-wrap gap-1">
      <button type="button" class="chip" :class="!kind ? 'chip-iolite' : 'chip-warm hover:bg-warm-200 dark:hover:bg-warm-700'" data-test="module-kind-all" @click="kind = ''">{{ t("lab.library.allKinds") }}</button>
      <button v-for="k in kinds" :key="k" type="button" class="chip" :class="kind === k ? 'chip-iolite' : 'chip-warm hover:bg-warm-200 dark:hover:bg-warm-700'" :data-test="`module-kind-${k}`" @click="kind = kind === k ? '' : k">{{ k }}</button>
    </div>

    <div v-if="loading && !modules.length" class="py-8 text-center text-[12px] text-warm-500">{{ t("common.loading") }}</div>
    <div v-else-if="error" class="rounded border border-coral/40 px-3 py-2 text-[12px] text-coral" role="alert">{{ error }}</div>
    <div v-else-if="!filtered.length" class="py-10 text-center text-[12px] text-warm-500" data-test="modules-empty">{{ modules.length ? t("lab.library.noModuleMatch") : t("extensions.none") }}</div>

    <ul v-else class="kt-v2-card !p-0 overflow-hidden">
      <li v-for="m in filtered" :key="`${m.kind}/${m.package}/${m.name}`" class="kt-v2-line border-b last:border-b-0 flex items-start gap-3 px-3 py-2" :data-test="`module-${m.name}`">
        <span :class="iconFor(m.kind)" class="mt-0.5 shrink-0 text-warm-500" />
        <div class="min-w-0 flex-1">
          <div class="flex items-center gap-2 min-w-0">
            <span class="text-[13px] font-medium text-warm-800 dark:text-warm-100 truncate">{{ m.name }}</span>
            <span class="chip-warm !text-[10px] shrink-0">{{ m.kind }}</span>
            <span v-if="m.editable" class="chip-iolite !text-[10px] shrink-0">{{ t("extensions.editable") }}</span>
            <span class="flex-1" />
            <span class="text-[11px] font-mono text-warm-400 shrink-0"
              >{{ m.package }}<template v-if="m.package_version">@{{ m.package_version }}</template></span
            >
          </div>
          <div v-if="m.description || m.module" class="flex items-center gap-2 min-w-0 text-[11px] text-warm-500">
            <span v-if="m.description" class="truncate">{{ m.description }}</span>
            <span v-if="m.module" class="font-mono text-warm-400 truncate shrink-0 max-w-[45%]" :title="m.module">{{ m.module }}</span>
          </div>
        </div>
      </li>
    </ul>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from "vue"

import { extensionsAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"

/**
 * Every module the installed packages contribute (plugins, tools, triggers,
 * I/O, presets, skills, commands, prompts) as one flat list, filtered by the
 * Library's search `query` and a kind chip. `load()` re-reads it.
 */
const props = defineProps({ query: { type: String, default: "" } })
const emit = defineEmits(["count"])

const KIND_ICON = {
  plugin: "i-carbon-plug",
  tool: "i-carbon-tools",
  trigger: "i-carbon-flash",
  io: "i-carbon-data-table",
  "llm-preset": "i-carbon-machine-learning-model",
  skill: "i-carbon-skill-level",
  command: "i-carbon-command-line",
  "user-command": "i-carbon-user",
  prompt: "i-carbon-document",
}

const { t } = useI18n()
const modules = ref([])
const loading = ref(false)
const error = ref("")
const kind = ref("")
let request = 0

const kinds = computed(() => [...new Set(modules.value.map((m) => m.kind))].sort())
const filtered = computed(() => {
  const q = props.query.trim().toLowerCase()
  return modules.value.filter((m) => {
    if (kind.value && m.kind !== kind.value) return false
    return !q || `${m.name} ${m.package} ${m.description || ""} ${m.module || ""}`.toLowerCase().includes(q)
  })
})

function iconFor(k) {
  return KIND_ICON[k] || "i-carbon-cube"
}

async function load() {
  const mine = ++request
  loading.value = true
  error.value = ""
  try {
    const data = await extensionsAPI.list()
    if (mine !== request) return
    modules.value = Array.isArray(data) ? data : []
    emit("count", modules.value.length)
  } catch (err) {
    if (mine === request) error.value = err?.response?.data?.detail || err?.message || String(err)
  } finally {
    if (mine === request) loading.value = false
  }
}

onMounted(load)
defineExpose({ load })
</script>
