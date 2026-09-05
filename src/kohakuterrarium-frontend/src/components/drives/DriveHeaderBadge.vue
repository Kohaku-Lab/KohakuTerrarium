<template>
  <DriveCountBadges v-if="show" :counts="store.counts" clickable @badge-click="onClick" />
  <el-drawer v-model="drawerOpen" direction="rtl" size="min(760px, 94vw)" :with-header="false" append-to-body class="drive-drawer" data-testid="drive-drawer">
    <DrivesPanel v-if="drawerOpen" :instance="instance" />
  </el-drawer>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue"

import DriveCountBadges from "@/components/drives/DriveCountBadges.vue"
import DrivesPanel from "@/components/panels/DrivesPanel.vue"
import { createVisibilityInterval } from "@/composables/useVisibilityInterval"
import { useDrivesStore } from "@/stores/drives"
import { LAYOUT_EVENTS, fireOpenDrives, onLayoutEvent } from "@/utils/layoutEvents"

const props = defineProps({
  instance: { type: Object, default: null },
})

const sessionId = computed(() => props.instance?.graph_id || props.instance?.id || "")
const store = useDrivesStore(sessionId.value || undefined)

// Invisible until the session actually has Drives — a zero-Drive session
// shows no new header chrome (byte-identical to today).
const show = computed(() => store.order.length > 0)
const drawerOpen = ref(false)

let poller = null
let unsubDrawer = () => {}

onMounted(() => {
  if (sessionId.value) start()
  // Other surfaces (the compact Creature State tab) ask this host to open
  // the full panel as a drawer; claiming the event tells them it is handled.
  unsubDrawer = onLayoutEvent(LAYOUT_EVENTS.OPEN_DRIVES_DRAWER, async (evt) => {
    const detail = evt?.detail || {}
    if (detail.sessionId && detail.sessionId !== sessionId.value) return
    evt?.preventDefault?.()
    drawerOpen.value = true
    // The drawer's panel mounts on the next tick and resets the store as it
    // loads, so the deep link is handed to it only once it is listening.
    if (detail.driveId) {
      await nextTick()
      fireOpenDrives(detail)
    }
  })
})

watch(sessionId, (id, prev) => {
  if (id === prev) return
  stop()
  drawerOpen.value = false
  if (id) start()
})

onBeforeUnmount(() => {
  stop()
  unsubDrawer()
})

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
  // An open Drives panel for this session focuses itself and claims the
  // event; otherwise the badge is the way in, so open the panel as a drawer.
  const handled = fireOpenDrives({ sessionId: sessionId.value })
  if (!handled) drawerOpen.value = true
}

defineExpose({ drawerOpen })
</script>
