<template>
  <div class="settings-pane flex flex-col gap-3 max-w-4xl text-[13px]">
    <p class="text-xs text-warm-400 mb-2">{{ t("advanced.description") }}</p>

    <div class="card">
      <table class="w-full text-[12px]">
        <thead>
          <tr class="text-left text-warm-400 text-[11px]">
            <th class="px-3 py-2">{{ t("advanced.colName") }}</th>
            <th class="px-3 py-2">{{ t("advanced.colPath") }}</th>
            <th class="px-3 py-2">{{ t("advanced.colKind") }}</th>
            <th class="px-3 py-2 text-right">{{ t("advanced.colSize") }}</th>
            <th class="px-3 py-2">{{ t("advanced.colMtime") }}</th>
            <th class="px-3 py-2 text-right">{{ t("advanced.colActions") }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="f in files" :key="f.name" class="border-t border-warm-100 dark:border-warm-800 align-middle">
            <td class="px-3 py-2 font-mono">{{ f.name }}</td>
            <td class="px-3 py-2 font-mono text-warm-400 truncate max-w-[280px]" :title="f.path">{{ f.path }}</td>
            <td class="px-3 py-2">{{ f.kind }}</td>
            <td class="px-3 py-2 text-right text-warm-400">{{ f.exists ? formatSize(f.size) : "—" }}</td>
            <td class="px-3 py-2 text-warm-400">{{ f.exists ? formatTime(f.mtime) : t("advanced.notExist") }}</td>
            <td class="px-3 py-2 text-right">
              <el-button size="small" plain @click="openFile(f)">
                <span class="i-carbon-edit mr-1" />
                {{ t("common.edit") }}
              </el-button>
              <el-button size="small" plain :disabled="!f.exists" @click="downloadFile(f)">
                <span class="i-carbon-download mr-1" />
                {{ t("advanced.download") }}
              </el-button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Editor modal -->
    <el-dialog v-model="editorOpen" :title="t('advanced.editTitle', { name: editing?.name || '' })" width="800px" :close-on-click-modal="false" :before-close="onDialogClose">
      <div v-if="editorLoading" class="py-8 text-center text-secondary">{{ t("common.loading") }}</div>
      <template v-else>
        <p class="text-[12px] text-warm-400 mb-2 font-mono truncate">{{ editing?.path }}</p>
        <el-input v-model="editorContent" type="textarea" :rows="22" class="font-mono" @input="dirty = true" />
        <p v-if="saveError" class="text-[12px] text-red-500 mt-2">{{ saveError }}</p>
        <p v-if="!editing?.exists" class="text-[12px] text-amber-shadow dark:text-amber-light mt-2">
          {{ t("advanced.newFileHint") }}
        </p>
        <p v-if="dirty" class="text-[12px] text-amber-shadow dark:text-amber-light mt-2">
          {{ t("advanced.unsaved") }}
        </p>
      </template>
      <template #footer>
        <el-button @click="onDialogClose(() => (editorOpen = false))">{{ t("common.close") }}</el-button>
        <el-button type="primary" :loading="saving" :disabled="!dirty" @click="save">{{ t("common.save") }}</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { ref } from "vue"
import { ElMessage } from "element-plus"

import { settingsAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()

const files = ref([])
const editing = ref(null)
const editorOpen = ref(false)
const editorLoading = ref(false)
const editorContent = ref("")
const editorSha = ref("")
const dirty = ref(false)
const saving = ref(false)
const saveError = ref("")

async function load() {
  try {
    files.value = await settingsAPI.listConfigFiles()
  } catch (e) {
    ElMessage.error(e?.response?.data?.detail || e.message || "load failed")
  }
}

async function openFile(f) {
  editing.value = f
  editorOpen.value = true
  editorLoading.value = true
  editorContent.value = ""
  editorSha.value = ""
  dirty.value = false
  saveError.value = ""
  try {
    const data = await settingsAPI.readConfigFile(f.name)
    editorContent.value = data.content || ""
    editorSha.value = data.sha256
  } catch (e) {
    saveError.value = e?.response?.data?.detail || e.message || "read failed"
  } finally {
    editorLoading.value = false
  }
}

async function save() {
  if (!editing.value) return
  saving.value = true
  saveError.value = ""
  try {
    const r = await settingsAPI.writeConfigFile(editing.value.name, editorContent.value, editorSha.value || null)
    editorSha.value = r.sha256
    dirty.value = false
    ElMessage.success(t("advanced.saved", { name: editing.value.name }))
    await load()
  } catch (e) {
    saveError.value = e?.response?.data?.detail || e.message || "save failed"
  } finally {
    saving.value = false
  }
}

function onDialogClose(done) {
  if (dirty.value && !window.confirm(t("advanced.discardConfirm"))) {
    return
  }
  if (typeof done === "function") done()
  else editorOpen.value = false
}

function formatSize(bytes) {
  if (!bytes) return "0 B"
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
}

function formatTime(ts) {
  if (!ts) return "—"
  return new Date(ts * 1000).toLocaleString()
}

function downloadFile(f) {
  // Trigger a browser download via a blob URL — content fetched
  // through the same read endpoint to honour the server-side
  // whitelist + size guard.
  settingsAPI.readConfigFile(f.name).then((data) => {
    const blob = new Blob([data.content], { type: "text/plain;charset=utf-8" })
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = f.path.split(/[/\\]/).pop() || `${f.name}.txt`
    a.click()
    URL.revokeObjectURL(url)
  })
}

load()
</script>
