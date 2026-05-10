<template>
  <div class="h-full w-full flex items-center justify-center p-6 text-secondary">
    <div class="max-w-sm flex flex-col items-center gap-3 text-center">
      <div class="i-carbon-screen text-4xl opacity-60" />
      <div class="text-base font-semibold text-warm-800 dark:text-warm-200">
        {{ t("density.needsLargerScreen.title") }}
      </div>
      <div class="text-sm text-warm-600 dark:text-warm-400">
        {{ message }}
      </div>
      <button class="mt-2 px-3 py-1.5 rounded-md bg-iolite text-white text-sm hover:bg-iolite/90 transition-colors" @click="forceRequired">
        {{ t("density.useDesktopMode") }}
      </button>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue"

import { useDensity } from "@/composables/useDensity"
import { useI18n } from "@/utils/i18n"

const props = defineProps({
  required: { type: String, default: "regular" },
  panelLabel: { type: String, default: "" },
})

const { setOverride } = useDensity()
const { t } = useI18n()

const message = computed(() => {
  const label = props.panelLabel || t("density.thisView")
  return t("density.needsLargerScreen.message", { panel: label })
})

function forceRequired() {
  // Pin to the required density (or higher). For "regular" we pin to
  // regular; user can later switch back to auto in settings.
  setOverride(props.required === "expansive" ? "expansive" : "regular")
}
</script>
