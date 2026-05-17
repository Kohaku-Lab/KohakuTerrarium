<template>
  <el-drawer v-model="visible" :title="t('registry.infoTitle', { name: packageName })" size="40%" direction="rtl">
    <div v-if="loading" class="py-8 text-center text-secondary">{{ t("common.loading") }}</div>
    <div v-else-if="error" class="card p-4 text-coral text-sm">{{ error }}</div>
    <div v-else-if="info" class="space-y-4 text-[13px]">
      <div>
        <h3 class="font-semibold text-warm-700 dark:text-warm-200 mb-1">{{ info.name }}</h3>
        <p v-if="info.description" class="text-secondary">{{ info.description }}</p>
      </div>

      <div class="grid grid-cols-2 gap-2 text-[12px]">
        <div>
          <div class="text-warm-400">{{ t("registry.infoKind") }}</div>
          <div class="font-mono">{{ info.config_type || info.type || "creature" }}</div>
        </div>
        <div v-if="info.model">
          <div class="text-warm-400">{{ t("registry.infoModel") }}</div>
          <div class="font-mono">{{ info.model }}</div>
        </div>
        <div v-if="info.path" class="col-span-2">
          <div class="text-warm-400">{{ t("registry.infoPath") }}</div>
          <div class="font-mono text-[11px] break-all">{{ info.path }}</div>
        </div>
      </div>

      <div v-if="info.tools && info.tools.length">
        <div class="text-warm-400 text-[12px] mb-1">{{ t("registry.infoTools") }}</div>
        <div class="flex flex-wrap gap-1">
          <el-tag v-for="tl in info.tools" :key="tl" size="small" type="info" effect="plain" round>{{ tl }}</el-tag>
        </div>
      </div>

      <div v-if="files.length">
        <div class="text-warm-400 text-[12px] mb-1">{{ t("registry.infoFiles", { n: files.length }) }}</div>
        <ul class="text-[12px] font-mono space-y-0.5 max-h-40 overflow-y-auto">
          <li v-for="f in files.slice(0, 50)" :key="f.path" class="truncate text-warm-500 dark:text-warm-400">
            {{ f.path }}
          </li>
          <li v-if="files.length > 50" class="text-warm-400">… {{ files.length - 50 }} more</li>
        </ul>
      </div>
    </div>
  </el-drawer>
</template>

<script setup>
import { computed, ref, watch } from "vue"

import { registryAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  packageName: { type: String, default: "" },
})
const emit = defineEmits(["update:modelValue"])
const { t } = useI18n()

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit("update:modelValue", v),
})

const loading = ref(false)
const error = ref("")
const info = ref(null)
const files = ref([])

async function load() {
  if (!props.packageName) return
  loading.value = true
  error.value = ""
  info.value = null
  files.value = []
  try {
    // Pull the full local catalog row (it has model/description/path).
    const all = await registryAPI.listLocal()
    info.value = all.find((c) => c.name === props.packageName) || { name: props.packageName }
    try {
      files.value = await registryAPI.listFiles(props.packageName)
    } catch {
      files.value = []
    }
  } catch (e) {
    error.value = e?.response?.data?.detail || e.message || "load failed"
  } finally {
    loading.value = false
  }
}

watch(
  () => [visible.value, props.packageName],
  ([v]) => {
    if (v) load()
  },
)
</script>
