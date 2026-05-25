<template>
  <div class="flex items-center gap-2 px-2 h-12 border-b border-warm-200 dark:border-warm-700 shrink-0 bg-white dark:bg-warm-900">
    <button class="w-10 h-10 flex items-center justify-center rounded text-warm-500 hover:text-iolite hover:bg-warm-100 dark:hover:bg-warm-800 transition-colors" :title="t('shell.rail.commandPalette')" @click="$emit('open-rail')">
      <div class="i-carbon-menu text-lg" />
    </button>
    <BrandMark v-if="!tabs.activeTab" class="w-7 h-7 rounded-full shrink-0" />
    <span class="text-sm font-medium text-warm-700 dark:text-warm-200 truncate flex-1">
      {{ title }}
    </span>
    <!-- Host-picker chip — replaces the old "force desktop mode"
         button which was a one-way trap on Android (no way back
         once switched).  Density auto-detect handles small screens
         without user intervention now. -->
    <HostStatusChip :show-label="false" @open="openHostPicker" />
  </div>
</template>

<script setup>
import { computed } from "vue"

import HostStatusChip from "@/components/host-picker/HostStatusChip.vue"
import BrandMark from "@/components/shell/BrandMark.vue"
import { useTabsStore } from "@/stores/tabs"
import { useI18n } from "@/utils/i18n"

defineEmits(["open-rail"])

const tabs = useTabsStore()
const { t } = useI18n()

const title = computed(() => tabs.activeTab?.label || "Kohaku Terrarium")

function openHostPicker() {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event("kt-open-host-picker"))
  }
}
</script>
