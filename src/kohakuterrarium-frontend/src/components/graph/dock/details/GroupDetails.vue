<template>
  <div class="flex flex-col gap-4">
    <header class="flex flex-col gap-1">
      <div class="flex items-center gap-2 min-w-0">
        <span :class="[groupIcon(group.kind), group.kind === 'control' ? 'text-iolite' : 'text-warm-500']" class="text-base" />
        <SiteChip v-if="group.kind === 'host'" :node-id="group.key" always-show />
        <span v-else class="text-base font-semibold text-warm-800 dark:text-warm-100 truncate">{{ groupTitle(group, t) }}</span>
      </div>
      <div class="text-xs text-warm-500">{{ t("graph.group.counts", { n: group.creatureIds.length, busy: group.busy }) }}</div>
    </header>

    <div class="flex flex-wrap gap-1.5">
      <button class="kt-graph-action" @click="view.toggleCollapse(group.id)"><span :class="group.collapsed ? 'i-carbon-expand-categories' : 'i-carbon-collapse-categories'" />{{ group.collapsed ? t("graph.group.expand") : t("graph.group.collapse") }}</button>
      <template v-if="session">
        <button class="kt-graph-action" @click="view.setSession(session.id)"><span class="i-carbon-zoom-in-area" />{{ t("graph.action.focusSession") }}</button>
        <button class="kt-graph-action" @click="actions.openChatTab(session.id)"><span class="i-carbon-launch" />{{ t("graph.action.openTab") }}</button>
      </template>
    </div>

    <section class="flex flex-col gap-1">
      <h4 class="kt-graph-section">{{ t("graph.section.members") }}</h4>
      <button v-for="m in members" :key="m.id" class="flex items-center gap-2 text-xs text-left hover:underline" @click="view.select('creature', m.id)">
        <span :class="statusStyle(m.status).text">{{ statusStyle(m.status).glyph }}</span>
        <span class="truncate">{{ m.name }}</span>
      </button>
    </section>

    <section v-if="session" class="pt-2 border-t border-warm-200 dark:border-warm-700">
      <button class="kt-graph-action kt-graph-action--danger" @click="$emit('confirm', sessionStopRequest(view, actions, t, session))"><span class="i-carbon-power" />{{ t("graph.action.stopSession") }}</button>
    </section>
  </div>
</template>

<script setup>
import { computed } from "vue"

import SiteChip from "@/components/cluster/SiteChip.vue"
import { groupIcon, groupTitle, statusStyle } from "@/components/graph/graphTheme"
import { sessionStopRequest } from "@/components/graph/dock/details/deleteRequests"
import { useI18n } from "@/utils/i18n"

const props = defineProps({
  view: { type: Object, required: true },
  actions: { type: Object, required: true },
  group: { type: Object, required: true },
})
defineEmits(["confirm"])

const { t } = useI18n()

const session = computed(() => (props.group.kind === "session" ? props.view.model.sessions.find((s) => s.id === props.group.key) || null : null))
const members = computed(() => props.group.creatureIds.map((id) => props.view.index.get(id)?.item).filter(Boolean))
</script>
