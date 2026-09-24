<template>
  <ProviderRow :name="displayName" :identifier="backend.name" :status="t(backend.has_key ? 'settings.oauth.configured' : 'settings.providers.unconfigured')" :tone="backend.has_key ? 'success' : 'info'" :subtitle="backend.base_url">
    <details v-if="backend.env_var || backend.backend_type || backend.masked_key" class="provider-connection-details">
      <summary>{{ t("settings.providers.connectionDetails") }}</summary>
      <div class="mt-2 space-y-1 font-mono break-all">
        <p>{{ backend.backend_type }}</p>
        <p v-if="backend.env_var">{{ backend.env_var }}</p>
        <p v-if="backend.masked_key">{{ backend.masked_key }}</p>
        <p v-if="backend.provider_name">{{ backend.provider_name }}</p>
        <p v-if="backend.provider_native_tools?.length">{{ backend.provider_native_tools.join(", ") }}</p>
      </div>
    </details>
    <template #actions>
      <template v-if="editing">
        <el-input :model-value="modelValue" size="small" type="password" show-password :placeholder="t('settings.keys.enterKey')" :aria-label="t('settings.keys.enterKey')" class="provider-key-input" @update:model-value="$emit('update:modelValue', $event)" @keyup.enter="$emit('save')" />
        <el-button size="small" type="primary" @click="$emit('save')">{{ t("common.save") }}</el-button>
        <el-button size="small" @click="$emit('cancel')">{{ t("common.cancel") }}</el-button>
      </template>
      <el-button v-else size="small" :type="backend.has_key ? 'default' : 'primary'" @click="$emit('edit')">{{ t(backend.has_key ? "settings.providers.changeKey" : "settings.keys.setKey") }}</el-button>
      <el-dropdown v-if="!editing && backend.has_key" trigger="click" @command="confirmRemove">
        <el-button size="small" :aria-label="t('settings.providers.more')">···</el-button>
        <template #dropdown
          ><el-dropdown-menu
            ><el-dropdown-item command="delete">{{ t("settings.keys.delete") }}</el-dropdown-item></el-dropdown-menu
          ></template
        >
      </el-dropdown>
      <slot name="actions" />
    </template>
  </ProviderRow>
</template>
<script setup>
import { computed } from "vue"
import { ElMessageBox } from "element-plus"
import { useI18n } from "@/utils/i18n"
import ProviderRow from "./ProviderRow.vue"
const props = defineProps({ backend: { type: Object, required: true }, editing: Boolean, modelValue: { type: String, default: "" } })
const emit = defineEmits(["update:modelValue", "edit", "save", "cancel", "delete"])
const { t } = useI18n()
const displayName = computed(() => (props.backend.built_in ? { openai: "OpenAI", openrouter: "OpenRouter", anthropic: "Anthropic", gemini: "Gemini", mimo: "MiMo", "kimi-code": "Kimi Code", "glm-coding": "GLM Coding" }[props.backend.name] || props.backend.name : props.backend.name))
async function confirmRemove() {
  try {
    await ElMessageBox.confirm(t("settings.keys.deleteConfirm", { provider: props.backend.name }), { type: "warning", confirmButtonText: t("common.delete"), cancelButtonText: t("common.cancel") })
  } catch {
    return
  }
  emit("delete")
}
</script>
<style scoped>
.provider-connection-details {
  margin-top: 0.4rem;
  font-size: 11px;
  color: var(--el-text-color-secondary);
}
.provider-connection-details summary {
  cursor: pointer;
  width: fit-content;
}
.provider-key-input {
  flex-basis: 100%;
  width: 220px;
  max-width: 100%;
}
</style>
