<template>
  <details class="mx-3 mb-2 text-sm">
    <summary class="cursor-pointer text-warm-600 dark:text-warm-300">{{ t("discovery.region") }}</summary>
    <form class="flex items-end gap-2 flex-wrap mt-2" @submit.prevent="save">
      <label class="flex-1 min-w-32"
        ><span class="kt-text-caption">{{ t("discovery.region") }}</span>
        <el-select v-model="region" :aria-label="t('discovery.region')"><el-option v-for="id in regions" :key="id" :value="id" :label="id" /></el-select>
      </label>
      <label v-if="region !== 'us-east-1'" class="flex-1 min-w-32"
        ><span class="kt-text-caption">{{ t("discovery.workspace") }}</span>
        <el-input v-model.trim="workspace" placeholder="llm-…" :aria-label="t('discovery.workspace')" />
      </label>
      <el-button native-type="submit" :disabled="!valid || saving" size="small">{{ t("common.save") }}</el-button>
    </form>
  </details>
</template>
<script setup>
import { computed, ref, watch } from "vue"
import { ElMessage } from "element-plus"
import { settingsAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"
const props = defineProps({ backend: { type: Object, required: true } })
const emit = defineEmits(["saved"])
const { t } = useI18n()
const regions = ["cn-beijing", "ap-southeast-1", "cn-hongkong", "ap-northeast-1", "eu-central-1", "us-east-1"]
const region = ref(""),
  workspace = ref(""),
  saving = ref(false)
watch(
  () => props.backend.base_url,
  (url) => {
    const match = /^https:\/\/([a-z0-9-]+)\.([a-z0-9-]+)\.maas\.aliyuncs\.com\//.exec(url || "")
    region.value = match?.[2] || (url?.includes("dashscope-us.aliyuncs.com") ? "us-east-1" : "")
    workspace.value = match?.[1] || ""
  },
  { immediate: true },
)
const valid = computed(() => regions.includes(region.value) && (region.value === "us-east-1" || /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(workspace.value)))
async function save() {
  if (!valid.value || saving.value) return
  const host = region.value === "us-east-1" ? "dashscope-us.aliyuncs.com" : workspace.value + "." + region.value + ".maas.aliyuncs.com"
  saving.value = true
  try {
    const { name, backend_type, api_key_env, provider_name, provider_native_tools } = props.backend
    await settingsAPI.saveBackend({ name, backend_type, api_key_env, provider_name, provider_native_tools, base_url: "https://" + host + "/compatible-mode/v1" })
    emit("saved")
    ElMessage.success(t("discovery.regionSaved"))
  } catch (err) {
    ElMessage.error(err.response?.data?.detail || err.message)
  } finally {
    saving.value = false
  }
}
</script>
