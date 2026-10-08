<template>
  <SectionShell :title="t('set.triggers.title')" :hint="t('set.triggers.hint')" :error="error">
    <template #aside>
      <div class="flex items-center gap-3">
        <CreaturePicker v-model="target" />
        <button class="i-carbon-renew text-warm-400 hover:text-warm-700 dark:hover:text-warm-200" :title="t('set.refresh')" @click="reload" />
      </div>
    </template>
    <div v-if="loading" class="text-sm text-warm-400">{{ t("loading") }}</div>
    <div v-else-if="!triggers.length" class="rounded-lg border border-dashed kt-v2-edge px-4 py-8 text-center text-sm text-warm-400">{{ t("set.triggers.empty") }}</div>
    <table v-else class="w-full text-sm" data-test="v2-settings-triggers">
      <thead>
        <tr class="text-left text-[11px] uppercase tracking-wider text-warm-400">
          <th class="py-2 pr-4 font-medium">{{ t("set.triggers.type") }}</th>
          <th class="py-2 pr-4 font-medium">{{ t("set.triggers.id") }}</th>
          <th class="py-2 pr-4 font-medium">{{ t("set.triggers.state") }}</th>
          <th class="py-2 font-medium">{{ t("set.triggers.created") }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="tr in triggers" :key="tr.trigger_id" class="border-t kt-v2-line">
          <td class="py-2 pr-4 font-medium text-warm-800 dark:text-warm-100">{{ tr.trigger_type }}</td>
          <td class="py-2 pr-4 font-mono text-xs text-warm-500">{{ tr.trigger_id }}</td>
          <td class="py-2 pr-4">
            <span class="inline-flex items-center gap-1.5 text-xs" :class="tr.running ? 'text-aquamarine' : 'text-warm-400'"><span class="w-1.5 h-1.5 rounded-full" :class="tr.running ? 'bg-aquamarine' : 'bg-warm-400'" />{{ tr.running ? t("set.triggers.running") : t("set.triggers.idle") }}</span>
          </td>
          <td class="py-2 font-mono text-xs text-warm-500">{{ formatTs(tr.created_at) }}</td>
        </tr>
      </tbody>
    </table>
  </SectionShell>
</template>

<script setup>
import { ref } from "vue"

import { errorText } from "@/components/session-v2/model/settings/settingsModel"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import CreaturePicker from "@/components/session-v2/settings/CreaturePicker.vue"
import SectionShell from "@/components/session-v2/settings/SectionShell.vue"
import { useSectionLoad, useSectionTarget } from "@/components/session-v2/settings/useSectionTarget"
import { terrariumAPI } from "@/utils/api"

/** A creature's active triggers. */
const t = useV2T()
const session = useSessionV2()
const target = useSectionTarget(session)
const triggers = ref([])
const loading = ref(false)
const error = ref("")

function formatTs(ts) {
  if (!ts) return "—"
  const d = new Date(ts)
  return Number.isNaN(d.getTime()) ? String(ts) : d.toLocaleString()
}

async function load(isCurrent) {
  const sid = session.sessionId.value
  if (!sid || !target.value) {
    triggers.value = []
    return
  }
  loading.value = true
  error.value = ""
  try {
    const data = await terrariumAPI.listTriggers(sid, target.value)
    if (!isCurrent()) return
    triggers.value = Array.isArray(data) ? data : []
  } catch (err) {
    if (!isCurrent()) return
    error.value = errorText(err)
    triggers.value = []
  } finally {
    if (isCurrent()) loading.value = false
  }
}

const reload = useSectionLoad(load, [target, () => session.sessionId.value])
</script>
