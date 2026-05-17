<template>
  <el-dialog v-model="visible" :title="t('registry.installFromUrl')" width="520px">
    <div class="space-y-3 text-[13px]">
      <div>
        <label class="block text-[11px] text-warm-400 mb-1">{{ t("registry.urlLabel") }}</label>
        <el-input v-model="url" size="small" placeholder="https://github.com/owner/repo.git" @keyup.enter="onInstall" />
        <p class="text-[11px] text-warm-400 mt-1">{{ t("registry.urlHint") }}</p>
      </div>
      <div>
        <label class="block text-[11px] text-warm-400 mb-1">{{ t("registry.nameOverride") }}</label>
        <el-input v-model="name" size="small" :placeholder="t('registry.nameOverridePlaceholder')" />
      </div>
      <p v-if="error" class="text-[12px] text-red-500">{{ error }}</p>
    </div>
    <template #footer>
      <el-button @click="visible = false">{{ t("common.cancel") }}</el-button>
      <el-button type="primary" :loading="installing" :disabled="!url.trim()" @click="onInstall">
        {{ t("registry.install") }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, ref, watch } from "vue"

import { registryAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"

const props = defineProps({ modelValue: { type: Boolean, default: false } })
const emit = defineEmits(["update:modelValue", "installed"])
const { t } = useI18n()

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit("update:modelValue", v),
})

const url = ref("")
const name = ref("")
const installing = ref(false)
const error = ref("")

watch(visible, (v) => {
  if (v) {
    url.value = ""
    name.value = ""
    error.value = ""
  }
})

async function onInstall() {
  if (!url.value.trim()) return
  installing.value = true
  error.value = ""
  try {
    const result = await registryAPI.install(url.value.trim(), name.value.trim() || null)
    emit("installed", result.name || name.value.trim() || url.value.trim())
    visible.value = false
  } catch (e) {
    error.value = e?.response?.data?.detail || e.message || "install failed"
  } finally {
    installing.value = false
  }
}
</script>
