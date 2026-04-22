<template>
  <el-popover v-model:visible="open" :width="480" placement="top-end" trigger="click" :hide-after="0" popper-class="studio-model-picker-popover">
    <template #reference>
      <button type="button" class="flex items-center gap-1.5 px-2 py-0.5 rounded hover:bg-warm-200/60 dark:hover:bg-warm-800/60 text-[11px] transition-colors" :title="t('studio.model.pickerTitle')">
        <div class="i-carbon-machine-learning-model text-xs text-iolite dark:text-iolite-light shrink-0" />
        <span class="font-mono truncate max-w-[16rem]">{{ summary }}</span>
        <div class="i-carbon-chevron-up text-[10px] text-warm-400 shrink-0" />
      </button>
    </template>

    <div class="flex flex-col gap-2 p-1 max-h-[60vh]">
      <div class="flex items-center gap-2">
        <input v-model="search" type="text" :placeholder="t('studio.model.search')" class="flex-1 px-2 py-1 text-xs rounded bg-warm-50 dark:bg-warm-950 border border-warm-200 dark:border-warm-700 focus:outline-none focus:border-iolite" />
        <button class="text-[11px] text-warm-500 hover:text-warm-700 px-2 py-1" :disabled="loading" @click="fetchModels({ force: true })">
          {{ t("studio.model.refresh") }}
        </button>
      </div>

      <!-- "Let user choose" / inherit default -->
      <button :class="['text-left px-3 py-2 rounded border transition-colors', !hasExplicitModel ? 'border-iolite bg-iolite/10' : 'border-warm-200 dark:border-warm-700 hover:border-iolite hover:bg-warm-50 dark:hover:bg-warm-800']" @click="onInherit">
        <div class="flex items-center gap-2">
          <div class="i-carbon-user text-warm-500 shrink-0" />
          <div class="flex-1 min-w-0">
            <div class="text-sm font-medium text-warm-800 dark:text-warm-200">
              {{ t("studio.model.letUserChoose") }}
            </div>
            <div class="text-[11px] text-warm-500">
              {{ t("studio.model.letUserChooseHint") }}
            </div>
          </div>
        </div>
      </button>

      <div class="text-[10px] uppercase tracking-wider text-warm-400 mt-1 px-1">
        {{ t("studio.model.orPick") }}
      </div>

      <div class="flex flex-col gap-1 max-h-72 overflow-y-auto">
        <div v-if="loading" class="text-xs text-warm-500 p-2">
          {{ t("studio.common.loading") }}
        </div>
        <button v-for="m in filtered" :key="m.name" :class="['text-left px-2.5 py-1.5 rounded border text-xs transition-colors', isCurrent(m) ? 'border-iolite bg-iolite/10' : m.available ? 'border-warm-200 dark:border-warm-700 hover:border-iolite hover:bg-warm-50 dark:hover:bg-warm-800' : 'border-warm-200 dark:border-warm-700 opacity-50']" @click="onPick(m)">
          <div class="flex items-center gap-2">
            <span class="font-medium text-sm">{{ m.name }}</span>
            <span v-if="m.is_default" class="text-[9px] px-1 rounded bg-iolite/20 text-iolite uppercase">
              {{ t("studio.model.userDefault") }}
            </span>
            <span v-if="!m.available" class="text-[9px] text-warm-400">
              {{ t("studio.model.notAvailable") }}
            </span>
          </div>
          <div class="text-[10px] text-warm-500 font-mono truncate">{{ m.model }} · {{ m.provider }}</div>
        </button>
        <div v-if="!loading && !filtered.length" class="text-xs text-warm-500 italic p-2 text-center">
          {{ t("studio.model.noMatch") }}
        </div>
      </div>
    </div>
  </el-popover>
</template>

<script setup>
import { computed, onMounted, ref } from "vue"
import { ElPopover } from "element-plus"

import { catalogAPI } from "@/utils/studio/api"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()

const props = defineProps({
  /** Current creature config — read llm_profile / model fields. */
  config: { type: Object, default: () => ({}) },
})

const emit = defineEmits(["patch"])

const open = ref(false)
const models = ref([])
const loading = ref(false)
const search = ref("")

async function fetchModels({ force = false } = {}) {
  if (loading.value) return
  if (models.value.length && !force) return
  loading.value = true
  try {
    models.value = await catalogAPI.models()
  } catch {
    models.value = []
  } finally {
    loading.value = false
  }
}

onMounted(() => fetchModels())

// LLM-profile fields live under `config.controller.*` in kt-biome-style
// YAML (the key is `llm`, not `llm_profile`). Fall back to top-level
// for legacy configs that put them at the root.
const controller = computed(() => props.config.controller || {})
const currentLlm = computed(() => controller.value.llm ?? props.config.llm_profile ?? "")
const currentModel = computed(() => controller.value.model ?? props.config.model ?? "")

const hasExplicitModel = computed(() => !!(currentLlm.value || currentModel.value))

const summary = computed(() => {
  if (currentLlm.value) return `@${currentLlm.value}`
  if (currentModel.value) return currentModel.value
  return t("studio.model.letUserChooseShort")
})

const filtered = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return models.value
  return models.value.filter((m) => m.name.toLowerCase().includes(q) || (m.model || "").toLowerCase().includes(q) || (m.provider || "").toLowerCase().includes(q))
})

function isCurrent(m) {
  if (currentLlm.value) return m.name === currentLlm.value
  if (currentModel.value) return m.model === currentModel.value
  return false
}

/** Clear controller.llm/model/provider + legacy top-level fields —
 *  runtime falls back to the user's default model. */
function onInherit() {
  emit("patch", "controller.llm", undefined)
  emit("patch", "controller.model", undefined)
  emit("patch", "controller.provider", undefined)
  emit("patch", "llm_profile", undefined)
  emit("patch", "model", undefined)
  emit("patch", "provider", undefined)
  open.value = false
}

/** Pick a specific profile — stored as controller.llm reference. */
function onPick(m) {
  if (!m.available) return
  emit("patch", "controller.llm", m.name)
  emit("patch", "controller.model", undefined)
  emit("patch", "controller.provider", undefined)
  emit("patch", "llm_profile", undefined)
  emit("patch", "model", undefined)
  emit("patch", "provider", undefined)
  open.value = false
}
</script>

<style>
/* Element-Plus popover scoping so the studio footer picker doesn't
   inherit the runner's popover paddings. */
.studio-model-picker-popover.el-popover.el-popper {
  padding: 8px !important;
}
</style>
