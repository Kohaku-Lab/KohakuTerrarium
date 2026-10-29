<template>
  <div class="h-full overflow-auto px-3 py-2" data-test="widget-surface-list">
    <div v-if="!items.length" class="h-full flex items-center justify-center text-xs text-warm-400">No widgets here yet.</div>
    <div v-for="item in items" :key="item.message.eventId" :data-widget-id="item.message.eventId" :class="{ 'widget-focused': item.message.eventId === focusId }">
      <div v-if="item.widget.title" class="text-[11px] font-medium text-warm-500 mt-2">{{ item.widget.title }}</div>
      <WidgetBlock :widget="item.widget" :host="item.host" mode="inline" />
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue"

import { effectiveSurface } from "../../../ext/widgets.js"
import WidgetBlock from "./WidgetBlock.vue"
import { widgetFromMessage } from "./widgetModel.js"

/** Every widget of one chat tab whose renderer draws it on ``surface`` (the side-panel host). */
const props = defineProps({
  store: { type: Object, required: true },
  tab: { type: String, default: "" },
  surface: { type: String, default: "side-panel" },
  focusId: { type: String, default: "" },
})

const hosts = new Map()

function hostFor(eventId) {
  if (!hosts.has(eventId)) {
    hosts.set(eventId, {
      send: (name, data) => props.store.submitWidgetMessage(props.tab, eventId, name, data || {}),
      setState: (patch) => props.store.setWidgetState(props.tab, eventId, patch),
      close: () => {},
      openInCanvas: () => {},
      theme: {},
    })
  }
  return hosts.get(eventId)
}

const items = computed(() => (props.store.messagesByTab?.[props.tab] || []).filter((m) => m.role === "widget" && effectiveSurface(m.renderer, m.surface) === props.surface).map((message) => ({ message, widget: widgetFromMessage(message), host: hostFor(message.eventId) })))
</script>

<style scoped>
.widget-focused {
  outline: 1px solid var(--color-border, rgba(160, 160, 160, 0.4));
  border-radius: 0.5rem;
}
</style>
