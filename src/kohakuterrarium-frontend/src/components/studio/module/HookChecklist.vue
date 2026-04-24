<template>
  <div class="flex flex-col gap-2">
    <div v-if="catalog.loading" class="text-xs text-warm-500 italic px-1">
      {{ t("studio.common.loading") }}
    </div>
    <div v-else-if="!groups.length" class="text-xs text-warm-500 italic px-1">
      {{ t("studio.module.hooks.empty") }}
    </div>
    <template v-else>
      <div v-for="group in groups" :key="group.name" class="flex flex-col gap-1">
        <div class="text-[11px] font-medium uppercase tracking-wider text-warm-500 px-1">
          {{ t(`studio.module.hooks.group.${group.name}`) || group.name }}
        </div>
        <label v-for="hook in group.hooks" :key="hook.name" class="flex items-start gap-2 px-2 py-1 rounded hover:bg-warm-100 dark:hover:bg-warm-900 cursor-pointer">
          <input type="checkbox" :checked="isEnabled(hook.name)" class="mt-0.5 shrink-0" @change="toggle(hook.name)" />
          <div class="min-w-0 flex-1">
            <div class="text-xs font-mono text-warm-800 dark:text-warm-200 truncate">
              {{ hook.name }}
              <span class="text-[10px] text-warm-500">{{ hook.args_signature }}</span>
            </div>
            <div v-if="hook.description" class="text-[11px] text-warm-500 truncate">
              {{ hook.description }}
            </div>
          </div>
        </label>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted } from "vue"

import { useStudioCatalogStore } from "@/stores/studio/catalog"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()

const props = defineProps({
  /** Array of { name, body } — currently enabled hooks. */
  modelValue: { type: Array, default: () => [] },
})

const emit = defineEmits(["update:modelValue"])

const catalog = useStudioCatalogStore()

onMounted(() => {
  catalog.fetchAll().catch(() => {})
})

const groups = computed(() => {
  const byGroup = new Map()
  for (const h of catalog.pluginHooks || []) {
    const g = h.group || "other"
    if (!byGroup.has(g)) byGroup.set(g, [])
    byGroup.get(g).push(h)
  }
  return Array.from(byGroup.entries()).map(([name, hooks]) => ({ name, hooks }))
})

function isEnabled(name) {
  return (props.modelValue || []).some((h) => h.name === name)
}

function toggle(name) {
  const current = props.modelValue || []
  if (isEnabled(name)) {
    emit(
      "update:modelValue",
      current.filter((h) => h.name !== name),
    )
  } else {
    emit("update:modelValue", [...current, { name, body: "return None" }])
  }
}
</script>
