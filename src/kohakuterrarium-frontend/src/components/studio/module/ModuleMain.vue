<template>
  <div class="h-full flex flex-col overflow-hidden">
    <!-- Mode + warnings strip -->
    <div class="shrink-0 flex items-center gap-2 px-3 py-2 border-b border-warm-200 dark:border-warm-800 bg-warm-50/60 dark:bg-warm-950/60">
      <ModeToggle :model-value="mode" @update:model-value="$emit('mode-change', $event)" />
      <span v-if="readOnlyBannerActive" class="text-[11px] font-medium px-1.5 py-0.5 rounded bg-amber/20 text-amber-shadow dark:text-amber">
        {{ t("studio.module.raw.auto") }}
      </span>
      <div class="flex-1" />
      <span v-if="warnings.length" :title="warningsTitle" class="text-[11px] text-amber-shadow dark:text-amber flex items-center gap-1">
        <div class="i-carbon-warning-alt text-sm" />
        {{ t("studio.module.warnings", { n: warnings.length }) }}
      </span>
    </div>

    <!-- Round-trip error banner -->
    <RawModeBanner v-if="roundTripError" :title="t('studio.module.raw.roundtripFailed')" :message="roundTripError" :retry="true" @retry="$emit('retry-simple')" />

    <!-- Per-kind form body -->
    <div class="flex-1 min-h-0 overflow-auto">
      <div v-if="mode === 'raw'" class="h-full">
        <MonacoEditor language="python" :model-value="rawSource" @update:model-value="$emit('raw-change', $event)" @save="$emit('save')" />
      </div>
      <div v-else class="h-full">
        <ToolForm v-if="kind === 'tools'" :kind="kind" :name="name" :form="form" :execute-body="executeBody" :doc-refresh-key="docRefreshKey" @patch="$emit('patch-form', $event.path, $event.value)" @execute-body-change="$emit('execute-body-change', $event)" @save="$emit('save')" @open-doc="$emit('open-doc')" />
        <SubagentForm v-else-if="kind === 'subagents'" :kind="kind" :name="name" :form="form" :execute-body="executeBody" :doc-refresh-key="docRefreshKey" @patch="$emit('patch-form', $event.path, $event.value)" @execute-body-change="$emit('execute-body-change', $event)" @save="$emit('save')" @open-doc="$emit('open-doc')" />
        <TriggerForm v-else-if="kind === 'triggers'" :kind="kind" :name="name" :form="form" :execute-body="executeBody" @patch="$emit('patch-form', $event.path, $event.value)" @execute-body-change="$emit('execute-body-change', $event)" @save="$emit('save')" />
        <PluginForm v-else-if="kind === 'plugins'" :kind="kind" :name="name" :form="form" :execute-body="executeBody" @patch="$emit('patch-form', $event.path, $event.value)" @execute-body-change="$emit('execute-body-change', $event)" @save="$emit('save')" />
        <IoForm v-else-if="kind === 'inputs' || kind === 'outputs'" :kind="kind" :name="name" :form="form" :execute-body="executeBody" @patch="$emit('patch-form', $event.path, $event.value)" @execute-body-change="$emit('execute-body-change', $event)" @save="$emit('save')" />
        <div v-else class="h-full flex flex-col items-center justify-center gap-2 text-warm-500 px-6 text-center">
          <div class="i-carbon-code text-3xl text-warm-400" />
          <div class="text-sm">
            {{ t("studio.module.simpleNotAvailable", { kind }) }}
          </div>
          <div class="text-xs opacity-70">
            {{ t("studio.module.simpleNotAvailableHint") }}
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue"

import MonacoEditor from "@/components/studio/code/MonacoEditor.vue"
import RawModeBanner from "@/components/studio/code/RawModeBanner.vue"
import IoForm from "@/components/studio/module/IoForm.vue"
import ModeToggle from "@/components/studio/module/ModeToggle.vue"
import PluginForm from "@/components/studio/module/PluginForm.vue"
import SubagentForm from "@/components/studio/module/SubagentForm.vue"
import ToolForm from "@/components/studio/module/ToolForm.vue"
import TriggerForm from "@/components/studio/module/TriggerForm.vue"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()

const props = defineProps({
  kind: { type: String, required: true },
  name: { type: String, default: "" },
  mode: { type: String, default: "simple" },
  form: { type: Object, default: () => ({}) },
  executeBody: { type: String, default: "" },
  rawSource: { type: String, default: "" },
  warnings: { type: Array, default: () => [] },
  roundTripError: { type: String, default: "" },
  docRefreshKey: { type: Number, default: 0 },
})

defineEmits(["mode-change", "raw-change", "patch-form", "execute-body-change", "retry-simple", "save", "open-doc"])

const readOnlyBannerActive = computed(() => props.mode === "raw" && props.warnings.some((w) => w.code === "ast_roundtrip_unsafe"))

const warningsTitle = computed(() => props.warnings.map((w) => `${w.code}: ${w.message}`).join("\n"))
</script>
