<template>
  <div class="kt-v2-card p-4 flex flex-col gap-2" data-test="status-session-card">
    <div class="flex items-center gap-2">
      <span class="i-carbon-information text-warm-500" />
      <h3 class="text-sm font-semibold text-warm-700 dark:text-warm-200">{{ t("status.session") }}</h3>
      <span class="flex-1" />
      <span class="text-[11px] px-2 py-0.5 rounded-full" :class="instance?.status === 'running' ? 'bg-aquamarine/10 text-aquamarine' : 'bg-warm-100 dark:bg-warm-800 text-warm-500'">{{ instance?.status || "—" }}</span>
    </div>
    <dl class="grid grid-cols-[6.5rem_1fr] gap-x-3 gap-y-1.5 text-xs">
      <dt class="text-warm-400">{{ t("status.uptime") }}</dt>
      <dd class="font-mono text-warm-700 dark:text-warm-300">{{ uptime }}</dd>
      <dt class="text-warm-400">{{ t("status.site") }}</dt>
      <dd class="flex items-center gap-1 min-w-0"><SiteChip :node-id="homeNode" always-show /></dd>
      <dt class="text-warm-400">{{ t("status.model") }}</dt>
      <dd class="font-mono text-iolite dark:text-iolite-light truncate" :title="model">{{ modelName || "—" }}</dd>
      <dt class="text-warm-400">{{ t("status.provider") }}</dt>
      <dd class="text-warm-700 dark:text-warm-300 truncate">{{ provider || "—" }}</dd>
      <dt class="text-warm-400">{{ t("status.sessionId") }}</dt>
      <dd class="font-mono text-[11px] text-warm-500 truncate" :title="sessionLabel">{{ sessionLabel }}</dd>
    </dl>
  </div>
</template>

<script setup>
import { computed, onActivated, onBeforeUnmount, onDeactivated, ref } from "vue"

import SiteChip from "@/components/cluster/SiteChip.vue"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { formatUptime } from "@/components/session-v2/model/status/statusModel"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import { createVisibilityInterval } from "@/composables/useVisibilityInterval"

/** The session at a glance: state, uptime, site, the session's (primary) model and provider, and its id. */
const ctx = useSessionV2()
const t = useV2T()
const instance = computed(() => ctx.instance.value)
const chat = ctx.chat

const now = ref(Date.now())
const ticker = createVisibilityInterval(() => {
  now.value = Date.now()
}, 30_000)
onActivated(() => {
  now.value = Date.now()
  ticker.start()
})
onDeactivated(() => ticker.stop())
onBeforeUnmount(() => ticker.stop())

const uptime = computed(() => formatUptime(instance.value?.created_at, now.value))
const homeNode = computed(() => chat.sessionInfo?.homeNode || instance.value?.home_node || "_host")
const model = computed(() => chat.sessionInfo?.llmName || chat.sessionInfo?.model || instance.value?.llm_name || instance.value?.model || "")
const provider = computed(() => {
  const base = model.value.split("@", 1)[0]
  return base.includes("/") ? base.slice(0, base.indexOf("/")) : instance.value?.provider || ""
})
const modelName = computed(() => {
  const base = model.value
  return base.includes("/") ? base.slice(base.indexOf("/") + 1) : base
})
const sessionLabel = computed(() => chat.sessionInfo?.sessionId || instance.value?.session_id || ctx.sessionId.value || "—")
</script>
