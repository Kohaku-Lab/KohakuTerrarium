<template>
  <aside class="kt-v2-panel kt-v2-edge h-full w-60 shrink-0 flex flex-col border-l" data-test="channel-members">
    <header class="kt-v2-line h-10 shrink-0 flex items-center gap-2 px-3 border-b text-xs font-medium text-warm-700 dark:text-warm-200">
      <span class="i-carbon-user-multiple text-warm-500" />
      <span class="truncate">{{ t("members.title") }}</span>
      <span class="font-mono font-normal text-warm-400">{{ members.length }}</span>
      <span class="flex-1" />
      <button class="i-carbon-close text-warm-400 hover:text-warm-700 dark:hover:text-warm-200" :title="t('members.hide')" data-test="channel-members-hide" @click="$emit('hide')" />
    </header>
    <div class="flex-1 min-h-0 overflow-y-auto px-2 py-3">
      <div v-if="!members.length" class="px-2 py-6 text-center text-xs text-warm-400">{{ t("members.empty") }}</div>
      <section v-for="group in groups" :key="group.id" class="mb-4">
        <h4 class="px-2 pb-1 text-[11px] font-semibold uppercase tracking-wide text-warm-400">{{ t(`members.${group.id}`) }} — {{ group.items.length }}</h4>
        <button v-for="m in group.items" :key="m.name" class="w-full flex items-center gap-2.5 px-2 py-1.5 rounded-lg text-left hover:bg-warm-100 dark:hover:bg-warm-800" :class="group.id === 'stopped' ? 'opacity-55' : ''" :title="t('members.openChat', { name: m.name })" :data-test="`member-${m.name}`" @click="chat.openTab(m.key)">
          <span class="relative w-8 h-8 shrink-0 rounded-full flex items-center justify-center text-xs font-semibold uppercase" :class="m.privileged ? 'bg-iolite/15 text-iolite dark:text-iolite-light' : 'bg-warm-200 dark:bg-warm-700 text-warm-700 dark:text-warm-200'">
            {{ m.name.slice(0, 1) }}
            <span class="kt-v2-panel absolute -right-0.5 -bottom-0.5 w-3 h-3 rounded-full flex items-center justify-center"><span v-if="chat.processingByTab[m.key]" class="i-carbon-circle-dash animate-spin text-[10px] text-aquamarine" /><StatusDot v-else :status="m.status" /></span>
          </span>
          <span class="min-w-0 flex-1">
            <span class="flex items-center gap-1 text-[13px] text-warm-800 dark:text-warm-100 truncate">
              <span class="truncate">{{ m.name }}</span>
              <span v-if="m.privileged" class="i-carbon-security text-[11px] text-iolite/80 shrink-0" />
            </span>
            <span class="block text-[11px] text-warm-400 truncate">{{ roleLabel(m) }}</span>
          </span>
        </button>
      </section>
    </div>
  </aside>
</template>

<script setup>
import { computed } from "vue"

import StatusDot from "@/components/common/StatusDot.vue"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { channelMembers } from "@/components/session-v2/model/sessionModel"
import { useV2T } from "@/components/session-v2/model/v2Strings"

/**
 * Discord-style member list of the open channel: every creature that posts
 * to or listens on it, grouped running / stopped, with its role. Clicking a
 * member opens that creature's own conversation.
 */
const props = defineProps({ channel: { type: String, required: true } })
defineEmits(["hide"])

const ctx = useSessionV2()
const chat = ctx.chat
const t = useV2T()

const members = computed(() => channelMembers(ctx.instance.value, props.channel, chat._rootSourceName))
const groups = computed(() =>
  [
    { id: "running", items: members.value.filter((m) => m.status !== "stopped") },
    { id: "stopped", items: members.value.filter((m) => m.status === "stopped") },
  ].filter((g) => g.items.length),
)

function roleLabel(m) {
  if (m.sends && m.listens) return t("members.both")
  return m.sends ? t("members.sends") : t("members.listens")
}
</script>
