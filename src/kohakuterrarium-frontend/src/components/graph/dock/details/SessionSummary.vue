<template>
  <div class="flex flex-col gap-4">
    <template v-if="session">
      <header class="flex flex-col gap-1">
        <div class="flex items-center gap-2 min-w-0">
          <span class="i-carbon-network-4 text-base text-warm-500" />
          <span class="text-base font-semibold text-warm-800 dark:text-warm-100 truncate">{{ session.name }}</span>
        </div>
        <div class="text-xs text-warm-500">{{ t("graph.summary.counts", stats) }}</div>
      </header>
      <div class="flex flex-wrap gap-1.5">
        <button class="kt-graph-action kt-graph-action--primary" @click="$emit('quick-add', 'creature')"><span class="i-carbon-add" />{{ t("graph.action.addCreature") }}</button>
        <button class="kt-graph-action" @click="$emit('quick-add', 'channel')"><span class="i-carbon-add" />{{ t("graph.action.addChannel") }}</button>
        <button class="kt-graph-action" @click="actions.openChatTab(session.id)"><span class="i-carbon-launch" />{{ t("graph.action.openTab") }}</button>
        <button class="kt-graph-action" @click="actions.openInspector(session.id)"><span class="i-carbon-search-locate" />{{ t("graph.action.inspect") }}</button>
      </div>
      <section class="pt-2 border-t border-warm-200 dark:border-warm-700">
        <button class="kt-graph-action kt-graph-action--danger" @click="$emit('confirm', sessionStopRequest(view, actions, t, session))"><span class="i-carbon-power" />{{ t("graph.action.stopSession") }}</button>
      </section>
    </template>

    <template v-else>
      <header class="text-sm font-semibold text-warm-700 dark:text-warm-200">{{ t("graph.summary.allSessions", { n: view.model.sessions.length }) }}</header>
      <div class="flex flex-col gap-1">
        <button v-for="s in sessions" :key="s.id" class="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-warm-100 dark:hover:bg-warm-800 text-left" @click="view.setSession(s.id)">
          <span class="i-carbon-network-4 text-sm text-warm-500 shrink-0" />
          <span class="text-sm truncate flex-1">{{ s.name }}</span>
          <span class="text-[11px] font-mono text-warm-500"
            >{{ s.creatureIds.length }}<span v-if="s.busy" class="text-aquamarine"> · {{ s.busy }}●</span></span
          >
        </button>
        <div v-if="!sessions.length" class="text-xs text-warm-400">{{ t("graph.empty.noSessions") }}</div>
      </div>
      <button class="kt-graph-action self-start" @click="$emit('new-session')"><span class="i-carbon-add" />{{ t("graph.action.newSession") }}</button>
    </template>

    <GraphLegend :view="view" />
  </div>
</template>

<script setup>
import { computed } from "vue"

import GraphLegend from "@/components/graph/dock/GraphLegend.vue"
import { sessionStopRequest } from "@/components/graph/dock/details/deleteRequests"
import { useI18n } from "@/utils/i18n"

const props = defineProps({
  view: { type: Object, required: true },
  actions: { type: Object, required: true },
})
defineEmits(["quick-add", "confirm", "new-session"])

const { t } = useI18n()

const session = computed(() => props.view.model.sessions.find((s) => s.id === props.view.effectiveSessionId) || null)
const stats = computed(() => props.view.projection.stats)
const sessions = computed(() =>
  props.view.model.sessions.map((s) => ({
    ...s,
    busy: s.creatureIds.filter((id) => props.view.index.get(id)?.item?.status === "busy").length,
  })),
)
</script>
