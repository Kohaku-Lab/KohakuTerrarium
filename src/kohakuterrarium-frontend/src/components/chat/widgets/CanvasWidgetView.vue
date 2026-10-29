<template>
  <div class="h-full min-h-0 overflow-auto" data-test="canvas-widget">
    <div v-if="!found" class="h-full flex items-center justify-center text-xs text-warm-400">This widget is no longer in the conversation.</div>
    <WidgetBlock v-else :widget="widget" :host="host" mode="inline" class="h-full" />
  </div>
</template>

<script setup>
import { computed } from "vue"

import WidgetBlock from "./WidgetBlock.vue"
import { findWidgetMessage, makeWidgetHost } from "./widgetHost.js"
import { widgetFromMessage } from "./widgetModel.js"

/** One widget drawn full size on the canvas, live from its chat message. */
const props = defineProps({
  store: { type: Object, required: true },
  eventId: { type: String, required: true },
  canvas: { type: Object, default: null },
})

const found = computed(() => findWidgetMessage(props.store, props.eventId))
const widget = computed(() => ({ ...widgetFromMessage(found.value.message), surface: "canvas" }))
const host = makeWidgetHost({
  store: props.store,
  tab: () => found.value?.tab || "",
  eventId: props.eventId,
  canvas: props.canvas,
})
</script>
