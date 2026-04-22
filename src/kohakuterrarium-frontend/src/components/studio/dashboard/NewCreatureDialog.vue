<template>
  <el-dialog v-model="localOpen" :title="t('studio.newCreature.title')" width="520px" :close-on-click-modal="!submitting" :close-on-press-escape="!submitting">
    <form class="flex flex-col gap-3" @submit.prevent="onSubmit">
      <KField :label="t('studio.newCreature.name')" :hint="t('studio.newCreature.nameHint')" :error="nameError" required>
        <KInput ref="nameInputRef" v-model="form.name" :placeholder="t('studio.newCreature.namePlaceholder')" :disabled="submitting" />
      </KField>

      <KField :label="t('studio.newCreature.baseConfig')" :hint="t('studio.newCreature.baseConfigHint')">
        <KInput v-model="form.base_config" :placeholder="baseConfigPlaceholder" :disabled="submitting" />
      </KField>

      <KField :label="t('studio.newCreature.description')">
        <KInput v-model="form.description" :placeholder="t('studio.newCreature.descriptionPlaceholder')" :disabled="submitting" />
      </KField>

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
          {{ submitting ? t("studio.newCreature.creating") : t("studio.newCreature.create") }}
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
import { creatureAPI, packagesAPI } from "@/utils/studio/api"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  /** Existing creature names in the workspace (for dedupe check). */
  existingNames: { type: Array, default: () => [] },
})

const emit = defineEmits(["update:modelValue", "created"])

const localOpen = ref(props.modelValue)
const form = ref({ name: "", base_config: "", description: "" })
const submitting = ref(false)
const serverError = ref("")
const nameInputRef = ref(null)
const firstPackage = ref(null)

// Name validation — mirrors backend sanitize_name rules.
const NAME_INVALID = /[\s/\\]|^\./
const nameError = computed(() => {
  const v = form.value.name.trim()
  if (!v) return ""
  if (NAME_INVALID.test(v)) return t("studio.newCreature.nameInvalid")
  if (props.existingNames.includes(v)) return t("studio.newCreature.nameExists")
  return ""
})

const baseConfigPlaceholder = computed(() => {
  const pkg = firstPackage.value
  if (pkg) return `@${pkg}/creatures/general`
  return t("studio.newCreature.baseConfigPlaceholder")
})

watch(
  () => props.modelValue,
  async (v) => {
    localOpen.value = v
    if (v) {
      form.value = { name: "", base_config: "", description: "" }
      serverError.value = ""
      // Preload package names for placeholder hint
      packagesAPI
        .list()
        .then((pkgs) => {
          if (pkgs?.length) firstPackage.value = pkgs[0].name
        })
        .catch(() => {})
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
    const body = {
      name,
      base_config: form.value.base_config.trim() || null,
      description: form.value.description.trim() || "",
    }
    const created = await creatureAPI.scaffold(body)
    emit("created", created)
    close()
  } catch (err) {
    // Backend returns { code, message } via the axios wrapper
    if (err?.code === "name_exists") {
      serverError.value = t("studio.newCreature.nameExists")
    } else if (err?.code === "invalid_name") {
      serverError.value = t("studio.newCreature.nameInvalid")
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
