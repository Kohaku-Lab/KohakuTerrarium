<template>
  <div class="h-full overflow-auto px-4 py-4 flex flex-col gap-4">
    <!-- Identity -->
    <section class="flex flex-col gap-3">
      <h3 class="text-xs font-semibold uppercase tracking-wider text-warm-500">
        {{ t("studio.module.form.identity") }}
      </h3>
      <div class="grid grid-cols-2 gap-3">
        <KField :label="t('studio.module.form.className')" :hint="t('studio.module.form.ioClassNameHint')" required>
          <KInput :model-value="form.class_name || ''" :placeholder="kind === 'inputs' ? 'MyInput' : 'MyOutput'" @update:model-value="patch('class_name', $event)" />
        </KField>
      </div>
      <KField :label="t('studio.module.form.description')" :hint="t('studio.module.form.descriptionHint')">
        <KInput :model-value="form.description || ''" :placeholder="t('studio.module.form.descriptionPlaceholder')" @update:model-value="patch('description', $event)" />
      </KField>
    </section>

    <!-- Protocol method body -->
    <section class="flex flex-col gap-2">
      <div>
        <h3 class="text-xs font-semibold uppercase tracking-wider text-warm-500">
          {{ t("studio.module.form.ioBody", { method: methodName }) }}
        </h3>
        <p class="text-[11px] text-warm-500 mt-0.5">
          {{ t(kind === "inputs" ? "studio.module.form.ioInputHint" : "studio.module.form.ioOutputHint") }}
        </p>
      </div>
      <ExecuteBodyEditor :model-value="executeBody" :method-name="methodName" :method-signature="methodSignature" height="300px" @update:model-value="$emit('execute-body-change', $event)" @save="$emit('save')" />
    </section>

    <!-- Wiring preview -->
    <section class="flex flex-col gap-2">
      <h3 class="text-xs font-semibold uppercase tracking-wider text-warm-500">
        {{ t("studio.module.form.wiring") }}
      </h3>
      <WiringPreview :kind="kind" :tool-name="name" :params="[]" />
    </section>
  </div>
</template>

<script setup>
import { computed } from "vue"

import KField from "@/components/studio/common/KField.vue"
import KInput from "@/components/studio/common/KInput.vue"
import ExecuteBodyEditor from "@/components/studio/module/ExecuteBodyEditor.vue"
import WiringPreview from "@/components/studio/module/WiringPreview.vue"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()

const props = defineProps({
  /** "inputs" or "outputs" — same form, different protocol method. */
  kind: { type: String, required: true },
  name: { type: String, default: "" },
  form: { type: Object, default: () => ({}) },
  executeBody: { type: String, default: "" },
})

const emit = defineEmits(["patch", "execute-body-change", "save"])

// Framework canonical methods. ``form.method_name`` is set by
// parse_back when the file already uses a legacy name (read_input /
// write_output); otherwise fall back to the framework's abstract
// (get_input for inputs, write for outputs).
const methodName = computed(() => {
  const detected = props.form?.method_name
  if (detected) return detected
  return props.kind === "inputs" ? "get_input" : "write"
})
const methodSignature = computed(() => {
  if (props.kind === "inputs") return "self"
  if (methodName.value === "write_output") return "self, data"
  return "self, content: str"
})

function patch(path, value) {
  emit("patch", { path, value })
}
</script>
