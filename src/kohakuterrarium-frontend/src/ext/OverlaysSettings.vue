<template>
  <div class="space-y-3" data-test="overlays-settings">
    <p class="text-xs text-warm-500">Overlays are small windows that float above the app. Hide any of them here; the position and size you set are remembered.</p>
    <section v-if="pets.length" class="space-y-3 rounded-lg border border-warm-200 p-4 dark:border-warm-700" :aria-label="t('overlays.pet.title')" data-test="pet-selection">
      <h3 class="text-sm font-semibold">{{ t("overlays.pet.title") }}</h3>
      <p class="text-xs text-warm-500">{{ t("overlays.pet.hint") }}</p>
      <div class="flex flex-wrap items-end gap-4">
        <label class="flex min-w-48 flex-1 flex-col gap-2 text-sm"
          >{{ t("overlays.pet.type") }}
          <select :value="choice.selected || ''" class="min-h-10 rounded border border-warm-300 bg-transparent px-3 dark:border-warm-600" data-test="pet-type" @change="selectPet($event.target.value)">
            <option v-if="!pets.some((o) => o.key === choice.selected)" :value="choice.selected || ''" disabled>{{ t("overlays.pet.unavailable") }}</option>
            <option v-for="pet in pets" :key="pet.key" :value="pet.key">{{ pet.label }}</option>
          </select>
        </label>
        <label class="flex min-h-10 items-center gap-3 text-sm">{{ t("overlays.pet.show") }}<el-switch :model-value="choice.visible" :disabled="!pets.some((o) => o.key === choice.selected)" data-test="pet-switch" @change="setPetVisible" /></label>
      </div>
    </section>
    <div v-for="overlay in list" :key="overlay.key" class="rounded border border-warm-200 px-3 py-2 dark:border-warm-700" data-test="overlay-row">
      <div class="flex items-center justify-between gap-3">
        <div class="min-w-0">
          <div class="truncate text-sm font-medium">{{ overlay.label }}</div>
          <div class="truncate text-[11px] text-warm-500">{{ overlay.pkg }}</div>
        </div>
        <div class="flex items-center gap-2">
          <el-button size="small" data-test="overlay-reset" @click="reset(overlay.key)"> Reset position </el-button>
          <el-switch v-if="!isPetOverlay(overlay)" :model-value="isOverlayVisible(overlay.key)" data-test="overlay-switch" @change="toggleOverlay(overlay.key)" />
        </div>
      </div>
      <div v-if="canUseWindow(overlay) && !overlay.options.firstParty" class="mt-2 flex items-center justify-between gap-3 text-xs" data-test="overlay-window-row">
        <span>Show on the desktop, outside this window</span>
        <el-switch :model-value="overlay.options.desktopPet ? windowPreference(overlay.key) !== false : isWindowAllowed(overlay.key)" data-test="overlay-window-switch" @change="(on) => changeWindowAllowed(overlay, on)" />
      </div>
      <p v-if="windowFailure(overlay.key)" class="mt-2 text-xs text-gem-amber" role="status">{{ windowFailure(overlay.key) }} <button type="button" class="underline" @click="retryOverlayWindow(overlay.key)">Retry desktop</button></p>
      <details v-if="settingsFor(overlay)" class="mt-3 border-t border-warm-200 pt-3 dark:border-warm-700" data-test="overlay-settings" @toggle="(event) => (expanded[overlay.key] = event.target.open)">
        <summary class="cursor-pointer text-sm">{{ settingsFor(overlay).label }}</summary>
        <component :is="settingsFor(overlay).component" v-if="expanded[overlay.key]" :active="true" />
      </details>
    </div>
    <div v-if="!list.length" class="text-xs text-warm-500" data-test="overlays-empty">No installed extension provides an overlay.</div>
  </div>
</template>

<script setup>
import { computed, reactive } from "vue"

import { confirm } from "@/ext/sdk"
import { isOverlayVisible, isWindowAllowed, overlays, resetOverlay, setWindowAllowed, toggleOverlay, isPetOverlay, petSelection, selectPet, showOverlay, hideOverlay } from "@/ext/overlays"
import { resetNativeOverlay, retryOverlayWindow, wantsWindow, windowFailure } from "@/ext/overlayWindows"
import { windowPreference } from "@/ext/overlays"
import { overlayWindowsSupported } from "@/utils/hostBridge"
import { settingsSections } from "@/stores/settingsSectionRegistry"
import { useI18n } from "@/utils/i18n"

const { t } = useI18n()
const pets = computed(() => [...overlays.values()].filter(isPetOverlay))
const choice = computed(petSelection)
const list = computed(() => [...overlays.values()].filter((o) => !isPetOverlay(o) || o.key === choice.value.selected))
const expanded = reactive({})

function setPetVisible(visible) {
  if (visible) showOverlay(choice.value.selected)
  else hideOverlay(choice.value.selected)
}

function settingsFor(overlay) {
  const id = overlay.options.settingsSection
  // Manifest contributions exist before the lazy extension entry has loaded.
  const candidates = [...settingsSections.values()].filter((s) => s.id.startsWith(`ext:${overlay.pkg}/`))
  const section = id ? settingsSections.get(`ext:${overlay.pkg}/${id}`) : candidates.length === 1 ? candidates[0] : null
  return section && (!section.visible || section.visible()) ? section : null
}

function reset(key) {
  if (!resetNativeOverlay(key)) resetOverlay(key)
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
