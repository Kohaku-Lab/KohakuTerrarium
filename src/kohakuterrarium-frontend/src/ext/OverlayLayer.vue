<template>
  <div class="kt-overlay-layer" data-test="overlay-layer">
    <OverlayFrame v-for="overlay in shown" :key="overlay.key" :overlay="overlay" />
  </div>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted } from "vue"

import OverlayFrame from "@/ext/OverlayFrame.vue"
import { isOverlayVisible, overlays, syncViewport } from "@/ext/overlays"

const shown = computed(() => [...overlays.values()].filter((o) => isOverlayVisible(o.key)))

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
