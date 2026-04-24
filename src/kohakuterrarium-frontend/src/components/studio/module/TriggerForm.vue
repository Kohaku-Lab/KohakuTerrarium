<template>
  <div class="h-full overflow-auto px-4 py-4 flex flex-col gap-4">
    <!-- Identity -->
    <section class="flex flex-col gap-3">
      <h3 class="text-xs font-semibold uppercase tracking-wider text-warm-500">
        {{ t("studio.module.form.identity") }}
      </h3>
      <div class="grid grid-cols-2 gap-3">
        <KField :label="t('studio.module.form.triggerName')" :hint="t('studio.module.form.triggerNameHint')" required>
          <KInput :model-value="form.class_name || ''" placeholder="MyTrigger" @update:model-value="patch('class_name', $event)" />
        </KField>
      </div>
    </section>

    <!-- Universal / setup-tool metadata. When universal, the trigger
         is reachable via a creature's setup_tool_name (an add_* tool
         the controller calls at runtime). -->
    <section class="flex flex-col gap-3">
      <h3 class="text-xs font-semibold uppercase tracking-wider text-warm-500">
        {{ t("studio.module.form.triggerSetup") }}
      </h3>
      <KField :label="t('studio.module.form.universal')" :hint="t('studio.module.form.universalHint')">
        <KCheckbox :model-value="!!form.universal" :label="t('studio.module.form.universalLabel')" @update:model-value="patch('universal', $event)" />
      </KField>
      <div v-if="form.universal" class="grid grid-cols-2 gap-3">
        <KField :label="t('studio.module.form.setupToolName')" :hint="t('studio.module.form.setupToolNameHint')">
          <KInput :model-value="form.setup_tool_name || ''" placeholder="add_my_trigger" @update:model-value="patch('setup_tool_name', $event)" />
        </KField>
        <KField :label="t('studio.module.form.setupDescription')" :hint="t('studio.module.form.setupDescriptionHint')">
          <KInput :model-value="form.setup_description || ''" :placeholder="t('studio.module.form.setupDescriptionPlaceholder')" @update:model-value="patch('setup_description', $event)" />
        </KField>
      </div>
    </section>

    <!-- wait_for_trigger body -->
    <section class="flex flex-col gap-2">
      <div>
        <h3 class="text-xs font-semibold uppercase tracking-wider text-warm-500">
          {{ t("studio.module.form.triggerBody") }}
        </h3>
        <p class="text-[11px] text-warm-500 mt-0.5">{{ t("studio.module.form.triggerBodyHint") }}</p>
      </div>
      <ExecuteBodyEditor :model-value="executeBody" method-name="wait_for_trigger" method-signature="self" height="300px" @update:model-value="$emit('execute-body-change', $event)" @save="$emit('save')" />
    </section>

    <!-- Wiring preview -->
    <section class="flex flex-col gap-2">
      <h3 class="text-xs font-semibold uppercase tracking-wider text-warm-500">
        {{ t("studio.module.form.wiring") }}
      </h3>
      <WiringPreview kind="triggers" :tool-name="form.setup_tool_name || form.class_name" :params="[]" />
    </section>
  </div>
</template>

<script setup>
import KCheckbox from "@/components/studio/common/KCheckbox.vue"
import KField from "@/components/studio/common/KField.vue"
import KInput from "@/components/studio/common/KInput.vue"
import ExecuteBodyEditor from "@/components/studio/module/ExecuteBodyEditor.vue"
import WiringPreview from "@/components/studio/module/WiringPreview.vue"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()

defineProps({
  kind: { type: String, default: "triggers" },
  name: { type: String, default: "" },
  form: { type: Object, default: () => ({}) },
  executeBody: { type: String, default: "" },
})

const emit = defineEmits(["patch", "execute-body-change", "save"])

function patch(path, value) {
  emit("patch", { path, value })
}
</script>
