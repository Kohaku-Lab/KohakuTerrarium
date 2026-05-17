<template>
  <el-drawer v-model="visible" :title="t('registry.editFilesTitle', { name: packageName })" size="60%" direction="rtl">
    <div class="h-full flex flex-col gap-3 text-[13px]">
      <div v-if="loading" class="py-8 text-center text-secondary">{{ t("common.loading") }}</div>
      <template v-else>
        <div class="flex gap-3 flex-1 min-h-0">
          <!-- File list -->
          <div class="w-64 shrink-0 border-r border-warm-200 dark:border-warm-700 pr-2 overflow-y-auto">
            <div class="text-[11px] text-warm-400 mb-2">{{ t("registry.editFilesCount", { n: editableFiles.length }) }}</div>
            <ul class="space-y-0.5">
              <li v-for="f in editableFiles" :key="f.path">
                <button class="w-full text-left text-[12px] px-2 py-1 rounded hover:bg-warm-100 dark:hover:bg-warm-800" :class="{ 'bg-warm-100 dark:bg-warm-800 font-medium': currentFile === f.path }" @click="openFile(f.path)">
                  <span class="font-mono truncate block">{{ f.path }}</span>
                </button>
              </li>
            </ul>
          </div>

          <!-- Editor area -->
          <div class="flex-1 min-w-0 flex flex-col gap-2">
            <div v-if="!currentFile" class="flex-1 grid place-items-center text-warm-400">
              {{ t("registry.editPickFile") }}
            </div>
            <template v-else>
              <div class="flex items-center justify-between gap-2">
                <span class="font-mono text-[12px] text-warm-500 truncate">{{ currentFile }}</span>
                <div class="flex gap-2 items-center">
                  <span v-if="dirty" class="text-[11px] text-amber-shadow dark:text-amber-light">{{ t("registry.editUnsaved") }}</span>
                  <el-button size="small" :loading="saving" :disabled="!dirty" type="primary" @click="save">{{ t("common.save") }}</el-button>
                </div>
              </div>
              <el-input v-model="content" type="textarea" :rows="22" class="font-mono" @input="onEdit" />
              <p v-if="saveError" class="text-[12px] text-red-500">{{ saveError }}</p>
            </template>
          </div>
        </div>
      </template>
    </div>
  </el-drawer>
</template>

<script setup>
import { computed, ref, watch } from "vue"
import { ElMessage } from "element-plus"

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
const files = ref([])
const currentFile = ref("")
const content = ref("")
const originalSha = ref("")
const dirty = ref(false)
const saving = ref(false)
const saveError = ref("")

const _TEXT_SUFFIXES = [".yaml", ".yml", ".json", ".md", ".markdown", ".txt", ".py", ".toml", ".ini", ".cfg", ".jinja", ".jinja2", ".j2", ".sh"]
const editableFiles = computed(() => files.value.filter((f) => !f.is_dir && _TEXT_SUFFIXES.some((s) => f.path.toLowerCase().endsWith(s))))

async function load() {
  if (!props.packageName) return
  loading.value = true
  currentFile.value = ""
  content.value = ""
  originalSha.value = ""
  dirty.value = false
  saveError.value = ""
  try {
    files.value = await registryAPI.listFiles(props.packageName)
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || e.message || "load failed")
    files.value = []
  } finally {
    loading.value = false
  }
}

async function openFile(path) {
  if (dirty.value) {
    const ok = await confirmDiscard()
    if (!ok) return
  }
  currentFile.value = path
  saveError.value = ""
  try {
    const data = await registryAPI.readFile(props.packageName, path)
    content.value = data.content
    originalSha.value = data.sha256
    dirty.value = false
  } catch (e) {
    saveError.value = e?.response?.data?.detail || e.message || "read failed"
    content.value = ""
  }
}

function onEdit() {
  dirty.value = true
}

async function save() {
  saving.value = true
  saveError.value = ""
  try {
    const r = await registryAPI.writeFile(props.packageName, currentFile.value, content.value, originalSha.value || null)
    originalSha.value = r.sha256
    dirty.value = false
    ElMessage.success(t("registry.fileSaved", { path: currentFile.value }))
  } catch (e) {
    saveError.value = e?.response?.data?.detail || e.message || "save failed"
  } finally {
    saving.value = false
  }
}

async function confirmDiscard() {
  // Lightweight: window.confirm avoids pulling in another ElMessageBox.
  return window.confirm(t("registry.editDiscardConfirm"))
}

watch(
  () => [visible.value, props.packageName],
  ([v]) => {
    if (v) load()
  },
)
</script>
