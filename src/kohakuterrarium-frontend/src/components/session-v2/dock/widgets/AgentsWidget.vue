<template>
  <div class="h-full min-h-0 overflow-y-auto py-1" data-test="v2-widget-agents">
    <div v-if="error" class="px-3 py-1 text-[11px] text-coral">{{ error }}</div>
    <div v-if="!creatures.length" class="px-3 py-6 text-center text-xs text-warm-400">{{ t("widget.agents.empty") }}</div>
    <div v-for="c in creatures" :key="c.creature_id || c.name" class="group flex items-center gap-2.5 px-3 py-2 hover:bg-warm-100 dark:hover:bg-warm-800/60" :class="ctx.chat.activeTab === keyOf(c) ? 'bg-iolite/5' : ''">
      <StatusDot :status="creatureStatus(c)" />
      <div class="min-w-0 flex-1">
        <div class="flex items-center gap-1.5 text-[13px] text-warm-800 dark:text-warm-100 min-w-0">
          <span class="truncate font-medium">{{ c.name }}</span>
          <span v-if="isPrivileged(c)" class="i-carbon-security text-iolite text-xs shrink-0" :title="t('privileged')" />
          <span v-if="busy(c)" class="i-carbon-circle-dash animate-spin text-aquamarine text-xs shrink-0" :title="t('widget.busy')" />
          <SiteChip v-if="mode === 'side'" :node-id="c.home_node || '_host'" />
        </div>
        <div class="text-[11px] text-warm-500 truncate font-mono">{{ creatureModel(c) || t("widget.noModel") }}</div>
      </div>
      <button class="h-6 px-2 rounded text-[11px] text-iolite dark:text-iolite-light hover:bg-iolite/10 shrink-0" :data-test="`v2-agent-chat-${c.name}`" @click="openChat(c)">{{ t("widget.openChat") }}</button>
      <button v-if="creatureStatus(c) === 'running'" class="h-6 px-2 rounded text-[11px] text-coral hover:bg-coral/10 shrink-0 disabled:opacity-40" :disabled="!!pending[c.name]" @click="lifecycle(c, 'stop')">{{ t("widget.stop") }}</button>
      <button v-else class="h-6 px-2 rounded text-[11px] text-aquamarine-shadow dark:text-aquamarine-light hover:bg-aquamarine/10 shrink-0 disabled:opacity-40" :disabled="!!pending[c.name]" @click="lifecycle(c, 'start')">{{ t("widget.start") }}</button>
    </div>
  </div>
</template>

<script setup>
import { computed, reactive, ref } from "vue"

import SiteChip from "@/components/cluster/SiteChip.vue"
import StatusDot from "@/components/common/StatusDot.vue"
import { tabKeyFor } from "@/components/session-v2/model/creatureKeys"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { creatureStatus, isPrivileged } from "@/components/session-v2/model/sessionModel"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import { creatureModel } from "@/components/session-v2/model/widgets/widgetData"
import { terrariumAPI } from "@/utils/api"

/** The session's creatures: status, model, busy state; open a creature's chat or start / stop it. */
defineProps({ mode: { type: String, default: "widget" } })

const ctx = useSessionV2()
const t = useV2T()
const pending = reactive({})
const error = ref("")

const creatures = computed(() => [...(ctx.instance.value?.creatures || [])].sort((a, b) => Number(isPrivileged(b)) - Number(isPrivileged(a))))

const keyOf = (c) => tabKeyFor(c.name, ctx.chat._rootSourceName)

function busy(c) {
  return !!ctx.chat.processingByTab?.[keyOf(c)]
}

function openChat(c) {
  ctx.chat.openTab(keyOf(c))
  ctx.setTab("chat")
  ctx.closeWidget()
}

async function lifecycle(c, verb) {
  const sid = ctx.sessionId.value
  if (!sid || pending[c.name]) return
  pending[c.name] = true
  error.value = ""
  try {
    if (verb === "start") await terrariumAPI.startCreature(sid, c.name)
    else await terrariumAPI.stopCreature(sid, c.name)
    await ctx.refresh()
  } catch (err) {
    error.value = err?.response?.data?.detail || err?.message || String(err)
  } finally {
    pending[c.name] = false
  }
}
</script>
