<template>
  <div class="grid grid-cols-[1fr_1fr] gap-3">
    <div>
      <label class="text-[11px] text-warm-400 mb-1 block">{{ t("settings.backends.name") }}</label>
      <el-input :model-value="form.name" size="small" placeholder="my-provider" :disabled="isEditing" @update:model-value="updateField('name', $event)" />
    </div>
    <div>
      <label class="text-[11px] text-warm-400 mb-1 block">{{ t("settings.backends.backendType") }}</label>
      <el-select :model-value="form.backend_type" size="small" class="w-full" @update:model-value="updateField('backend_type', $event)">
        <el-option value="openai" label="openai" />
        <el-option value="codex" label="codex" />
        <el-option value="anthropic" label="anthropic" />
      </el-select>
    </div>
    <div class="col-span-2">
      <label class="text-[11px] text-warm-400 mb-1 block">{{ t("settings.backends.baseUrl") }}</label>
      <el-input :model-value="form.base_url" size="small" placeholder="https://api.example.com/v1" @update:model-value="updateField('base_url', $event)" />
    </div>
    <div class="col-span-2">
      <label class="text-[11px] text-warm-400 mb-1 block">{{ t("settings.backends.providerName") }}</label>
      <el-input :model-value="form.provider_name" size="small" :placeholder="form.name || 'my-provider'" @update:model-value="updateField('provider_name', $event)" />
      <p class="text-[10px] text-warm-400 mt-1">
        {{ t("settings.backends.providerNameHint") }}
      </p>
    </div>
    <div v-if="nativeToolCatalog.length" class="col-span-2">
      <label class="text-[11px] text-warm-400 mb-1 block">{{ t("settings.backends.nativeTools") }}</label>
      <el-checkbox-group :model-value="form.provider_native_tools" class="flex flex-col gap-1" @update:model-value="updateField('provider_native_tools', $event)">
        <el-checkbox v-for="tool in nativeToolCatalog" :key="tool.name" :value="tool.name" class="!mr-0">
          <span class="font-mono text-[12px]">{{ tool.name }}</span>
          <span class="text-[10px] text-warm-400 ml-1"> ({{ tool.provider_support.length ? tool.provider_support.join(", ") : "any" }}) </span>
          <span v-if="tool.description" class="text-[10px] text-warm-400 ml-1 truncate">— {{ tool.description }}</span>
        </el-checkbox>
      </el-checkbox-group>
      <p class="text-[10px] text-warm-400 mt-1">
        {{ t("settings.backends.nativeToolsHint") }}
      </p>
    </div>
    <div class="col-span-2 flex justify-end gap-2">
      <el-button size="small" @click="$emit('cancel')">
        {{ t("common.cancel") }}
      </el-button>
      <el-button type="primary" size="small" :disabled="!form.name || !form.backend_type" @click="$emit('save')">
        {{ t("common.save") }}
      </el-button>
    </div>
  </div>
</template>

<script setup>
import { useI18n } from "@/utils/i18n"

defineProps({
  form: { type: Object, required: true },
  nativeToolCatalog: { type: Array, default: () => [] },
  isEditing: { type: Boolean, default: false },
})

const emit = defineEmits(["save", "cancel", "update-field"])

const { t } = useI18n()

function updateField(key, value) {
  emit("update-field", { key, value })
}
</script>
