<template>
  <el-dialog v-model="localOpen" :title="t('studio.picker.title')" width="720px" :close-on-click-modal="true">
    <!-- Nav bar -->
    <div class="flex items-center gap-2 mb-3">
      <KButton size="sm" icon="i-carbon-arrow-up" :disabled="!parent || loading" @click="browseTo(parent)">
        {{ t("studio.picker.up") }}
      </KButton>
      <KButton size="sm" icon="i-carbon-data-base" :disabled="loading" @click="browseTo(null)">
        {{ t("studio.picker.roots") }}
      </KButton>
      <div class="flex-1 px-3 py-1.5 rounded border border-warm-200 dark:border-warm-700 bg-warm-50 dark:bg-warm-900 font-mono text-xs truncate" :title="current?.path || ''">
        {{ current?.path || t("studio.picker.chooseRoot") }}
      </div>
      <KButton size="sm" variant="primary" icon="i-carbon-checkmark" :disabled="!current?.path" @click="pick(current?.path)">
        {{ t("studio.picker.useThisFolder") }}
      </KButton>
    </div>

    <div v-if="error" class="mb-3 px-3 py-2 rounded bg-coral/10 text-coral text-xs border border-coral/20">
      {{ error }}
    </div>

    <!-- Roots view -->
    <div v-if="!current" class="flex flex-col gap-2">
      <div class="text-[11px] uppercase tracking-wider text-warm-500 dark:text-warm-400 font-medium">
        {{ t("studio.picker.allowedRoots") }}
      </div>
      <button v-for="r in roots" :key="r.path" class="text-left px-3 py-2 rounded border border-warm-200 dark:border-warm-700 hover:border-iolite hover:bg-warm-50 dark:hover:bg-warm-800 transition-colors" :disabled="loading" @click="browseTo(r.path)">
        <div class="flex items-center gap-2 min-w-0">
          <div class="i-carbon-folder text-amber shrink-0" />
          <span class="font-medium text-sm text-warm-800 dark:text-warm-200 truncate">
            {{ r.name }}
          </span>
          <span class="text-xs text-warm-500 font-mono truncate">
            {{ r.path }}
          </span>
        </div>
      </button>
      <div v-if="!loading && !roots.length" class="text-sm text-warm-500 py-6 text-center">
        {{ t("studio.picker.noRoots") }}
      </div>
    </div>

    <!-- Directory view -->
    <div v-else class="flex flex-col gap-1 max-h-96 overflow-y-auto">
      <button v-for="d in directories" :key="d.path" :class="['text-left px-3 py-2 rounded border transition-colors', selectedPath === d.path ? 'border-iolite bg-iolite/10 dark:bg-iolite/15' : 'border-warm-200 dark:border-warm-700 hover:border-iolite hover:bg-warm-50 dark:hover:bg-warm-800']" @click="selectedPath = d.path" @dblclick="browseTo(d.path)">
        <div class="flex items-center gap-2 min-w-0">
          <div class="i-carbon-folder text-amber shrink-0" />
          <span class="font-medium text-sm text-warm-800 dark:text-warm-200 truncate">
            {{ d.name }}
          </span>
          <span class="text-xs text-warm-500 font-mono truncate">
            {{ d.path }}
          </span>
        </div>
      </button>
      <div v-if="!loading && !directories.length" class="text-sm text-warm-500 py-6 text-center">
        {{ t("studio.picker.noSubdirs") }}
      </div>
    </div>

    <template #footer>
      <div class="flex justify-between gap-2">
        <KButton @click="close">{{ t("studio.common.cancel") }}</KButton>
        <KButton variant="primary" :disabled="!selectedPath" @click="pick(selectedPath)">
          {{ t("studio.picker.selectHighlighted") }}
        </KButton>
      </div>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref, watch } from "vue"
import { ElDialog } from "element-plus"

import KButton from "./KButton.vue"
import { filesAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  /** Path to start at (falls back to roots view when missing). */
  initialPath: { type: String, default: "" },
})

const emit = defineEmits(["update:modelValue", "pick"])

const { t } = useI18n()

const localOpen = ref(props.modelValue)
const loading = ref(false)
const error = ref("")
const current = ref(null)
const parent = ref(null)
const roots = ref([])
const directories = ref([])
const selectedPath = ref("")

watch(
  () => props.modelValue,
  async (v) => {
    localOpen.value = v
    if (v) {
      await open()
    }
  },
)

watch(localOpen, (v) => {
  if (v !== props.modelValue) emit("update:modelValue", v)
})

async function open() {
  const initial = (props.initialPath || "").trim()
  if (initial) {
    const ok = await browseTo(initial)
    if (!ok) await browseTo(null)
  } else {
    await browseTo(null)
  }
}

async function browseTo(path) {
  loading.value = true
  error.value = ""
  try {
    const data = await filesAPI.browseDirectories(path)
    current.value = data.current || null
    parent.value = data.parent || null
    roots.value = data.roots || []
    directories.value = data.directories || []
    selectedPath.value = data.current?.path || ""
    return true
  } catch (err) {
    error.value = err?.response?.data?.detail || err?.message || String(err)
    current.value = null
    parent.value = null
    directories.value = []
    if (!path) roots.value = []
    return false
  } finally {
    loading.value = false
  }
}

function pick(path) {
  if (!path) return
  emit("pick", path)
  close()
}

function close() {
  localOpen.value = false
}
</script>
