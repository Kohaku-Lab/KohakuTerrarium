<template>
  <div class="flex flex-col items-center gap-2 text-center select-none px-6" data-test="lab-empty">
    <BrandMark class="w-10 h-10 rounded-full opacity-90" />
    <h2 class="text-xl font-semibold text-warm-800 dark:text-warm-100">{{ t("lab.hero.title") }}</h2>
    <p class="max-w-[46ch] text-xs text-warm-500">{{ t("lab.hero.hint") }}</p>
    <button type="button" class="mt-1.5 h-8 px-3 rounded-lg text-xs font-medium bg-iolite text-white hover:bg-iolite-shadow flex items-center gap-1.5" data-test="lab-hero-new" @click="$emit('new', null)"><span class="i-carbon-add-large" />{{ t("lab.new.title") }}</button>
    <div v-if="starts.length" class="mt-1.5 flex flex-wrap justify-center gap-1.5" data-test="lab-quick-starts">
      <button v-for="s in starts" :key="s.configPath" type="button" class="kt-v2-edge kt-v2-card !rounded-lg h-7 px-2.5 border text-xs text-warm-700 dark:text-warm-200 hover:text-iolite hover:border-iolite/50 flex items-center gap-1.5" :title="s.configPath" :data-test="`lab-quick-${s.label}`" @click="$emit('new', s)"><span :class="s.kind === 'terrarium' ? 'i-carbon-network-4' : 'i-carbon-bot'" class="text-warm-400" />{{ s.label }}</button>
    </div>
  </div>
</template>

<script setup>
import BrandMark from "@/components/shell/BrandMark.vue"
import { useI18n } from "@/utils/i18n"

/**
 * The lab with nothing running: a start button, and quick starts from the
 * configs the latest saved sessions used. `new` carries the picked quick
 * start (`{configPath, kind}`) or null.
 */
defineProps({ starts: { type: Array, default: () => [] } })
defineEmits(["new"])

const { t } = useI18n()
</script>
