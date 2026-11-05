<template>
  <div class="kt-overlay-layer" data-test="overlay-layer">
    <component v-for="overlay in controllers" :key="`controller:${overlay.key}`" :is="overlay.controller" />
    <OverlayFrame v-for="overlay in shown" :key="overlay.key" :overlay="overlay" />
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, watchEffect } from "vue"

import OverlayFrame from "@/ext/OverlayFrame.vue"
import { isOverlayVisible, overlays, syncViewport } from "@/ext/overlays"
import { windowActive } from "@/ext/overlayWindows"
import { runtime } from "@/ext/state"

// A native renderer does not mount ExtensionContribution in the main window.
// Load its entry here so its optional controller can own session transport.
watchEffect(() => {
  for (const overlay of overlays.values()) {
    if (overlay.pkg && isOverlayVisible(overlay.key)) void runtime.ensureLoaded(overlay.pkg).catch(() => {})
  }
})

const controllers = computed(() => [...overlays.values()].filter((o) => o.controller))

const shown = computed(() => [...overlays.values()].filter((o) => isOverlayVisible(o.key) && !windowActive(o.key)))

onMounted(() => {
  syncViewport()
  window.addEventListener("resize", syncViewport)
})
onBeforeUnmount(() => window.removeEventListener("resize", syncViewport))
</script>

<style scoped>
.kt-overlay-layer {
  position: fixed;
  inset: 0;
  z-index: 2500;
  pointer-events: none;
}
</style>
