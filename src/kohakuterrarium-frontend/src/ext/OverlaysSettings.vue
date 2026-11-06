<template>
  <div class="space-y-3" data-test="overlays-settings">
    <p class="text-xs text-warm-500">Overlays are small windows that float above the app. Hide any of them here; the position and size you set are remembered.</p>
    <div v-for="overlay in list" :key="overlay.key" class="rounded border border-warm-200 px-3 py-2 dark:border-warm-700" data-test="overlay-row">
      <div class="flex items-center justify-between gap-3">
        <div class="min-w-0">
          <div class="truncate text-sm font-medium">{{ overlay.label }}</div>
          <div class="truncate text-[11px] text-warm-500">{{ overlay.pkg }}</div>
        </div>
        <div class="flex items-center gap-2">
          <el-button size="small" data-test="overlay-reset" @click="reset(overlay.key)"> Reset position </el-button>
          <el-switch :model-value="isOverlayVisible(overlay.key)" data-test="overlay-switch" @change="toggleOverlay(overlay.key)" />
        </div>
      </div>
      <div v-if="canUseWindow(overlay) && !overlay.options.firstParty" class="mt-2 flex items-center justify-between gap-3 text-xs" data-test="overlay-window-row">
        <span>Show on the desktop, outside this window</span>
        <el-switch :model-value="overlay.options.desktopPet ? windowPreference(overlay.key) !== false : isWindowAllowed(overlay.key)" data-test="overlay-window-switch" @change="(on) => changeWindowAllowed(overlay, on)" />
      </div>
      <p v-if="windowFailure(overlay.key)" class="mt-2 text-xs text-gem-amber" role="status">{{ windowFailure(overlay.key) }} <button type="button" class="underline" @click="retryOverlayWindow(overlay.key)">Retry desktop</button></p>
    </div>
    <div v-if="!list.length" class="text-xs text-warm-500" data-test="overlays-empty">No installed extension provides an overlay.</div>
  </div>
</template>

<script setup>
import { computed } from "vue"

import { confirm } from "@/ext/sdk"
import { isOverlayVisible, isWindowAllowed, overlays, resetOverlay, setWindowAllowed, toggleOverlay } from "@/ext/overlays"
import { resetNativeOverlay, retryOverlayWindow, wantsWindow, windowFailure } from "@/ext/overlayWindows"
import { windowPreference } from "@/ext/overlays"
import { overlayWindowsSupported } from "@/utils/hostBridge"

const list = computed(() => [...overlays.values()])

function reset(key) {
  resetOverlay(key)
  resetNativeOverlay(key)
}

function canUseWindow(overlay) {
  return wantsWindow(overlay) && overlayWindowsSupported()
}

async function changeWindowAllowed(overlay, on) {
  if (on && !overlay.options.desktopPet) {
    const approved = await confirm(`"${overlay.label}" from ${overlay.pkg} will draw its own always-on-top window on your desktop and can stay visible while this app is in the background. Allow it?`, "Show on the desktop")
    if (!approved) return
  }
  setWindowAllowed(overlay.key, on)
}
</script>
