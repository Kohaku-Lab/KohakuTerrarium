<template>
  <div class="h-full overflow-auto px-4 py-4 flex flex-col gap-4">
    <!-- Identity -->
    <section class="flex flex-col gap-3">
      <h3 class="text-xs font-semibold uppercase tracking-wider text-warm-500">
        {{ t("studio.module.form.identity") }}
      </h3>
      <div class="grid grid-cols-2 gap-3">
        <KField :label="t('studio.module.form.subagentName')" :hint="t('studio.module.form.subagentNameHint')" required>
          <KInput :model-value="form.name || ''" placeholder="my_subagent" @update:model-value="patch('name', $event)" />
        </KField>
      </div>
      <KField :label="t('studio.module.form.description')" :hint="t('studio.module.form.descriptionHint')">
        <KInput :model-value="form.description || ''" :placeholder="t('studio.module.form.descriptionPlaceholder')" @update:model-value="patch('description', $event)" />
      </KField>
    </section>

    <!-- Skill doc (sidecar .md — subagents honor ##info##). -->
    <SkillDocSection v-if="name" :kind="kind" :name="name" :refresh-key="docRefreshKey" @edit="$emit('open-doc')" />

    <!-- Behavior -->
    <section class="flex flex-col gap-3">
      <h3 class="text-xs font-semibold uppercase tracking-wider text-warm-500">
        {{ t("studio.module.form.behavior") }}
      </h3>
      <KField :label="t('studio.module.form.subagentFlags')">
        <div class="flex flex-col gap-1 pt-1">
          <KCheckbox :model-value="!!form.stateless" :label="t('studio.module.form.stateless')" @update:model-value="patch('stateless', $event)" />
          <KCheckbox :model-value="!!form.interactive" :label="t('studio.module.form.interactive')" @update:model-value="patch('interactive', $event)" />
          <KCheckbox :model-value="!!form.can_modify" :label="t('studio.module.form.canModify')" @update:model-value="patch('can_modify', $event)" />
        </div>
      </KField>
    </section>

    <!-- Tools granted -->
    <section class="flex flex-col gap-2">
      <div>
        <h3 class="text-xs font-semibold uppercase tracking-wider text-warm-500">
          {{ t("studio.module.form.subagentTools") }}
        </h3>
        <p class="text-[11px] text-warm-500 mt-0.5">{{ t("studio.module.form.subagentToolsHint") }}</p>
      </div>
      <ToolsMultiSelect :model-value="form.tools || []" @update:model-value="patch('tools', $event)" />
    </section>

    <!-- System prompt — inline markdown editor. The prompt text lives
         inside the SubAgentConfig call (not a sidecar file), so it's
         part of the form, not a tab. -->
    <section class="flex flex-col gap-2">
      <div>
        <h3 class="text-xs font-semibold uppercase tracking-wider text-warm-500">
          {{ t("studio.module.form.systemPrompt") }}
        </h3>
        <p class="text-[11px] text-warm-500 mt-0.5">{{ t("studio.module.form.systemPromptHint") }}</p>
      </div>
      <div class="h-[260px] rounded-md border border-warm-200 dark:border-warm-800 overflow-hidden">
        <MonacoEditor language="markdown" :model-value="form.system_prompt || ''" @update:model-value="patch('system_prompt', $event)" @save="$emit('save')" />
      </div>
    </section>

    <!-- Wiring preview -->
    <section class="flex flex-col gap-2">
      <h3 class="text-xs font-semibold uppercase tracking-wider text-warm-500">
        {{ t("studio.module.form.wiring") }}
      </h3>
      <WiringPreview kind="subagents" :tool-name="form.name" :params="[]" />
    </section>
  </div>
</template>

<script setup>
import KCheckbox from "@/components/studio/common/KCheckbox.vue"
import KField from "@/components/studio/common/KField.vue"
import KInput from "@/components/studio/common/KInput.vue"
import MonacoEditor from "@/components/studio/code/MonacoEditor.vue"
import SkillDocSection from "@/components/studio/module/SkillDocSection.vue"
import ToolsMultiSelect from "@/components/studio/module/ToolsMultiSelect.vue"
import WiringPreview from "@/components/studio/module/WiringPreview.vue"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()

defineProps({
  kind: { type: String, default: "subagents" },
  name: { type: String, default: "" },
  form: { type: Object, default: () => ({}) },
  executeBody: { type: String, default: "" },
  docRefreshKey: { type: Number, default: 0 },
})

const emit = defineEmits(["patch", "execute-body-change", "save", "open-doc"])

function patch(path, value) {
  emit("patch", { path, value })
}
</script>
