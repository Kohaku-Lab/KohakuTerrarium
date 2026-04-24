<template>
  <div class="flex flex-col gap-2">
    <div class="flex items-center gap-2">
      <div class="relative flex-1">
        <input :value="search" :placeholder="t('studio.module.tools.searchPlaceholder')" class="w-full px-2.5 py-1.5 pl-7 rounded-md text-xs bg-warm-50 dark:bg-warm-950 border border-warm-200 dark:border-warm-700 focus:outline-none focus:border-iolite" @input="search = $event.target.value" />
        <div class="i-carbon-search absolute left-2 top-1/2 -translate-y-1/2 text-xs text-warm-500" />
      </div>
      <span class="text-[11px] text-warm-500">{{ modelValue.length }} / {{ filteredTools.length }}</span>
    </div>

    <div class="rounded-md border border-warm-200 dark:border-warm-800 max-h-48 overflow-auto">
      <div v-if="catalog.loading" class="px-3 py-2 text-xs text-warm-500">
        {{ t("studio.common.loading") }}
      </div>
      <div v-else-if="!filteredTools.length" class="px-3 py-2 text-xs text-warm-500 italic">
        {{ t("studio.module.tools.empty") }}
      </div>
      <ul v-else class="py-1">
        <li v-for="tool in filteredTools" :key="tool.name">
          <label :class="['flex items-center gap-2 px-3 py-1 text-xs cursor-pointer transition-colors', isSelected(tool.name) ? 'bg-iolite/10 text-iolite dark:text-iolite-light' : 'hover:bg-warm-100 dark:hover:bg-warm-900']">
            <input type="checkbox" :checked="isSelected(tool.name)" class="shrink-0" @change="toggle(tool.name)" />
            <span class="font-mono flex-1 truncate">{{ tool.name }}</span>
            <span v-if="tool.source && tool.source !== 'builtin'" class="shrink-0 text-[10px] uppercase tracking-wider px-1 rounded bg-warm-200/60 dark:bg-warm-800/60 text-warm-500">
              {{ shortSource(tool.source) }}
            </span>
          </label>
        </li>
      </ul>
    </div>

    <div v-if="modelValue.length" class="flex flex-wrap gap-1">
      <span v-for="name in modelValue" :key="name" class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-iolite/10 text-iolite dark:text-iolite-light text-[11px] font-mono">
        {{ name }}
        <button class="text-iolite/70 hover:text-coral" @click="toggle(name)">
          <div class="i-carbon-close text-xs" />
        </button>
      </span>
    </div>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from "vue"

import { useStudioCatalogStore } from "@/stores/studio/catalog"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
})

const emit = defineEmits(["update:modelValue"])

const catalog = useStudioCatalogStore()
const search = ref("")

onMounted(() => {
  catalog.fetchAll().catch(() => {})
})

const filteredTools = computed(() => {
  const q = search.value.trim().toLowerCase()
  const all = catalog.tools || []
  if (!q) return all
  return all.filter((t) => t.name.toLowerCase().includes(q))
})

function isSelected(name) {
  return (props.modelValue || []).includes(name)
}

function toggle(name) {
  const current = props.modelValue || []
  if (current.includes(name)) {
    emit(
      "update:modelValue",
      current.filter((x) => x !== name),
    )
  } else {
    emit("update:modelValue", [...current, name])
  }
}

function shortSource(source) {
  if (source === "workspace") return "ws"
  if (source === "workspace-manifest") return "yaml"
  if (source.startsWith("package:")) return "pkg"
  return source
}
</script>
