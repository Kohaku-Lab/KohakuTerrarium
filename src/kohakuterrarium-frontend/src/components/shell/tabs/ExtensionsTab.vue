<template>
  <div class="h-full overflow-y-auto">
    <div class="container-page max-w-5xl">
      <div class="mb-4 flex items-center justify-between gap-2">
        <div>
          <h1 class="text-xl font-bold text-warm-800 dark:text-warm-200">{{ t("extensions.title") }}</h1>
          <p class="text-secondary text-sm">{{ t("extensions.subtitle") }}</p>
        </div>
        <el-button size="small" plain @click="load">
          <span class="i-carbon-renew mr-1" />
          {{ t("common.refresh") }}
        </el-button>
      </div>

      <!-- Filters -->
      <div class="flex items-center gap-2 mb-4 flex-wrap">
        <el-input v-model="search" size="small" :placeholder="t('extensions.searchPlaceholder')" clearable style="max-width: 240px" />
        <el-select v-model="kindFilter" size="small" :placeholder="t('extensions.allKinds')" clearable style="width: 160px">
          <el-option v-for="k in availableKinds" :key="k" :value="k" :label="k" />
        </el-select>
        <span class="text-[11px] text-warm-400">{{ t("extensions.foundN", { n: filtered.length }) }}</span>
      </div>

      <div v-if="loading" class="py-8 text-center text-secondary">{{ t("common.loading") }}</div>
      <div v-else-if="error" class="card p-4 text-coral text-sm">{{ error }}</div>
      <div v-else-if="!extensions.length" class="card p-8 text-center text-secondary">
        {{ t("extensions.none") }}
      </div>

      <div v-else>
        <div v-for="entry in filtered" :key="`${entry.kind}/${entry.package}/${entry.name}`" class="card-hover p-3 mb-2 flex items-start gap-3">
          <span :class="iconClass(entry.kind)" class="text-lg shrink-0 mt-0.5" />
          <div class="flex-1 min-w-0">
            <div class="flex items-center gap-2 flex-wrap">
              <span class="font-medium text-warm-800 dark:text-warm-200">{{ entry.name }}</span>
              <el-tag size="small" :type="kindTagType(entry.kind)" effect="plain">{{ entry.kind }}</el-tag>
              <el-tag v-if="entry.editable" size="small" effect="plain">{{ t("extensions.editable") }}</el-tag>
            </div>
            <div class="text-[12px] text-warm-500 dark:text-warm-400 mt-0.5">
              <span class="font-mono">{{ entry.package }}</span>
              <span class="text-warm-400">@{{ entry.package_version }}</span>
              <span v-if="entry.module" class="ml-2 font-mono text-[11px] text-warm-400">{{ entry.module }}</span>
            </div>
            <p v-if="entry.description" class="text-[12px] text-warm-500 dark:text-warm-400 mt-1">
              {{ entry.description }}
            </p>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from "vue"
import { ElMessage } from "element-plus"

import { extensionsAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()

const extensions = ref([])
const loading = ref(false)
const error = ref("")
const search = ref("")
const kindFilter = ref("")

const availableKinds = computed(() => Array.from(new Set(extensions.value.map((e) => e.kind))).sort())

const filtered = computed(() => {
  const q = search.value.trim().toLowerCase()
  return extensions.value.filter((e) => {
    if (kindFilter.value && e.kind !== kindFilter.value) return false
    if (!q) return true
    return (e.name + " " + e.package + " " + (e.description || "")).toLowerCase().includes(q)
  })
})

const KIND_ICON = {
  plugin: "i-carbon-plug",
  tool: "i-carbon-tools",
  trigger: "i-carbon-flash",
  io: "i-carbon-data-table",
  "llm-preset": "i-carbon-machine-learning-model",
  skill: "i-carbon-skill-level",
  command: "i-carbon-command-line",
  "user-command": "i-carbon-user",
  prompt: "i-carbon-document",
}

const KIND_TAG = {
  plugin: "primary",
  tool: "success",
  trigger: "warning",
  io: "info",
  "llm-preset": "primary",
  skill: "success",
  command: "info",
  "user-command": "info",
  prompt: "info",
}

function iconClass(kind) {
  return KIND_ICON[kind] || "i-carbon-cube"
}

function kindTagType(kind) {
  return KIND_TAG[kind] || "info"
}

async function load() {
  loading.value = true
  error.value = ""
  try {
    extensions.value = await extensionsAPI.list()
  } catch (e) {
    error.value = e?.response?.data?.detail || e.message || "load failed"
    ElMessage.error(error.value)
  } finally {
    loading.value = false
  }
}

load()
</script>
