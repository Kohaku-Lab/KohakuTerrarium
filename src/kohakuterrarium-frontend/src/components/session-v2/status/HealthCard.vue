<template>
  <div class="kt-v2-card p-4 flex flex-col gap-2" data-test="status-health-card">
    <div class="flex items-center gap-2">
      <span class="i-carbon-activity text-aquamarine" />
      <h3 class="text-sm font-semibold text-warm-700 dark:text-warm-200">{{ t("status.health") }}</h3>
    </div>
    <dl class="grid grid-cols-[1fr_auto] gap-x-3 gap-y-1.5 text-xs">
      <dt class="text-warm-400">{{ t("status.connection") }}</dt>
      <dd class="flex items-center gap-1.5 justify-end">
        <span class="w-1.5 h-1.5 rounded-full" :class="connectionDot" />
        <span class="font-mono text-warm-700 dark:text-warm-300">{{ chat.wsStatus || "—" }}</span>
      </dd>
      <dt class="text-warm-400">{{ t("status.recovering") }}</dt>
      <dd class="font-mono text-right" :class="recovering ? 'text-amber' : 'text-warm-500'">{{ recovering }}</dd>
      <dt class="text-warm-400">{{ t("status.stoppedAgents") }}</dt>
      <dd class="font-mono text-right" :class="stopped ? 'text-coral' : 'text-warm-500'">{{ stopped }}</dd>
      <dt class="text-warm-400">{{ t("status.runningJobs") }}</dt>
      <dd class="font-mono text-right text-warm-700 dark:text-warm-300">{{ jobs }}</dd>
      <dt class="text-warm-400">{{ t("status.unread") }}</dt>
      <dd class="font-mono text-right" :class="unread ? 'text-amber' : 'text-warm-500'">{{ unread }}</dd>
    </dl>
  </div>
</template>

<script setup>
import { computed } from "vue"

import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { creatureStatus } from "@/components/session-v2/model/sessionModel"
import { useV2T } from "@/components/session-v2/model/v2Strings"

/** Connection and trouble counters: recovering models, stopped agents, running jobs, unread. */
const ctx = useSessionV2()
const t = useV2T()
const chat = ctx.chat

const connectionDot = computed(() => {
  if (chat.wsStatus === "open" || chat.wsStatus === "connected") return "bg-aquamarine"
  if (chat.wsStatus === "reconnecting" || chat.wsStatus === "connecting") return "bg-amber"
  return "bg-coral"
})
const recovering = computed(() => Object.values(chat.modelRecoveryByTab || {}).filter((s) => s?.phase).length)
const stopped = computed(() => (ctx.instance.value?.creatures || []).filter((c) => creatureStatus(c) === "stopped").length)
const jobs = computed(() => Object.keys(chat.runningJobs || {}).length)
const unread = computed(() => Object.values(chat.unreadCounts || {}).reduce((sum, n) => sum + (n || 0), 0))
</script>
