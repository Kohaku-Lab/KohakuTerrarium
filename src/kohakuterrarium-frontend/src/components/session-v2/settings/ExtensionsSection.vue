<template>
  <SectionShell :title="t('set.extensions.title')" :hint="t('set.extensions.hint')" :error="error" bare>
    <template #aside>
      <div class="flex items-center gap-3">
        <el-input v-model="query" size="small" clearable class="!w-56" :placeholder="t('set.filter')" />
        <button class="i-carbon-renew text-warm-400 hover:text-warm-700 dark:hover:text-warm-200" :title="t('set.refresh')" @click="load" />
      </div>
    </template>
    <div v-if="loading" class="text-sm text-warm-400">{{ t("loading") }}</div>
    <div v-else-if="!items.length" class="rounded-lg border border-dashed kt-v2-edge px-4 py-8 text-center text-sm text-warm-400">{{ t("set.extensions.empty") }}</div>
    <div v-else class="flex flex-col gap-6">
      <SettingsGroup v-for="group in groups" :key="group.type" :title="`${group.type} · ${group.items.length}`" data-test="v2-settings-extensions">
        <SettingsRow v-for="p in group.items" :key="`${p.name}:${p.path || ''}`" :title="p.name" :hint="p.description || ''">
          <template v-if="p.path" #detail
            ><p class="kt-text-caption mt-1 font-mono text-warm-400 truncate" :title="p.path">{{ p.path }}</p></template
          >
          <span v-if="p.source" class="font-mono text-[11px] text-warm-500">{{ p.source }}</span>
        </SettingsRow>
      </SettingsGroup>
      <div v-if="!groups.length" class="text-sm text-warm-400">{{ t("set.noMatches") }}</div>
    </div>
  </SectionShell>
</template>

<script setup>
import { computed, onMounted, ref } from "vue"

import { errorText } from "@/components/session-v2/model/settings/settingsModel"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import SectionShell from "@/components/session-v2/settings/SectionShell.vue"
import SettingsGroup from "@/components/settings/ui/SettingsGroup.vue"
import SettingsRow from "@/components/settings/ui/SettingsRow.vue"
import { onShownAgain } from "@/components/session-v2/settings/useSectionTarget"
import { registryAPI } from "@/utils/api"

/** Installed packages (creatures, terrariums, tools, plugins), grouped by type. */
const t = useV2T()
const items = ref([])
const query = ref("")
const loading = ref(false)
const error = ref("")

const groups = computed(() => {
  const q = query.value.trim().toLowerCase()
  const byType = new Map()
  for (const p of items.value) {
    if (q && !`${p.name} ${p.description || ""}`.toLowerCase().includes(q)) continue
    const type = p.type || "package"
    if (!byType.has(type)) byType.set(type, [])
    byType.get(type).push(p)
  }
  return [...byType].map(([type, list]) => ({ type, items: list }))
})

function normalize(data) {
  if (Array.isArray(data)) return data
  if (!data || typeof data !== "object") return []
  const out = []
  for (const [type, list] of Object.entries(data)) if (Array.isArray(list)) for (const it of list) out.push({ ...it, type })
  return out
}

async function load() {
  loading.value = !items.value.length
  error.value = ""
  try {
    items.value = normalize(await registryAPI.listLocal())
  } catch (err) {
    error.value = errorText(err)
    items.value = []
  } finally {
    loading.value = false
  }
}

onMounted(load)
onShownAgain(load)
</script>
