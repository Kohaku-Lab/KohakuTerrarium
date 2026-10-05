<template>
  <div class="h-full flex flex-col bg-warm-50 dark:bg-warm-900 overflow-hidden">
    <div class="flex items-center gap-2 px-3 py-2 border-b border-warm-200 dark:border-warm-700 shrink-0">
      <div class="i-carbon-network-4 text-sm text-warm-500" />
      <span class="text-xs font-medium text-warm-500 dark:text-warm-400 flex-1">{{ t("common.creatures") }}</span>
      <GraphCounts :instance="instance" />
    </div>

    <div class="flex-1 overflow-y-auto px-3 py-2 text-xs">
      <div v-if="creatures.length === 0" class="text-warm-400 py-6 text-center text-[11px]">No creatures yet.</div>

      <template v-if="creatures.length">
        <div v-for="group in creatureGroups" :key="group.key" class="mb-3" :data-group="group.key">
          <div class="flex items-center gap-1 text-[10px] uppercase tracking-wider text-warm-400 font-medium mb-1">
            <span>{{ group.label }}</span>
            <span v-if="group.showCount" class="tabular-nums">({{ group.creatures.length }})</span>
          </div>
          <div v-if="group.creatures.length === 0" class="px-2 py-1 text-[11px] text-warm-400">—</div>
          <div class="flex flex-col gap-1">
            <div v-for="creature in group.creatures" :key="creature.name" class="flex items-center gap-2 px-2 py-1.5 rounded cursor-pointer transition-colors hover:bg-warm-100 dark:hover:bg-warm-800" :class="[activeTab === creature.name ? 'bg-iolite/10' : '', group.key === 'outside' ? 'opacity-60' : '']" data-testid="creature-row" @click="onOpenTab(creature.name)">
              <StatusDot :status="creature.status" />
              <span class="font-medium text-warm-700 dark:text-warm-300 truncate">{{ creature.name }}</span>
              <span class="flex-1" />
              <span class="text-[10px] px-1.5 py-0.5 rounded" :class="creature.status === 'running' ? 'bg-aquamarine/10 text-aquamarine' : 'bg-warm-100 dark:bg-warm-800 text-warm-400'">
                {{ statusLabel(creature.status, creature.status) }}
              </span>
            </div>
          </div>
        </div>
      </template>

      <div v-if="channels.length">
        <div class="text-[10px] uppercase tracking-wider text-warm-400 font-medium mb-1">{{ t("common.channels") }}</div>
        <div class="flex flex-col gap-1">
          <div v-for="channel in channels" :key="channel.name" class="flex items-center gap-2 px-2 py-1.5 rounded cursor-pointer transition-colors hover:bg-warm-100 dark:hover:bg-warm-800" :class="activeTab === `ch:${channel.name}` ? 'bg-taaffeite/10' : ''" @click="onOpenTab(`ch:${channel.name}`)">
            <span class="w-2 h-2 rounded-sm shrink-0" :class="channel.type === 'broadcast' ? 'bg-taaffeite' : 'bg-aquamarine'" />
            <span class="font-medium text-warm-700 dark:text-warm-300 truncate">{{ channel.name }}</span>
            <span class="flex-1" />
            <span class="text-[10px] px-1.5 py-0.5 rounded bg-warm-100 dark:bg-warm-800 text-warm-400">{{ statusLabel(channel.type, channel.type) }}</span>
          </div>
        </div>
      </div>

      <div v-if="creatures.length === 1 && channels.length === 0" class="mt-3 text-[10px] text-warm-400 leading-relaxed">Solo graph. As soon as a second creature joins or a channel is wired, it shows up here.</div>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue"

import StatusDot from "@/components/common/StatusDot.vue"
import GraphCounts from "@/components/common/GraphCounts.vue"
import { useChatStore } from "@/stores/chat"
import { useI18n } from "@/utils/i18n"

const props = defineProps({
  instance: { type: Object, default: null },
})

const chat = useChatStore()
const { t, statusLabel } = useI18n()

const creatures = computed(() => props.instance?.creatures || [])
const channels = computed(() => props.instance?.channels || [])
const activeTab = computed(() => chat.activeTab)

const selectedChannel = computed(() => {
  const tab = activeTab.value
  if (typeof tab !== "string" || !tab.startsWith("ch:")) return null
  const name = tab.slice(3)
  return channels.value.find((ch) => ch.name === name) || null
})

function isChannelMember(creature, channelName) {
  return (creature.listen_channels || []).includes(channelName) || (creature.send_channels || []).includes(channelName)
}

const creatureGroups = computed(() => {
  const channel = selectedChannel.value
  if (!channel) {
    return [{ key: "all", label: t("common.creatures"), showCount: false, creatures: creatures.value }]
  }
  const inside = []
  const outside = []
  for (const creature of creatures.value) {
    ;(isChannelMember(creature, channel.name) ? inside : outside).push(creature)
  }
  return [
    { key: "inside", label: t("creatures.inChannel"), showCount: true, creatures: inside },
    { key: "outside", label: t("creatures.notInChannel"), showCount: true, creatures: outside },
  ]
})

function onOpenTab(tabKey) {
  chat.openTab(tabKey)
}
</script>
