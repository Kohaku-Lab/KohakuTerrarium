<template>
  <SectionShell :title="t('set.env.title')" :hint="t('set.env.hint')" :error="error" bare>
    <template #aside><CreaturePicker v-model="target" /></template>
    <div class="flex flex-col gap-6">
      <SettingsGroup>
        <SettingsRow :title="t('set.env.pwd')">
          <template #detail
            ><code class="block mt-0.5 font-mono text-sm text-iolite dark:text-iolite-light break-all">{{ pwd || "—" }}</code></template
          >
        </SettingsRow>
      </SettingsGroup>
      <SettingsGroup :title="t('set.env.vars', { n: entries.length })" :description="t('set.env.secrets')" data-test="v2-settings-env">
        <template #action>
          <el-input v-model="query" size="small" clearable class="!w-64 max-md:!w-40" :placeholder="t('set.filter')" />
          <button class="i-carbon-renew mb-1 text-warm-400 hover:text-warm-700 dark:hover:text-warm-200" :title="t('set.refresh')" @click="reload" />
        </template>
        <div v-if="loading" class="px-4 py-3 text-sm text-warm-400">{{ t("loading") }}</div>
        <template v-else>
          <div v-for="[k, v] in filtered" :key="k" class="px-4 py-2 grid grid-cols-[minmax(10rem,18rem)_1fr] max-md:grid-cols-1 gap-4 max-md:gap-0.5">
            <span class="font-mono text-xs text-iolite dark:text-iolite-light truncate" :title="k">{{ k }}</span>
            <span class="font-mono text-xs text-warm-600 dark:text-warm-300 break-all">{{ v }}</span>
          </div>
          <div v-if="!filtered.length" class="px-4 py-6 text-center text-sm text-warm-400">{{ t("set.noMatches") }}</div>
        </template>
      </SettingsGroup>
    </div>
  </SectionShell>
</template>

<script setup>
import { computed, ref } from "vue"

import { errorText } from "@/components/session-v2/model/settings/settingsModel"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import CreaturePicker from "@/components/session-v2/settings/CreaturePicker.vue"
import SectionShell from "@/components/session-v2/settings/SectionShell.vue"
import SettingsGroup from "@/components/settings/ui/SettingsGroup.vue"
import SettingsRow from "@/components/settings/ui/SettingsRow.vue"
import { useSectionLoad, useSectionTarget } from "@/components/session-v2/settings/useSectionTarget"
import { terrariumAPI } from "@/utils/api"

/** A creature's working directory and environment variables (read-only; secrets are filtered server-side). */
const t = useV2T()
const session = useSessionV2()
const target = useSectionTarget(session)
const pwd = ref("")
const env = ref({})
const query = ref("")
const loading = ref(false)
const error = ref("")

const entries = computed(() => Object.entries(env.value).sort(([a], [b]) => a.localeCompare(b)))
const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return entries.value
  return entries.value.filter(([k, v]) => k.toLowerCase().includes(q) || String(v).toLowerCase().includes(q))
})

async function load(isCurrent) {
  const sid = session.sessionId.value
  pwd.value = session.instance.value?.pwd || ""
  if (!sid || !target.value) return
  loading.value = true
  error.value = ""
  try {
    const data = await terrariumAPI.getEnv(sid, target.value)
    if (!isCurrent()) return
    pwd.value = data?.pwd || pwd.value
    env.value = data?.env || {}
  } catch (err) {
    if (!isCurrent()) return
    error.value = errorText(err)
    env.value = {}
  } finally {
    if (isCurrent()) loading.value = false
  }
}

const reload = useSectionLoad(load, [target, () => session.sessionId.value])
</script>
