<template>
  <PhoneSheet :close-label="t('close')" test-id="phone-agent-sheet" @close="ctx.closeSheet()">
    <template #title>
      <span class="flex-1 min-w-0 flex items-center gap-2">
        <StatusDot :status="row?.status || 'stopped'" />
        <span class="truncate text-[15px] font-semibold text-warm-800 dark:text-warm-100">{{ name }}</span>
        <span v-if="row?.privileged" class="i-carbon-security text-iolite shrink-0" />
      </span>
    </template>
    <div v-if="!row" class="px-4 py-8 text-center text-sm text-warm-400">{{ t("status.none.agents") }}</div>
    <template v-else>
      <div v-if="error" class="mx-4 mt-3 text-xs text-coral" role="alert">{{ error }}</div>
      <div class="px-4 py-2 text-sm">
        <div class="kt-v2-line flex items-center justify-between gap-3 py-2.5 border-b">
          <span class="text-warm-500">{{ t("phone.status") }}</span>
          <span class="px-2 py-0.5 rounded text-xs" :class="row.status === 'running' ? 'bg-aquamarine/10 text-aquamarine-shadow dark:text-aquamarine-light' : 'bg-warm-100 dark:bg-warm-800 text-warm-500'">{{ t(`status.state.${row.status}`) }}</span>
        </div>
        <button type="button" class="kt-v2-line border-x-0 border-t-0 w-full min-h-12 flex items-center justify-between gap-3 py-2.5 border-b text-left" data-test="phone-agent-model" @click="ctx.openSheet('model', { agent: name })">
          <span class="text-warm-500 shrink-0">{{ t("phone.model") }}</span>
          <span class="flex items-center gap-1 min-w-0 font-mono text-xs text-iolite dark:text-iolite-light"
            ><span class="truncate">{{ row.model || t("phone.noModel") }}</span
            ><span class="i-carbon-chevron-right text-warm-400 shrink-0"
          /></span>
        </button>
        <div class="kt-v2-line flex items-center justify-between gap-3 py-2.5 border-b">
          <span class="text-warm-500">{{ t("phone.tokens") }}</span>
          <span class="font-mono text-warm-700 dark:text-warm-200">{{ formatTokens(row.tokens) }}</span>
        </div>
        <div class="flex items-center justify-between gap-3 py-2.5">
          <span class="text-warm-500">{{ t("phone.job") }}</span>
          <span class="min-w-0 truncate text-warm-700 dark:text-warm-200">
            <span v-if="row.job" class="inline-flex items-center gap-1"><span class="i-carbon-circle-dash animate-spin text-aquamarine" />{{ row.job.name }}</span>
            <span v-else class="text-warm-400">—</span>
          </span>
        </div>
      </div>
    </template>
    <template v-if="row" #footer>
      <div class="flex gap-3">
        <button v-if="row.status === 'running'" type="button" class="kt-v2-b flex-1 h-11 rounded-xl border border-coral/40 text-coral text-[15px] disabled:opacity-40" :disabled="busy" data-test="phone-agent-stop" @click="lifecycle('stop')">{{ t("status.stop") }}</button>
        <button v-else type="button" class="kt-v2-b flex-1 h-11 rounded-xl border border-aquamarine/50 text-aquamarine-shadow dark:text-aquamarine-light text-[15px] disabled:opacity-40" :disabled="busy" data-test="phone-agent-start" @click="lifecycle('start')">{{ t("status.start") }}</button>
        <button type="button" class="flex-1 h-11 rounded-xl bg-iolite text-white text-[15px]" data-test="phone-agent-chat" @click="openChat">{{ t("status.openChat") }}</button>
      </div>
    </template>
  </PhoneSheet>
</template>

<script setup>
import { computed, ref } from "vue"

import StatusDot from "@/components/common/StatusDot.vue"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { agentRows, formatTokens } from "@/components/session-v2/model/status/statusModel"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import PhoneSheet from "@/components/session-v2/phone/PhoneSheet.vue"
import { terrariumAPI } from "@/utils/api"

/** One agent on a phone: status, model (opens the model sheet), tokens and job; start or stop it, or open its chat. */
const props = defineProps({ name: { type: String, required: true } })

const ctx = useSessionV2()
const t = useV2T()
const chat = ctx.chat
const busy = ref(false)
const error = ref("")

const row = computed(
  () =>
    agentRows(ctx.instance.value, {
      tokenUsage: chat.tokenUsage,
      runningJobs: chat.runningJobs,
      modelByTab: chat.modelByTab,
      rootName: chat._rootSourceName,
    }).find((r) => r.name === props.name) || null,
)

async function lifecycle(verb) {
  if (busy.value) return
  busy.value = true
  error.value = ""
  try {
    if (verb === "start") await terrariumAPI.startCreature(ctx.sessionId.value, props.name)
    else await terrariumAPI.stopCreature(ctx.sessionId.value, props.name)
    await ctx.refresh()
  } catch (err) {
    error.value = err?.response?.data?.detail || err?.message || String(err)
  } finally {
    busy.value = false
  }
}

function openChat() {
  chat.openTab(row.value.key)
  ctx.setTab("chat")
  ctx.closeSheet()
}
</script>
