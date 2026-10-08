<template>
  <nav v-if="!bottom" class="flex items-end gap-0.5 self-end" role="tablist" :aria-label="t('tabs.label')">
    <button v-for="s in SESSION_TABS" :key="s.id" role="tab" :aria-selected="active(s.id)" class="kt-v2-tab h-9 px-4 text-[13px] flex items-center gap-2 transition-colors" :class="active(s.id) ? 'kt-v2-tab--active text-warm-800 dark:text-warm-100 font-medium' : 'text-warm-500 hover:text-warm-800 dark:hover:text-warm-200'" :data-test="`v2-tab-${s.id}`" @click="ctx.setTab(s.id)"><span :class="[s.icon, active(s.id) ? 'text-iolite dark:text-iolite-light' : '']" />{{ t(`tab.${s.id}`) }}</button>
  </nav>
  <nav v-else class="kt-v2-chrome kt-v2-edge shrink-0 flex border-t pb-[env(safe-area-inset-bottom)]" role="tablist" :aria-label="t('tabs.label')">
    <button v-for="s in SESSION_TABS" :key="s.id" role="tab" :aria-selected="active(s.id)" class="flex-1 flex flex-col items-center gap-0.5 py-1.5 text-[11px]" :class="active(s.id) ? 'text-iolite dark:text-iolite-light' : 'text-warm-500'" :data-test="`v2-tab-${s.id}`" @click="ctx.setTab(s.id)"><span :class="s.icon" class="text-lg" />{{ t(`tab.${s.id}`) }}</button>
  </nav>
</template>

<script setup>
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { SESSION_TABS } from "@/components/session-v2/model/sessionModel"
import { useV2T } from "@/components/session-v2/model/v2Strings"

/** The session tabs: browser-style tabs fused into the page (desktop), or a bottom bar on phones (`bottom`). */
defineProps({ bottom: { type: Boolean, default: false } })

const ctx = useSessionV2()
const t = useV2T()
const active = (id) => ctx.tab.value === id
</script>
