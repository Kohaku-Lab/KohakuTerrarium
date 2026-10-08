<template>
  <div class="h-full flex flex-col min-h-0" data-test="v2-side-terminal">
    <div class="shrink-0 flex items-center gap-2 px-3 h-9 border-b kt-v2-line text-[11px] text-warm-500">
      <span class="i-carbon-terminal" />
      <el-select v-if="targets.length > 1" v-model="target" size="small" class="!w-40" :teleported="true">
        <el-option v-for="name in targets" :key="name" :label="name" :value="name" />
      </el-select>
      <span v-else class="font-medium text-warm-700 dark:text-warm-200">{{ target }}</span>
      <SiteChip :node-id="homeNode" />
      <span class="w-1.5 h-1.5 rounded-full" :class="dotClass" :title="t(`side.terminal.state.${state}`)" />
      <span v-if="state === 'connecting'" class="text-warm-500">{{ t("side.terminal.state.connecting") }}</span>
      <span class="flex-1" />
      <button v-if="state === 'closed' && path" class="kt-v2-b h-6 px-2 rounded border text-warm-700 dark:text-warm-200 hover:bg-warm-100 dark:hover:bg-warm-800" @click="hostEl?.connect()">{{ t("side.terminal.connect") }}</button>
    </div>
    <div v-if="!path" class="flex-1 flex items-center justify-center text-xs text-warm-500">{{ t("side.terminal.none") }}</div>
    <XtermHost v-else ref="hostEl" class="flex-1" :path="path" :disconnected-label="t('side.terminal.disconnected')" @state="state = $event" />
  </div>
</template>

<script setup>
import { computed, ref, watch } from "vue"

import SiteChip from "@/components/cluster/SiteChip.vue"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import { focusedCreature } from "@/components/session-v2/model/widgets/widgetData"
import XtermHost from "@/components/session-v2/side/terminal/XtermHost.vue"

/** A terminal in a creature's working directory beside the chat; starts on the focused creature, then stays on its own pick. */
defineProps({ payload: { type: Object, default: () => ({}) } })

const ctx = useSessionV2()
const t = useV2T()
const hostEl = ref(null)
const state = ref("closed")

const creatures = computed(() => (ctx.instance.value?.creatures || []).filter((c) => c?.name))
const targets = computed(() => creatures.value.map((c) => c.name))
const focused = focusedCreature(ctx.instance.value, ctx.chat.activeTab, ctx.chat._rootSourceName)
const target = ref(targets.value.includes(focused) ? focused : targets.value[0] || "")

const path = computed(() => {
  const sid = ctx.sessionId.value
  if (!sid || !target.value) return ""
  return `/ws/sessions/${encodeURIComponent(sid)}/creatures/${encodeURIComponent(target.value)}/pty`
})
const homeNode = computed(() => {
  const c = creatures.value.find((x) => x.name === target.value)
  return c?.home_node || ctx.instance.value?.home_node || "_host"
})
const dotClass = computed(() => ({ open: "bg-aquamarine", connecting: "bg-amber animate-pulse", closed: "bg-warm-500" })[state.value])

watch(targets, (list) => {
  if (!list.includes(target.value)) target.value = list[0] || ""
})
</script>
