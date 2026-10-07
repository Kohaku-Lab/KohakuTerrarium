<template>
  <div class="flex flex-col gap-4">
    <header class="flex flex-col gap-1">
      <div class="flex items-center gap-2 min-w-0">
        <span class="text-base leading-none" :class="status.text">{{ status.glyph }}</span>
        <span class="text-base font-semibold text-warm-800 dark:text-warm-100 truncate">{{ creature.name }}</span>
        <span v-if="creature.privileged" class="i-carbon-security text-sm text-iolite" :title="t('graph.node.privileged')" />
        <span class="flex-1" />
        <SiteChip :node-id="creature.hostId" />
      </div>
      <div class="text-xs" :class="status.text">{{ t(`graph.status.${creature.status}`) }}</div>
    </header>

    <div class="flex flex-wrap gap-1.5">
      <button class="kt-graph-action kt-graph-action--primary" @click="$emit('open-chat')"><span class="i-carbon-chat" />{{ t("graph.action.chat") }}</button>
      <button v-if="creature.status === 'busy'" class="kt-graph-action" @click="actions.interrupt(creature)"><span class="i-carbon-stop-outline" />{{ t("graph.action.interrupt") }}</button>
      <button v-if="creature.status === 'stopped'" class="kt-graph-action" @click="actions.start(creature)"><span class="i-carbon-play" />{{ t("graph.action.start") }}</button>
      <button v-else class="kt-graph-action" @click="actions.stop(creature)"><span class="i-carbon-pause" />{{ t("graph.action.stop") }}</button>
      <button class="kt-graph-action" @click="actions.openChatTab(creature.sessionId, tabKey)"><span class="i-carbon-launch" />{{ t("graph.action.openTab") }}</button>
      <button class="kt-graph-action" @click="actions.openInspector(creature.sessionId)"><span class="i-carbon-search-locate" />{{ t("graph.action.inspect") }}</button>
    </div>

    <dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
      <dt class="text-warm-500">{{ t("graph.field.model") }}</dt>
      <dd class="font-mono text-warm-700 dark:text-warm-300 truncate">{{ creature.model || "—" }}</dd>
      <dt class="text-warm-500">{{ t("graph.field.config") }}</dt>
      <dd class="font-mono text-warm-700 dark:text-warm-300 truncate">{{ creature.configName || "—" }}</dd>
      <dt class="text-warm-500">{{ t("graph.field.session") }}</dt>
      <dd class="text-warm-700 dark:text-warm-300 truncate">{{ sessionName }}</dd>
      <dt class="text-warm-500">{{ t("graph.field.tools") }}</dt>
      <dd class="font-mono text-warm-700 dark:text-warm-300">{{ creature.tools }}</dd>
      <template v-if="creature.subagents.length">
        <dt class="text-warm-500">{{ t("graph.field.subagents") }}</dt>
        <dd class="flex flex-wrap gap-1">
          <span v-for="s in creature.subagents" :key="s" class="px-1.5 rounded bg-taaffeite/12 text-taaffeite font-mono">{{ s }}</span>
        </dd>
      </template>
    </dl>

    <section class="flex flex-col gap-1.5">
      <h4 class="kt-graph-section">{{ t("graph.section.channels") }}</h4>
      <div v-for="row in channelRows" :key="row.name" class="flex items-center gap-2 text-xs">
        <button class="font-mono text-aquamarine-shadow dark:text-aquamarine-light hover:underline truncate text-left flex-1" @click="selectChannel(row.name)">≋ {{ row.name }}</button>
        <button class="kt-graph-toggle" :class="row.send ? 'kt-graph-toggle--on' : ''" :title="t('graph.edge.mode.send')" @click="actions.setChannelMembership(creature, row.name, 'send', !row.send)">{{ t("graph.edge.sendShort") }}</button>
        <button class="kt-graph-toggle" :class="row.listen ? 'kt-graph-toggle--on' : ''" :title="t('graph.edge.mode.listen')" @click="actions.setChannelMembership(creature, row.name, 'listen', !row.listen)">{{ t("graph.edge.listenShort") }}</button>
      </div>
      <div v-if="!channelRows.length" class="text-xs text-warm-400">{{ t("graph.empty.noChannels") }}</div>
    </section>

    <section class="flex flex-col gap-1.5">
      <h4 class="kt-graph-section">{{ t("graph.section.wires") }}</h4>
      <div v-for="w in wiresOut" :key="w.id" class="flex items-center gap-2 text-xs">
        <span class="text-sapphire">◆→</span>
        <button class="truncate text-left flex-1 hover:underline" @click="view.select('edge', w.id)">
          {{ nameOf(w.target) }}<span v-if="w.withContent === false" class="text-warm-400"> · {{ t("graph.edge.ping") }}</span>
        </button>
        <button class="i-carbon-close text-warm-400 hover:text-coral" :title="t('graph.action.remove')" @click="actions.removeEdge(w)" />
      </div>
      <div v-for="w in wiresIn" :key="w.id" class="flex items-center gap-2 text-xs">
        <span class="text-sapphire">→◆</span>
        <button class="truncate text-left flex-1 hover:underline" @click="view.select('edge', w.id)">{{ t("graph.edge.from", { name: nameOf(w.source) }) }}</button>
      </div>
      <div v-if="!wiresOut.length && !wiresIn.length" class="text-xs text-warm-400">{{ t("graph.empty.noWires") }}</div>
    </section>

    <section v-if="parent || children.length" class="flex flex-col gap-1.5">
      <h4 class="kt-graph-section">{{ t("graph.section.lineage") }}</h4>
      <button v-if="parent" class="text-xs text-left hover:underline" @click="view.select('creature', parent.id)">↑ {{ parent.name }}</button>
      <button v-for="c in children" :key="c.id" class="text-xs text-left hover:underline" @click="view.select('creature', c.id)">↓ {{ c.name }}</button>
    </section>

    <section class="pt-2 border-t border-warm-200 dark:border-warm-700">
      <button class="kt-graph-action kt-graph-action--danger" @click="$emit('confirm', creatureDeleteRequest(view, actions, t, creature))"><span class="i-carbon-trash-can" />{{ t("graph.action.removeCreature") }}</button>
    </section>
  </div>
</template>

<script setup>
import { computed } from "vue"

import SiteChip from "@/components/cluster/SiteChip.vue"
import { statusStyle } from "@/components/graph/graphTheme"
import { creatureDeleteRequest } from "@/components/graph/dock/details/deleteRequests"
import { channelNodeId } from "@/utils/graph/data/model"
import { useI18n } from "@/utils/i18n"

const props = defineProps({
  view: { type: Object, required: true },
  actions: { type: Object, required: true },
  creature: { type: Object, required: true },
})
defineEmits(["open-chat", "confirm"])

const { t } = useI18n()

const status = computed(() => statusStyle(props.creature.status))
const sessionName = computed(() => props.view.model.sessions.find((s) => s.id === props.creature.sessionId)?.name || props.creature.sessionId)
const tabKey = computed(() => (props.creature.root ? "root" : props.creature.name))

const channelRows = computed(() => {
  const names = new Set([...props.creature.send, ...props.creature.listen])
  for (const ch of props.view.model.channels) if (ch.sessionId === props.creature.sessionId) names.add(ch.name)
  return [...names].sort().map((name) => ({
    name,
    send: props.creature.send.includes(name),
    listen: props.creature.listen.includes(name),
  }))
})

const wiresOut = computed(() => props.view.model.edges.filter((e) => e.kind === "wire" && e.source === props.creature.id))
const wiresIn = computed(() => props.view.model.edges.filter((e) => e.kind === "wire" && e.target === props.creature.id))
const parent = computed(() => props.view.model.creatures.find((c) => c.id === props.creature.parentId) || null)
const children = computed(() => props.view.model.creatures.filter((c) => c.parentId === props.creature.id))

function nameOf(id) {
  return props.view.model.creatures.find((c) => c.id === id)?.name || id
}

function selectChannel(name) {
  props.view.select("channel", channelNodeId(props.creature.sessionId, name))
}
</script>
