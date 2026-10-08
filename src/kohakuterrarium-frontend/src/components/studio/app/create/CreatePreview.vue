<template>
  <aside class="kt-v2-panel kt-v2-edge border-l h-full min-h-0 flex flex-col" data-test="create-preview">
    <div class="kt-v2-line border-b shrink-0 h-10 px-3 flex items-center gap-2">
      <span class="i-carbon-view text-warm-500" />
      <span class="text-[12px] font-semibold text-warm-700 dark:text-warm-200">{{ t("studioApp.create.preview") }}</span>
      <span v-if="loading" class="i-carbon-circle-dash animate-spin text-warm-400 text-xs" />
    </div>
    <div v-if="error" class="px-3 py-4 text-[12px] text-coral" role="alert" data-test="create-preview-error">{{ error }}</div>
    <div v-else-if="note" class="px-3 py-4 text-[12px] text-warm-500">{{ note }}</div>
    <template v-else-if="files.length">
      <div class="shrink-0 flex flex-wrap gap-1 px-3 pt-2.5">
        <button v-for="(f, i) in files" :key="f.path" type="button" class="px-2 h-6 rounded-md text-[11px] font-mono truncate max-w-full" :class="i === active ? 'bg-iolite text-white' : 'text-warm-600 dark:text-warm-300 hover:bg-warm-200/60 dark:hover:bg-warm-700/60'" :data-test="`create-file-${i}`" @click="active = i">{{ f.path }}</button>
      </div>
      <pre class="flex-1 min-h-0 overflow-auto m-3 mt-2 p-3 rounded-lg kt-v2-card text-[11.5px] leading-relaxed font-mono text-warm-800 dark:text-warm-100 whitespace-pre" data-test="create-file-content">{{ files[active]?.content || "" }}</pre>
      <div v-if="wiring" class="shrink-0 px-3 pb-3" data-test="create-wiring">
        <div class="text-[11px] uppercase tracking-wider text-warm-500 mb-1">{{ t("studioApp.create.wiring") }}</div>
        <pre class="p-2.5 rounded-lg kt-v2-card text-[11px] font-mono text-warm-700 dark:text-warm-200 whitespace-pre overflow-x-auto">{{ wiring }}</pre>
      </div>
    </template>
  </aside>
</template>

<script setup>
import { ref, watch } from "vue"

import { useI18n } from "@/utils/i18n"

/**
 * The files a create flow is about to write, one at a time, plus (for a
 * module) the config snippet that plugs it into a creature.
 */
const props = defineProps({
  files: { type: Array, default: () => [] },
  wiring: { type: String, default: "" },
  loading: { type: Boolean, default: false },
  error: { type: String, default: "" },
  note: { type: String, default: "" },
})

const { t } = useI18n()
const active = ref(0)

watch(
  () => props.files.map((f) => f.path).join("|"),
  () => (active.value = 0),
)
</script>
