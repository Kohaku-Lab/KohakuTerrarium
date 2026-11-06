<template>
  <div class="kt-overlay-window" :class="{ 'kt-overlay-window--through': through }" data-test="overlay-window">
    <div v-if="overlay && !through" class="kt-overlay-window__bar" data-test="overlay-window-bar" @pointerdown="onBarDown">
      <span class="kt-overlay-window__title">{{ overlay.label }}</span>
      <button type="button" class="kt-overlay-window__close" :aria-label="`Hide ${overlay.label}`" data-test="overlay-window-close" @pointerdown.stop @click="hideWindow()">×</button>
    </div>
    <div v-if="overlay" class="kt-overlay-window__body" :class="{ 'kt-overlay-window__body--through': through }">
      <component :is="overlay.component" />
    </div>
    <div v-else class="kt-overlay-window__notice" data-test="overlay-window-missing">
      {{ extState.loading ? "Loading…" : "This overlay is not available." }}
    </div>
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, provide, watch } from "vue"

import { hideOverlay, overlays } from "@/ext/overlays"
import { windowLabel } from "@/ext/overlayWindows"
import { installOverlayInput } from "@/ext/overlayInput"
import { desktopContextConnected, relayedWork } from "@/ext/desktopContext"
import { WORK_CONTEXT_KEY } from "@/ext/workContext"
import { OVERLAY_INJECT_KEY, OVERLAY_MODE_INJECT_KEY } from "@/ext/sdk/keys"
import { extState } from "@/ext/state"
import { ackOverlayWindow, closeOverlayWindow, presentOverlayWindow, startOverlayDrag } from "@/utils/hostBridge"

const props = defineProps({
  overlayKey: { type: String, required: true },
})

provide(OVERLAY_INJECT_KEY, props.overlayKey)
provide(OVERLAY_MODE_INJECT_KEY, "window")
provide(WORK_CONTEXT_KEY, relayedWork)

const params = new URLSearchParams(window.location.search)
const generation = params.get("kt_overlay_generation")
const expectedHost = params.get("kt_overlay_host")
const label = windowLabel(props.overlayKey)
const bound = computed(() => !generation || (desktopContextConnected.value && expectedHost === extState.hostKey))
const overlay = computed(() => (bound.value ? overlays.get(props.overlayKey) || null : null))
const through = computed(() => overlay.value?.options.clickThrough === true)
watch(
  () => [desktopContextConnected.value, extState.hostKey],
  () => {
    if (generation && (!desktopContextConnected.value || (extState.hostKey && expectedHost !== extState.hostKey))) void closeOverlayWindow(label)
  },
)

function hideWindow() {
  hideOverlay(props.overlayKey)
  return closeOverlayWindow(label)
}

function onBarDown(event) {
  if (event.button === 0) startOverlayDrag()
}

let disposeInput = () => {}
function ready(event) {
  if (generation && event.detail === props.overlayKey) void presentOverlayWindow(generation)
}
window.addEventListener("kt:overlay-content-ready", ready)

onMounted(() => {
  document.documentElement.classList.add("kt-overlay-window-page")
  ackOverlayWindow()
  disposeInput = installOverlayInput(
    () => through.value,
    () => overlay.value?.options.desktopPet === true,
  )
})
onBeforeUnmount(() => {
  disposeInput()
  window.removeEventListener("kt:overlay-content-ready", ready)
  document.documentElement.classList.remove("kt-overlay-window-page")
})
</script>

<style>
html.kt-overlay-window-page,
html.kt-overlay-window-page body,
html.kt-overlay-window-page #app {
  background: transparent !important;
}
</style>

<style scoped>
.kt-overlay-window {
  display: flex;
  flex-direction: column;
  width: 100vw;
  height: 100vh;
  overflow: hidden;
  color: var(--color-text, inherit);
}
.kt-overlay-window:not(.kt-overlay-window--through) {
  background: var(--color-surface, #fff);
  border: 1px solid var(--color-border, rgba(0, 0, 0, 0.15));
  border-radius: 10px;
}
.kt-overlay-window__bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 4px 6px 4px 10px;
  font-size: 12px;
  cursor: grab;
  user-select: none;
  border-bottom: 1px solid var(--color-border, rgba(0, 0, 0, 0.1));
}
.kt-overlay-window__title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.kt-overlay-window__close {
  width: 22px;
  height: 22px;
  line-height: 1;
  cursor: pointer;
  background: transparent;
  border: 0;
  border-radius: 4px;
  color: inherit;
}
.kt-overlay-window__body {
  flex: 1;
  min-height: 0;
  overflow: auto;
}
.kt-overlay-window__body--through {
  overflow: hidden;
}
.kt-overlay-window__notice {
  padding: 12px;
  font-size: 12px;
}
</style>
