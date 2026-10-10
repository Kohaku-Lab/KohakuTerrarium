<template>
  <div class="kt-v2-canvas absolute inset-0 flex flex-col" data-test="lab-focus">
    <nav class="kt-v2-line shrink-0 h-11 flex items-center gap-1 px-3 border-b overflow-x-auto" :aria-label="t('lab.focus.sessions')">
      <button type="button" class="shrink-0 h-7 px-2.5 rounded-lg flex items-center gap-1.5 text-xs text-warm-600 dark:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800" data-test="lab-back" @click="$emit('back')"><span class="i-carbon-arrow-left" />{{ t("lab.focus.all") }}</button>
      <span class="kt-v2-edge shrink-0 h-4 mx-1 border-l" />
      <button v-for="s in sessions" :key="s.id" type="button" class="shrink-0 h-7 px-2.5 rounded-lg flex items-center gap-1.5 text-xs" :class="s.id === session.id ? 'bg-iolite/12 text-iolite dark:text-iolite-light font-medium' : 'text-warm-600 dark:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800'" :aria-current="s.id === session.id ? 'true' : undefined" :data-test="`lab-focus-chip-${s.id}`" @click="$emit('focus', s.id)">
        <span v-if="s.counts.busy" class="i-carbon-circle-dash animate-spin text-aquamarine" />
        <span v-else class="w-2 h-2 rounded-full" :class="statusStyle(s.status).dot" />
        {{ s.name }}
      </button>
      <span class="flex-1" />
      <button type="button" class="kt-v2-edge kt-v2-panel shrink-0 h-7 px-2.5 rounded-lg border text-xs text-warm-700 dark:text-warm-200 hover:border-iolite/50 flex items-center gap-1.5" data-test="lab-focus-inspector" @click="$emit('inspector', session)"><span class="i-carbon-radar" />{{ t("lab.menu.inspector") }}</button>
      <button type="button" class="shrink-0 h-7 px-3 rounded-lg text-xs bg-iolite text-white hover:bg-iolite-shadow flex items-center gap-1.5" data-test="lab-focus-open" @click="$emit('open', session)"><span class="i-carbon-chat" />{{ t("lab.session.open") }}</button>
    </nav>
    <div class="relative flex-1 min-h-0">
      <GraphSurface :key="session.id" store-key="lab-focus" :session-id="session.id" lock-session />
    </div>
  </div>
</template>

<script setup>
import { statusStyle } from "@/components/graph/graphTheme"
import GraphSurface from "@/components/graph/GraphSurface.vue"
import { useI18n } from "@/utils/i18n"

/** Inside one session, in place of the bench: the other running sessions as a chip strip, then that session's live graph. */
defineProps({
  session: { type: Object, required: true },
  sessions: { type: Array, required: true },
})
defineEmits(["back", "focus", "open", "inspector"])

const { t } = useI18n()
</script>
