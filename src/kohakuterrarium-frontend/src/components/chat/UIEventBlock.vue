<template>
  <!--
    UIEventBlock: one legacy UI event (ask_text, confirm, selection,
    progress, notification, card) in the chat scroll. It renders through
    the widget registry (renderer ``kt.<kind>``) and turns a renderer's
    ``host.send(name, data)`` into the ``reply`` event the hosts already
    handle (``{actionId, values}``).
  -->
  <WidgetBlock :widget="widget" :host="host" mode="inline" />
</template>

<script setup>
import { computed } from "vue"

import WidgetBlock from "./widgets/WidgetBlock.vue"
import { widgetFromLegacyMessage } from "./widgets/widgetModel.js"

const props = defineProps({
  message: { type: Object, required: true },
  // Explicit override; when omitted the host-installed platform origin (or, in
  // the browser, ``window.location.origin``) resolves card Markdown links.
  origin: { type: String, default: null },
})

const emit = defineEmits(["reply"])

const widget = computed(() => widgetFromLegacyMessage(props.message))

const host = {
  send(name, data) {
    emit("reply", { actionId: name, values: data || {} })
  },
  setState() {},
  close() {},
  openInCanvas() {},
  theme: {},
  get origin() {
    return props.origin
  },
}
</script>
