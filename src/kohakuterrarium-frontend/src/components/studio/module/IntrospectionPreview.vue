<template>
  <div class="h-full flex flex-col overflow-hidden">
    <div class="shrink-0 flex items-center gap-2 px-3 py-2 border-b border-warm-200 dark:border-warm-800 text-xs text-warm-600 dark:text-warm-300">
      <div class="i-carbon-view text-sm" />
      <span class="font-medium">{{ t("studio.module.preview.title") }}</span>
      <div class="flex-1" />
      <span v-if="schemaLoading" class="text-[11px] text-warm-500">
        {{ t("studio.common.loading") }}
      </span>
    </div>

    <div class="flex-1 min-h-0 overflow-auto px-3 py-3 flex flex-col gap-3">
      <p class="text-[11px] text-warm-500 leading-relaxed">
        {{ t("studio.module.preview.hint", { kind }) }}
      </p>

      <!-- Rename warning — fires when the identity field has drifted
           from the URL name AND we know the module is wired. -->
      <div v-if="renameWarning" class="rounded border border-amber/30 bg-amber/10 text-amber-shadow dark:text-amber px-2 py-1.5 text-[11px] flex items-start gap-1.5">
        <div class="i-carbon-warning-alt text-sm shrink-0 mt-0.5" />
        <div>
          <div class="font-medium">{{ t("studio.module.preview.renameTitle") }}</div>
          <div class="mt-0.5 opacity-90">
            {{ t("studio.module.preview.renameHint", { old: name, count: usedCount }) }}
          </div>
        </div>
      </div>

      <!-- Schema preview -->
      <section class="flex flex-col gap-1.5">
        <div class="text-[11px] uppercase tracking-wider text-warm-500 font-medium">
          {{ t("studio.module.preview.schemaTitle") }}
        </div>
        <div v-if="schemaError" class="text-[11px] text-coral">{{ schemaError }}</div>
        <div v-else-if="!schemaParams.length && !schemaLoading" class="text-[11px] text-warm-500 italic">
          {{ t("studio.module.preview.noParams") }}
        </div>
        <div v-else class="flex flex-col gap-2 pointer-events-none opacity-90">
          <SchemaFormField v-for="p in schemaParams" :key="p.name" :param="p" :model-value="undefined" @change="noop" />
        </div>
        <div v-for="w in schemaWarnings" :key="w.code" class="text-[11px] text-warm-500 italic flex items-center gap-1">
          <div class="i-carbon-information text-xs" />
          {{ w.message }}
        </div>
      </section>

      <!-- Used-in list -->
      <section class="flex flex-col gap-1.5">
        <div class="text-[11px] uppercase tracking-wider text-warm-500 font-medium">
          {{ t("studio.module.preview.usedInTitle") }}
        </div>
        <UsedInCreaturesList :kind="kind" :name="name" :refresh-key="refreshKey" @open="$emit('open-creature', $event)" @count-change="onUsedCount" />
      </section>
    </div>
  </div>
</template>

<script setup>
import { computed, ref, watch } from "vue"

import SchemaFormField from "@/components/studio/creature/SchemaFormField.vue"
import UsedInCreaturesList from "@/components/studio/module/UsedInCreaturesList.vue"
import { schemaAPI } from "@/utils/studio/api"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()

const props = defineProps({
  kind: { type: String, required: true },
  name: { type: String, required: true },
  /** Optional: the module's current workspace-relative path
   *  (modules/tools/foo.py). Drives the schema resolver. */
  path: { type: String, default: "" },
  /** Optional: the identity field the user currently types in the
   *  form. If it differs from `name`, we surface a rename warning. */
  currentIdentity: { type: String, default: "" },
  /** Bump after saves so we re-fetch schema + used-in. */
  refreshKey: { type: Number, default: 0 },
})

defineEmits(["open-creature"])

const schemaParams = ref([])
const schemaWarnings = ref([])
const schemaError = ref("")
const schemaLoading = ref(false)
const usedCount = ref(0)

const renameWarning = computed(() => {
  if (!props.currentIdentity) return false
  if (!props.name) return false
  if (props.currentIdentity === props.name) return false
  return usedCount.value > 0
})

async function refreshSchema() {
  schemaLoading.value = true
  schemaError.value = ""
  try {
    // Workspace-authored (or manifest-editable) modules resolve as
    // type:"custom" with the workspace-relative .py path. The
    // introspect backend accepts file paths directly.
    const res = await schemaAPI.moduleSchema({
      kind: props.kind,
      name: props.name,
      type: "custom",
      module: props.path || "",
      class_name: null,
    })
    schemaParams.value = res?.params || []
    schemaWarnings.value = res?.warnings || []
  } catch (e) {
    schemaError.value = e?.message || String(e)
    schemaParams.value = []
    schemaWarnings.value = []
  } finally {
    schemaLoading.value = false
  }
}

function onUsedCount(n) {
  usedCount.value = n
}

function noop() {}

watch(
  () => [props.kind, props.name, props.path, props.refreshKey],
  () => {
    if (props.name && props.kind) refreshSchema()
  },
  { immediate: true },
)
</script>
