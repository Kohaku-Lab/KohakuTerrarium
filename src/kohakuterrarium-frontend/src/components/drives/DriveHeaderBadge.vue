<template>
  <DriveCountBadges v-if="show" :counts="store.counts" clickable @badge-click="onClick" />
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, watch } from "vue"

import DriveCountBadges from "@/components/drives/DriveCountBadges.vue"
import { createVisibilityInterval } from "@/composables/useVisibilityInterval"
import { useDrivesStore } from "@/stores/drives"
import { fireOpenDrives } from "@/utils/layoutEvents"

const props = defineProps({
  instance: { type: Object, default: null },
})

const sessionId = computed(() => props.instance?.graph_id || props.instance?.id || "")
const store = useDrivesStore(sessionId.value || undefined)

// Invisible until the session actually has Drives — a zero-Drive session
// shows no new header chrome (byte-identical to today).
const show = computed(() => store.order.length > 0)

let poller = null

onMounted(() => {
  if (sessionId.value) start()
})

watch(sessionId, (id, prev) => {
  if (id === prev) return
  stop()
  if (id) start()
})

onBeforeUnmount(stop)

function start() {
  // Best-effort: a session without the Drive runtime simply 404s and the
  // badge stays hidden.
  store.load(sessionId.value)
  poller = createVisibilityInterval(() => store.reconcile(), 8000)
  poller.start()
}

function stop() {
  if (poller) {
    poller.stop()
    poller = null
  }
}

function onClick() {
  // Ask any open Drives panel for this session to focus. Never forces the
  // panel open (see the "no empty chrome" rule).
  fireOpenDrives({ sessionId: sessionId.value })
}
</script>
