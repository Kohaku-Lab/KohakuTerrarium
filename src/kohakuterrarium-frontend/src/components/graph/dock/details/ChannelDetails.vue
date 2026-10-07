<template>
  <div class="flex flex-col gap-4">
    <header class="flex flex-col gap-1">
      <div class="flex items-center gap-2 min-w-0">
        <span class="text-aquamarine-shadow dark:text-aquamarine-light text-lg leading-none">≋</span>
        <span class="text-base font-semibold font-mono text-warm-800 dark:text-warm-100 truncate">{{ channel.name }}</span>
      </div>
      <div class="text-xs text-warm-500">{{ t("graph.channel.summary", { senders: channel.senders.length, listeners: channel.listeners.length, messages: messageCount }) }}</div>
      <div v-if="channel.description" class="text-xs text-warm-600 dark:text-warm-300">{{ channel.description }}</div>
    </header>

    <div class="flex flex-wrap gap-1.5">
      <button class="kt-graph-action kt-graph-action--primary" @click="$emit('open-chat')"><span class="i-carbon-chat" />{{ t("graph.action.chat") }}</button>
      <button class="kt-graph-action" @click="actions.openChatTab(channel.sessionId, `ch:${channel.name}`)"><span class="i-carbon-launch" />{{ t("graph.action.openTab") }}</button>
    </div>

    <div v-if="last" class="rounded-lg border border-aquamarine/30 bg-aquamarine/6 p-2 text-xs">
      <div class="text-warm-500 mb-0.5">{{ t("graph.channel.latest", { sender: last.sender }) }}</div>
      <div class="text-warm-700 dark:text-warm-200 break-words">{{ last.preview }}</div>
    </div>

    <form class="flex gap-1.5" @submit.prevent="post">
      <input id="graph-channel-post" v-model="draft" class="input-field flex-1 text-xs" :placeholder="t('graph.channel.postPlaceholder')" />
      <button class="kt-graph-action kt-graph-action--primary" :disabled="!draft.trim() || posting" type="submit"><span class="i-carbon-send" /></button>
    </form>

    <section class="flex flex-col gap-1.5">
      <h4 class="kt-graph-section">{{ t("graph.section.members") }}</h4>
      <div v-for="m in members" :key="m.id" class="flex items-center gap-2 text-xs">
        <span :class="statusStyle(m.status).text">{{ statusStyle(m.status).glyph }}</span>
        <button class="truncate text-left flex-1 hover:underline" @click="view.select('creature', m.id)">{{ m.name }}</button>
        <span class="font-mono text-[10px] text-aquamarine-shadow dark:text-aquamarine-light">{{ m.mode }}</span>
      </div>
      <div v-if="!members.length" class="text-xs text-warm-400">{{ t("graph.channel.noMembers") }}</div>
    </section>

    <section class="pt-2 border-t border-warm-200 dark:border-warm-700">
      <button class="kt-graph-action kt-graph-action--danger" @click="$emit('confirm', channelDeleteRequest(view, actions, t, channel))"><span class="i-carbon-trash-can" />{{ t("graph.action.removeChannel") }}</button>
    </section>
  </div>
</template>

<script setup>
import { computed, ref } from "vue"

import { statusStyle } from "@/components/graph/graphTheme"
import { channelDeleteRequest } from "@/components/graph/dock/details/deleteRequests"
import { useGraphLiveStore } from "@/stores/graph/live"
import { useI18n } from "@/utils/i18n"

const props = defineProps({
  view: { type: Object, required: true },
  actions: { type: Object, required: true },
  channel: { type: Object, required: true },
})
defineEmits(["open-chat", "confirm"])

const { t } = useI18n()
const live = useGraphLiveStore()
const draft = ref("")
const posting = ref(false)

const last = computed(() => live.lastMessages[props.channel.id] || null)
const messageCount = computed(() => props.channel.messageCount)

const members = computed(() => {
  const ids = new Set([...props.channel.senders, ...props.channel.listeners])
  return props.view.model.creatures
    .filter((c) => ids.has(c.id))
    .map((c) => {
      const send = props.channel.senders.includes(c.id)
      const listen = props.channel.listeners.includes(c.id)
      return { ...c, mode: send && listen ? "send+listen" : send ? "send" : "listen" }
    })
})

async function post() {
  const content = draft.value.trim()
  if (!content) return
  posting.value = true
  const ok = await props.actions.postToChannel(props.channel, content)
  posting.value = false
  if (ok) draft.value = ""
}
</script>
