<template>
  <div class="mt-2 flex items-center gap-2 text-[11px]">
    <span v-if="loading" class="text-warm-400">{{ t("common.loading") }}</span>
    <template v-else-if="status?.authenticated">
      <span class="text-emerald-600 dark:text-emerald-400">
        {{ t("settings.grok.reusing", { source: status.source }) }}
      </span>
      <button class="text-iolite hover:underline" @click="load">{{ t("common.refresh") }}</button>
    </template>
    <template v-else>
      <span class="text-amber-shadow dark:text-amber-light">{{ t("settings.grok.missing") }}</span>
      <button class="text-iolite hover:underline" @click="load">{{ t("common.refresh") }}</button>
    </template>
  </div>
</template>

<script setup>
import { ref, watch } from "vue"

import { settingsAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"

const props = defineProps({
  node: { type: String, default: "_host" },
})

const { t } = useI18n()
const loading = ref(false)
const status = ref(null)

async function load() {
  loading.value = true
  try {
    status.value = await settingsAPI.getGrokStatus(props.node)
  } catch {
    status.value = { authenticated: false }
  } finally {
    loading.value = false
  }
}

watch(() => props.node, load, { immediate: true })
</script>
