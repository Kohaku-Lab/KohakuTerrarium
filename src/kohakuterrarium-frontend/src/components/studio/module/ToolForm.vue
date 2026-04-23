<template>
  <div class="h-full overflow-auto px-4 py-4 flex flex-col gap-4">
    <!-- Identity -->
    <section class="flex flex-col gap-3">
      <h3 class="text-xs font-semibold uppercase tracking-wider text-warm-500">
        {{ t("studio.module.form.identity") }}
      </h3>
      <div class="grid grid-cols-2 gap-3">
        <KField :label="t('studio.module.form.toolName')" :hint="t('studio.module.form.toolNameHint')" required>
          <KInput :model-value="form.tool_name || ''" placeholder="my_tool" @update:model-value="patch('tool_name', $event)" />
        </KField>
        <KField :label="t('studio.module.form.className')" :hint="t('studio.module.form.classNameHint')">
          <KInput :model-value="form.class_name || ''" placeholder="MyTool" @update:model-value="patch('class_name', $event)" />
        </KField>
      </div>
      <KField :label="t('studio.module.form.description')" :hint="t('studio.module.form.descriptionHint')">
        <KInput :model-value="form.description || ''" :placeholder="t('studio.module.form.descriptionPlaceholder')" @update:model-value="patch('description', $event)" />
      </KField>
    </section>

    <!-- Skill documentation (sidecar .md — opens in a middle-col tab). -->
    <SkillDocSection v-if="kind && name" :kind="kind" :name="name" :refresh-key="docRefreshKey" @edit="$emit('open-doc')" />

    <!-- Behavior -->
    <section class="flex flex-col gap-3">
      <h3 class="text-xs font-semibold uppercase tracking-wider text-warm-500">
        {{ t("studio.module.form.behavior") }}
      </h3>
      <div class="grid grid-cols-2 gap-3">
        <KField :label="t('studio.module.form.executionMode')" :hint="t('studio.module.form.executionModeHint')">
          <KSelect :model-value="form.execution_mode || 'direct'" :options="EXEC_MODE_OPTIONS" @update:model-value="patch('execution_mode', $event)" />
        </KField>
        <KField :label="t('studio.module.form.flags')">
          <div class="flex flex-col gap-1 pt-1">
            <KCheckbox :model-value="!!form.needs_context" :label="t('studio.module.form.needsContext')" @update:model-value="patch('needs_context', $event)" />
            <KCheckbox :model-value="!!form.require_manual_read" :label="t('studio.module.form.requireManualRead')" @update:model-value="patch('require_manual_read', $event)" />
          </div>
        </KField>
      </div>
    </section>

    <!-- Parameters -->
    <section class="flex flex-col gap-3">
      <div>
        <h3 class="text-xs font-semibold uppercase tracking-wider text-warm-500">
          {{ t("studio.module.form.params") }}
        </h3>
        <p class="text-[11px] text-warm-500 mt-0.5">{{ t("studio.module.form.paramsHint") }}</p>
      </div>
      <ParamsTable :params="form.params || []" @update:params="patch('params', $event)" />
    </section>

    <!-- Execute body -->
    <section class="flex flex-col gap-2">
      <div>
        <h3 class="text-xs font-semibold uppercase tracking-wider text-warm-500">
          {{ t("studio.module.form.executeBody") }}
        </h3>
        <p class="text-[11px] text-warm-500 mt-0.5">{{ t("studio.module.form.executeBodyHint") }}</p>
      </div>
      <ExecuteBodyEditor :model-value="executeBody" method-name="_execute" method-signature="self, args" height="300px" @update:model-value="$emit('execute-body-change', $event)" @save="$emit('save')" />
    </section>

    <!-- Wiring preview -->
    <section class="flex flex-col gap-2">
      <h3 class="text-xs font-semibold uppercase tracking-wider text-warm-500">
        {{ t("studio.module.form.wiring") }}
      </h3>
      <WiringPreview kind="tools" :tool-name="form.tool_name" :params="form.params || []" />
    </section>
  </div>
</template>

<script setup>
import KCheckbox from "@/components/studio/common/KCheckbox.vue"
import KField from "@/components/studio/common/KField.vue"
import KInput from "@/components/studio/common/KInput.vue"
import KSelect from "@/components/studio/common/KSelect.vue"
import ExecuteBodyEditor from "@/components/studio/module/ExecuteBodyEditor.vue"
import ParamsTable from "@/components/studio/module/ParamsTable.vue"
import SkillDocSection from "@/components/studio/module/SkillDocSection.vue"
import WiringPreview from "@/components/studio/module/WiringPreview.vue"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()

const EXEC_MODE_OPTIONS = [
  { value: "direct", label: "direct" },
  { value: "background", label: "background" },
  { value: "stateful", label: "stateful" },
]

const props = defineProps({
  kind: { type: String, default: "tools" },
  name: { type: String, default: "" },
  form: { type: Object, default: () => ({}) },
  executeBody: { type: String, default: "" },
  /** Parent bumps this after the doc tab saves so the preview refreshes. */
  docRefreshKey: { type: Number, default: 0 },
})

const emit = defineEmits(["patch", "execute-body-change", "save", "open-doc"])

function patch(path, value) {
  emit("patch", { path, value })
}
</script>
