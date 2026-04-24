<template>
  <div class="h-full overflow-auto px-4 py-4 flex flex-col gap-4">
    <!-- Identity -->
    <section class="flex flex-col gap-3">
      <h3 class="text-xs font-semibold uppercase tracking-wider text-warm-500">
        {{ t("studio.module.form.identity") }}
      </h3>
      <div class="grid grid-cols-2 gap-3">
        <KField :label="t('studio.module.form.pluginName')" :hint="t('studio.module.form.pluginNameHint')" required>
          <KInput :model-value="form.name || ''" placeholder="my_plugin" @update:model-value="patch('name', $event)" />
        </KField>
        <KField :label="t('studio.module.form.className')" :hint="t('studio.module.form.classNameHint')">
          <KInput :model-value="form.class_name || ''" placeholder="MyPlugin" @update:model-value="patch('class_name', $event)" />
        </KField>
      </div>
      <KField :label="t('studio.module.form.description')" :hint="t('studio.module.form.descriptionHint')">
        <KInput :model-value="form.description || ''" :placeholder="t('studio.module.form.descriptionPlaceholder')" @update:model-value="patch('description', $event)" />
      </KField>
      <KField :label="t('studio.module.form.priority')" :hint="t('studio.module.form.priorityHint')">
        <KInput type="number" :model-value="form.priority ?? 50" @update:model-value="patch('priority', clampInt($event, 0, 100))" />
      </KField>
    </section>

    <!-- Options schema — describes the keys a creature may pass under
         `options:`. Persisted as a sidecar JSON file next to the .py
         so the creature pool can render per-key forms. -->
    <section class="flex flex-col gap-2">
      <div>
        <h3 class="text-xs font-semibold uppercase tracking-wider text-warm-500">
          {{ t("studio.module.form.optionsSchema") }}
        </h3>
        <p class="text-[11px] text-warm-500 mt-0.5">{{ t("studio.module.form.optionsSchemaHint") }}</p>
      </div>
      <OptionsSchemaEditor :model-value="form.options_schema || []" @update:model-value="patch('options_schema', $event)" />
    </section>

    <!-- Hook checklist -->
    <section class="flex flex-col gap-2">
      <div>
        <h3 class="text-xs font-semibold uppercase tracking-wider text-warm-500">
          {{ t("studio.module.form.hooks") }}
        </h3>
        <p class="text-[11px] text-warm-500 mt-0.5">{{ t("studio.module.form.hooksHint") }}</p>
      </div>
      <HookChecklist :model-value="form.enabled_hooks || []" @update:model-value="patch('enabled_hooks', $event)" />
    </section>

    <!-- One body editor per enabled hook -->
    <section v-if="(form.enabled_hooks || []).length" class="flex flex-col gap-3">
      <h3 class="text-xs font-semibold uppercase tracking-wider text-warm-500">
        {{ t("studio.module.form.hookBodies") }}
      </h3>
      <div v-for="(hook, idx) in form.enabled_hooks" :key="hook.name" class="flex flex-col gap-1">
        <div class="flex items-center gap-2">
          <span class="text-[11px] font-mono text-warm-700 dark:text-warm-300">
            {{ hook.name }}
          </span>
          <span v-if="hookSig(hook.name)" class="text-[11px] text-warm-500">
            {{ hookSig(hook.name) }}
          </span>
        </div>
        <ExecuteBodyEditor :model-value="hook.body || ''" :method-name="hook.name" :method-signature="hookSignatureFor(hook.name)" height="180px" @update:model-value="updateHookBody(idx, $event)" @save="$emit('save')" />
      </div>
    </section>

    <!-- Wiring preview -->
    <section class="flex flex-col gap-2">
      <h3 class="text-xs font-semibold uppercase tracking-wider text-warm-500">
        {{ t("studio.module.form.wiring") }}
      </h3>
      <WiringPreview kind="plugins" :tool-name="form.name" :params="[]" />
    </section>
  </div>
</template>

<script setup>
import { computed } from "vue"

import KField from "@/components/studio/common/KField.vue"
import KInput from "@/components/studio/common/KInput.vue"
import ExecuteBodyEditor from "@/components/studio/module/ExecuteBodyEditor.vue"
import HookChecklist from "@/components/studio/module/HookChecklist.vue"
import OptionsSchemaEditor from "@/components/studio/module/OptionsSchemaEditor.vue"
import WiringPreview from "@/components/studio/module/WiringPreview.vue"
import { useStudioCatalogStore } from "@/stores/studio/catalog"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()

const props = defineProps({
  kind: { type: String, default: "plugins" },
  name: { type: String, default: "" },
  form: { type: Object, default: () => ({}) },
  executeBody: { type: String, default: "" },
})

const emit = defineEmits(["patch", "execute-body-change", "save"])

const catalog = useStudioCatalogStore()

const hookSpecs = computed(() => {
  const m = new Map()
  for (const h of catalog.pluginHooks || []) m.set(h.name, h)
  return m
})

function hookSig(name) {
  return hookSpecs.value.get(name)?.args_signature || ""
}

function hookSignatureFor(name) {
  const sig = hookSig(name).trim().replace(/^,\s*/, "")
  return sig ? `self, ${sig}` : "self"
}

function patch(path, value) {
  emit("patch", { path, value })
}

function updateHookBody(idx, body) {
  const next = (props.form.enabled_hooks || []).map((h, i) => (i === idx ? { ...h, body } : h))
  patch("enabled_hooks", next)
}

function clampInt(raw, lo, hi) {
  const n = Number(raw)
  if (!Number.isFinite(n)) return lo
  return Math.min(hi, Math.max(lo, Math.trunc(n)))
}
</script>
