<template>
  <el-dialog v-model="localOpen" :title="t('studio.newModule.title')" width="520px" :close-on-click-modal="!submitting" :close-on-press-escape="!submitting">
    <form class="flex flex-col gap-3" @submit.prevent="onSubmit">
      <KField :label="t('studio.newModule.kind')">
        <KSelect v-model="form.kind" :options="kindOptions" :disabled="submitting" />
      </KField>

      <KField :label="t('studio.newModule.name')" :hint="t('studio.newModule.nameHint')" :error="nameError" required>
        <KInput ref="nameInputRef" v-model="form.name" :placeholder="t('studio.newModule.namePlaceholder')" :disabled="submitting" />
      </KField>

      <div class="text-[11px] text-warm-500 dark:text-warm-500 px-1">
        {{ t("studio.newModule.editorNote") }}
      </div>

      <div v-if="serverError" class="px-3 py-2 rounded bg-coral/10 text-coral text-xs border border-coral/20">
        {{ serverError }}
      </div>
    </form>

    <template #footer>
      <div class="flex justify-end gap-2">
        <KButton variant="secondary" :disabled="submitting" @click="close">
          {{ t("studio.common.cancel") }}
        </KButton>
        <KButton variant="primary" :icon="submitting ? 'i-carbon-circle-dash animate-spin' : 'i-carbon-add'" :disabled="submitting || !form.name.trim()" @click="onSubmit">
          {{ submitting ? t("studio.newModule.creating") : t("studio.newModule.create") }}
        </KButton>
      </div>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, nextTick, ref, watch } from "vue"
import { ElDialog } from "element-plus"

import KButton from "@/components/studio/common/KButton.vue"
import KField from "@/components/studio/common/KField.vue"
import KInput from "@/components/studio/common/KInput.vue"
import KSelect from "@/components/studio/common/KSelect.vue"
import { moduleAPI } from "@/utils/studio/api"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  /** Existing module names keyed by kind, for dedupe check. */
  existingByKind: { type: Object, default: () => ({}) },
})

const emit = defineEmits(["update:modelValue", "created"])

const localOpen = ref(props.modelValue)
const form = ref({ kind: "tools", name: "" })
const submitting = ref(false)
const serverError = ref("")
const nameInputRef = ref(null)

const kindOptions = computed(() => [
  { value: "tools", label: t("studio.module.kinds.tools") },
  { value: "subagents", label: t("studio.module.kinds.subagents") },
  { value: "triggers", label: t("studio.module.kinds.triggers") },
  { value: "plugins", label: t("studio.module.kinds.plugins") },
  { value: "inputs", label: t("studio.module.kinds.inputs") },
  { value: "outputs", label: t("studio.module.kinds.outputs") },
])

const NAME_INVALID = /[\s/\\]|^\./

const nameError = computed(() => {
  const v = form.value.name.trim()
  if (!v) return ""
  if (NAME_INVALID.test(v)) return t("studio.newModule.nameInvalid")
  const existing = props.existingByKind?.[form.value.kind] || []
  if (existing.some((m) => m.name === v)) return t("studio.newModule.nameExists")
  return ""
})

watch(
  () => props.modelValue,
  async (v) => {
    localOpen.value = v
    if (v) {
      form.value = { kind: "tools", name: "" }
      serverError.value = ""
      await nextTick()
      nameInputRef.value?.$el?.querySelector?.("input")?.focus?.()
    }
  },
)

watch(localOpen, (v) => {
  if (v !== props.modelValue) emit("update:modelValue", v)
})

async function onSubmit() {
  if (submitting.value) return
  const name = form.value.name.trim()
  if (!name || nameError.value) return

  submitting.value = true
  serverError.value = ""
  try {
    const created = await moduleAPI.scaffold(form.value.kind, {
      name,
      template: null,
    })
    emit("created", { kind: form.value.kind, name, detail: created })
    close()
  } catch (err) {
    if (err?.code === "name_exists") {
      serverError.value = t("studio.newModule.nameExists")
    } else if (err?.code === "invalid_name") {
      serverError.value = t("studio.newModule.nameInvalid")
    } else {
      serverError.value = err?.message || String(err)
    }
  } finally {
    submitting.value = false
  }
}

function close() {
  if (submitting.value) return
  localOpen.value = false
}
</script>
