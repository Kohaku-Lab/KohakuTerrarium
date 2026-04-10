<template>
  <el-dropdown
    trigger="click"
    @command="onPick"
    @visible-change="onVisibleChange"
  >
    <button
      class="flex items-center gap-1 px-1 py-0 rounded transition-colors hover:text-warm-700 dark:hover:text-warm-300"
      :disabled="!instanceId"
      :title="currentModel"
    >
      <span class="i-carbon-chip text-[11px]" />
      <span class="truncate max-w-40 font-mono">{{ currentModel || 'default' }}</span>
      <span class="i-carbon-chevron-down text-[9px] opacity-50" />
    </button>
    <template #dropdown>
      <el-dropdown-menu class="model-switcher-dropdown">
        <div
          v-if="loading"
          class="px-4 py-2 text-[11px] text-warm-400"
        >
          Loading…
        </div>
        <el-dropdown-item
          v-for="m in models"
          v-else
          :key="m.name"
          :command="m.name"
          :disabled="m.name === currentModel"
        >
          <div class="flex items-center gap-2">
            <span class="font-mono text-[11px]">{{ m.name }}</span>
            <span
              v-if="m.login_provider"
              class="text-[9px] text-warm-400"
            >{{ m.login_provider }}</span>
          </div>
        </el-dropdown-item>
        <div
          v-if="!loading && models.length === 0"
          class="px-4 py-2 text-[11px] text-warm-400"
        >
          No models available
        </div>
      </el-dropdown-menu>
    </template>
  </el-dropdown>
</template>

<script setup>
import { computed, ref } from "vue";
import { ElMessage } from "element-plus";

import { useChatStore } from "@/stores/chat";
import { useInstancesStore } from "@/stores/instances";
import { agentAPI, configAPI } from "@/utils/api";

const chat = useChatStore();
const instances = useInstancesStore();

const models = ref([]);
const loading = ref(false);

const instanceId = computed(() => instances.current?.id || null);
const currentModel = computed(
  () => chat.sessionInfo.model || instances.current?.model || "",
);

async function loadModels() {
  loading.value = true;
  try {
    const data = await configAPI.getModels();
    models.value = Array.isArray(data) ? data : [];
  } catch (err) {
    console.error("Failed to load models", err);
    models.value = [];
  } finally {
    loading.value = false;
  }
}

function onVisibleChange(open) {
  if (open && models.value.length === 0) loadModels();
}

async function onPick(modelName) {
  const id = instanceId.value;
  if (!id || !modelName || modelName === currentModel.value) return;
  try {
    await agentAPI.switchModel(id, modelName);
    chat.sessionInfo.model = modelName;
    ElMessage.success(`Switched to ${modelName}`);
  } catch (err) {
    ElMessage.error(`Model switch failed: ${err?.message || err}`);
  }
}
</script>

<style>
/* Constrain the model dropdown so it scrolls rather than pushing the layout */
.model-switcher-dropdown {
  max-height: 360px;
  overflow-y: auto;
}
</style>
