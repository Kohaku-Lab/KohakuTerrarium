<template>
  <div class="space-y-3" data-test="overlays-settings">
    <p class="text-xs text-warm-500">Overlays are small windows that float above the app. Hide any of them here; the position and size you set are remembered.</p>
    <div v-for="overlay in list" :key="overlay.key" class="flex items-center justify-between gap-3 rounded border border-warm-200 px-3 py-2 dark:border-warm-700" data-test="overlay-row">
      <div class="min-w-0">
        <div class="truncate text-sm font-medium">{{ overlay.label }}</div>
        <div class="truncate text-[11px] text-warm-500">{{ overlay.pkg }}</div>
      </div>
      <div class="flex items-center gap-2">
        <el-button size="small" data-test="overlay-reset" @click="resetOverlay(overlay.key)"> Reset position </el-button>
        <el-switch :model-value="isOverlayVisible(overlay.key)" data-test="overlay-switch" @change="toggleOverlay(overlay.key)" />
      </div>
    </div>
    <div v-if="!list.length" class="text-xs text-warm-500" data-test="overlays-empty">No installed extension provides an overlay.</div>
  </div>
</template>

<script setup>
import { computed } from "vue"

import { isOverlayVisible, overlays, resetOverlay, toggleOverlay } from "@/ext/overlays"

const list = computed(() => [...overlays.values()])
</script>
