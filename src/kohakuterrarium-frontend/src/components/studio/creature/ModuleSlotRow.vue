<template>
  <div class="border border-transparent hover:border-warm-200 dark:hover:border-warm-800 rounded transition-colors">
    <!-- Compact row -->
    <div :class="['group flex items-center gap-2 px-2 py-1.5 rounded cursor-default', expanded && !inherited ? 'bg-warm-100/60 dark:bg-warm-800/60' : 'hover:bg-warm-100/60 dark:hover:bg-warm-800/60']" @mouseenter="$emit('hover')" @mouseleave="$emit('leave')">
      <button v-if="!inherited" class="shrink-0 w-4 h-4 inline-flex items-center justify-center text-warm-500 hover:text-warm-700 dark:hover:text-warm-300" :title="expanded ? t('studio.creature.modules.collapse') : t('studio.creature.modules.expand')" @click.stop="expanded = !expanded">
        <div :class="[expanded ? 'i-carbon-chevron-down' : 'i-carbon-chevron-right', 'text-xs']" />
      </button>
      <span v-else class="shrink-0 w-4" aria-hidden="true" />

      <span :class="['shrink-0 text-[10px] font-mono uppercase tracking-wider px-1.5 py-0.5 rounded', kindBadgeClass]">
        {{ kind }}
      </span>
      <div :class="[kindIcon, 'text-sm text-warm-500 shrink-0']" />
      <span class="flex-1 text-sm font-mono text-warm-800 dark:text-warm-200 truncate">
        {{ name }}
      </span>

      <span v-if="inherited" class="text-[10px] font-medium px-1.5 py-0.5 rounded bg-warm-200/60 dark:bg-warm-800/60 text-warm-500 dark:text-warm-400" :title="t('studio.creature.modules.inheritedFrom')"> inherit </span>
      <span v-else-if="type !== 'builtin' && type !== 'trigger'" class="text-[10px] font-medium px-1.5 py-0.5 rounded bg-amber/20 text-amber-shadow dark:text-amber">
        {{ type }}
      </span>

      <button v-if="!inherited" class="w-6 h-6 inline-flex items-center justify-center rounded text-warm-500 hover:bg-coral/20 hover:text-coral opacity-0 group-hover:opacity-100 transition-opacity" :title="t('studio.creature.modules.remove')" @click.stop="$emit('remove')">
        <div class="i-carbon-close text-sm" />
      </button>
      <button v-else class="w-6 h-6 inline-flex items-center justify-center rounded text-warm-400 opacity-0 group-hover:opacity-100 transition-opacity cursor-not-allowed" :title="t('studio.creature.modules.convertOverride')" disabled>
        <div class="i-carbon-edit text-sm" />
      </button>
    </div>

    <!-- Expanded panel -->
    <div v-if="expanded && !inherited" class="ml-4 mr-1 mb-1 border-l-2 border-iolite/30 pl-3 py-2 flex flex-col gap-2">
      <!-- Custom-type: module + class inputs -->
      <div v-if="type === 'custom' || type === 'package'" class="grid grid-cols-2 gap-2">
        <KField :label="t('studio.moduleOptions.module')">
          <KInput :model-value="entry.module || ''" placeholder="./custom/my_tool.py" @update:model-value="emitTopLevel('module', $event || undefined)" />
        </KField>
        <KField :label="t('studio.moduleOptions.className')">
          <KInput :model-value="classValue" placeholder="MyTool" @update:model-value="emitTopLevel('class', $event || undefined)" />
        </KField>
      </div>

      <!-- Plugin with a free-form options=dict param: render ONE JSON
           editor that writes to entry.options wholesale. -->
      <template v-if="isPluginOptionsBlob">
        <KField :label="t('studio.plugin.optionsLabel')" :hint="t('studio.plugin.optionsHint')">
          <textarea :value="optionsBlobText" rows="5" class="w-full px-2.5 py-1.5 rounded-md text-xs font-mono bg-warm-50 dark:bg-warm-950 border border-warm-200 dark:border-warm-700 focus:outline-none focus:border-iolite resize-y" :placeholder="`{\n  &quot;budget_usd&quot;: 5.0\n}`" @change="onOptionsBlobChange" />
        </KField>
        <div v-if="optionsBlobError" class="text-[11px] text-coral -mt-1">
          {{ optionsBlobError }}
        </div>
      </template>

      <!-- Regular schema-driven form -->
      <template v-else>
        <div v-if="schemaLoading" class="text-[11px] text-warm-500">
          {{ t("studio.common.loading") }}
        </div>
        <div v-else-if="schemaError" class="text-[11px] text-coral">
          {{ schemaError }}
        </div>
        <div v-else-if="!params.length" class="text-[11px] text-warm-500 italic">
          {{ t("studio.schema.noOptions") }}
        </div>

        <SchemaFormField v-for="p in params" :key="p.name" :param="p" :model-value="readField(p.name)" @change="emitField(p.name, $event)" />
      </template>

      <div v-for="w in schemaWarnings" :key="w.code" class="text-[11px] text-warm-500 italic flex items-center gap-1">
        <div class="i-carbon-information text-xs" />
        {{ w.message }}
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from "vue"

import KField from "@/components/studio/common/KField.vue"
import KInput from "@/components/studio/common/KInput.vue"
import { schemaAPI } from "@/utils/studio/api"
import { useI18n } from "@/utils/i18n"

import SchemaFormField from "./SchemaFormField.vue"

const { t } = useI18n()

const props = defineProps({
  kind: { type: String, required: true }, // tool | subagent | trigger | plugin
  name: { type: String, required: true },
  type: { type: String, default: "builtin" },
  inherited: { type: Boolean, default: false },
  entry: { type: Object, default: () => ({}) },
})

const emit = defineEmits(["hover", "leave", "remove", "patch"])

const expanded = ref(false)

/** Top-level fields on the entry (module / class / name / type etc.)
 *  are written directly regardless of kind. */
function emitTopLevel(key, value) {
  emit("patch", key, value)
}

/** For plugins, schema fields map to `entry.options.<key>` (the plugin
 *  __init__ takes ``options: dict``). For every other kind, fields
 *  are top-level on the entry (per kt-biome YAML convention — core's
 *  _parse_tool_config hoists non-reserved top-level keys into the
 *  tool's options dict at load time). */
function emitField(key, value) {
  if (props.kind === "plugin") {
    emit("patch", `options.${key}`, value)
  } else {
    emit("patch", key, value)
  }
}

function readField(name) {
  if (props.kind === "plugin") return props.entry.options?.[name]
  return props.entry[name]
}

// Schema fetching
const schemaLoading = ref(false)
const schemaError = ref("")
const params = ref([])
const schemaWarnings = ref([])

watch(
  () => [expanded.value, props.kind, props.type, props.entry?.module, props.entry?.class, props.entry?.class_name],
  async ([exp]) => {
    if (!exp || props.inherited) return
    await fetchSchema()
  },
  { immediate: false },
)

async function fetchSchema() {
  schemaLoading.value = true
  schemaError.value = ""
  try {
    const kindToApi = {
      tool: "tools",
      subagent: "subagents",
      trigger: "triggers",
      plugin: "plugins",
    }
    const res = await schemaAPI.moduleSchema({
      kind: kindToApi[props.kind] || props.kind,
      name: props.name,
      type: props.type || "builtin",
      module: props.entry.module || null,
      class_name: props.entry.class || props.entry.class_name || null,
    })
    params.value = res?.params || []
    schemaWarnings.value = res?.warnings || []
  } catch (e) {
    schemaError.value = e?.message || String(e)
    params.value = []
    schemaWarnings.value = []
  } finally {
    schemaLoading.value = false
  }
}

/** Plugins whose __init__ signature is ``(self, options: dict)`` —
 *  very common in kt-biome. The schema parser returns a single param
 *  named ``options`` with a dict type hint; that's indistinguishable
 *  from real nested options from the frontend's perspective.
 *
 *  Special-case: render ONE JSON editor that writes to the entry's
 *  ``options:`` dict wholesale, instead of writing to
 *  ``entry.options.options`` (which would be wrong). */
const isPluginOptionsBlob = computed(() => {
  if (props.kind !== "plugin") return false
  if (params.value.length !== 1) return false
  const p = params.value[0]
  if (p.name !== "options") return false
  const hint = (p.type_hint || "").toLowerCase()
  return hint.includes("dict") || hint === "" || hint.includes("any")
})

const optionsBlobError = ref("")
const optionsBlobText = computed(() => {
  const v = props.entry.options
  if (v == null) return ""
  try {
    return JSON.stringify(v, null, 2)
  } catch {
    return String(v)
  }
})

function onOptionsBlobChange(e) {
  const text = e.target.value.trim()
  if (!text) {
    optionsBlobError.value = ""
    emit("patch", "options", undefined)
    return
  }
  let parsed
  try {
    parsed = JSON.parse(text)
  } catch (err) {
    optionsBlobError.value = err.message
    return
  }
  if (typeof parsed !== "object" || Array.isArray(parsed) || parsed == null) {
    optionsBlobError.value = t("studio.plugin.optionsMustBeObject")
    return
  }
  optionsBlobError.value = ""
  emit("patch", "options", parsed)
}

const classValue = computed(() => props.entry.class ?? props.entry.class_name ?? "")

const kindIcon = computed(() => {
  switch (props.kind) {
    case "tool":
      return "i-carbon-tool-kit"
    case "subagent":
      return "i-carbon-bot"
    case "trigger":
      return "i-carbon-alarm"
    case "plugin":
      return "i-carbon-plug"
    default:
      return "i-carbon-document"
  }
})

const kindBadgeClass = computed(() => {
  switch (props.kind) {
    case "tool":
      return "bg-aquamarine/15 text-aquamarine-shadow dark:text-aquamarine"
    case "subagent":
      return "bg-iolite/15 text-iolite dark:text-iolite-light"
    case "trigger":
      return "bg-amber/15 text-amber-shadow dark:text-amber"
    case "plugin":
      return "bg-taaffeite/15 text-taaffeite-shadow dark:text-taaffeite"
    default:
      return "bg-warm-200/60 dark:bg-warm-800/60 text-warm-600 dark:text-warm-300"
  }
})
</script>
