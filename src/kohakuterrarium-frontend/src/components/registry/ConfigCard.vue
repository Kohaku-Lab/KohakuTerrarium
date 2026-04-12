<template>
  <div class="card-hover rounded-xl p-5 flex flex-col gap-3">
    <!-- Header: name + type badge -->
    <div class="flex items-center gap-2">
      <span class="font-semibold text-warm-800 dark:text-warm-200 truncate flex-1">
        {{ config.name }}
      </span>
      <GemBadge :gem="typeBadgeGem">
        {{ typeBadgeLabel }}
      </GemBadge>
      <GemBadge v-if="installed" gem="aquamarine"> Installed </GemBadge>
    </div>

    <!-- Description -->
    <p v-if="config.description" class="text-secondary text-sm leading-relaxed">
      {{ config.description }}
    </p>

    <!-- Local mode: model + tools + source -->
    <template v-if="mode === 'local'">
      <div
        v-if="config.model"
        class="flex items-center gap-1.5 text-xs text-warm-500 dark:text-warm-400"
      >
        <span class="i-carbon-machine-learning-model" />
        <span class="font-mono">{{ config.model }}</span>
      </div>
      <div v-if="config.tools && config.tools.length" class="flex flex-wrap gap-1">
        <el-tag
          v-for="tool in config.tools"
          :key="tool"
          size="small"
          type="info"
          effect="plain"
          round
        >
          {{ tool }}
        </el-tag>
      </div>
      <div v-if="config.path" class="text-xs text-warm-400 font-mono truncate" :title="config.path">
        {{ config.path }}
      </div>
    </template>

    <!-- Remote mode: url + tags -->
    <template v-if="mode === 'remote'">
      <div v-if="config.url" class="text-xs text-warm-400 font-mono truncate" :title="config.url">
        {{ config.url }}
      </div>
      <div v-if="config.tags && config.tags.length" class="flex flex-wrap gap-1">
        <el-tag v-for="tag in config.tags" :key="tag" size="small" type="info" effect="plain" round>
          {{ tag }}
        </el-tag>
      </div>
    </template>

    <!-- Actions -->
    <div class="flex justify-end mt-auto pt-1">
      <template v-if="mode === 'local'">
        <el-popconfirm
          title="Uninstall this config?"
          confirm-button-text="Uninstall"
          cancel-button-text="Cancel"
          @confirm="$emit('uninstall', config)"
        >
          <template #reference>
            <el-button size="small" type="danger" plain>
              <span class="i-carbon-trash-can mr-1" /> Uninstall
            </el-button>
          </template>
        </el-popconfirm>
      </template>
      <template v-if="mode === 'remote' && !installed">
        <el-button
          size="small"
          type="primary"
          :loading="installing"
          @click="$emit('install', config)"
        >
          <span v-if="!installing" class="i-carbon-download mr-1" /> Install
        </el-button>
      </template>
    </div>
  </div>
</template>

<script setup>
import GemBadge from "@/components/common/GemBadge.vue"

const props = defineProps({
  config: { type: Object, required: true },
  mode: { type: String, default: "local" },
  installed: { type: Boolean, default: false },
  installing: { type: Boolean, default: false },
})

defineEmits(["install", "uninstall"])

const typeBadgeGem = computed(() => {
  const t = props.config.config_type || props.config.type || ""
  return t === "terrarium" ? "taaffeite" : "iolite"
})

const typeBadgeLabel = computed(() => {
  return props.config.config_type || props.config.type || "creature"
})
</script>
