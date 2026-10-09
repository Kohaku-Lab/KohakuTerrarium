<template>
  <PhoneSheet :title="t('phone.conversations')" :close-label="t('close')" test-id="phone-conversations" @close="ctx.closeSheet()">
    <section v-for="group in groups" :key="group.kind" class="pb-2">
      <div class="sticky top-0 z-[1] kt-v2-float flex items-center gap-2 pl-4 pr-1 h-10 text-[11px] font-semibold uppercase tracking-wider text-warm-500">
        <span class="flex-1">{{ t(group.kind === "creature" ? "phone.agents" : "phone.channels") }} · {{ group.rows.length }}</span>
        <button type="button" class="h-9 px-3 rounded-full flex items-center gap-1 normal-case tracking-normal text-xs text-iolite dark:text-iolite-light active:bg-iolite/10" :data-test="`phone-add-${group.kind}`" @click="ctx.openAdd(group.kind === 'creature' ? 'creature' : 'channel')"><span class="i-carbon-add" />{{ t(group.kind === "creature" ? "phone.addCreature" : "phone.addChannel") }}</button>
      </div>
      <button v-for="row in group.rows" :key="row.key" type="button" class="w-full min-h-14 flex items-center gap-3 px-4 py-2 text-left active:bg-warm-200/60 dark:active:bg-warm-800" :class="row.active ? 'bg-iolite/10' : ''" :aria-current="row.active ? 'true' : undefined" :data-test="`phone-conv-${row.key}`" @click="pick(row.key)">
        <span class="w-5 shrink-0 flex justify-center">
          <span v-if="row.kind === 'channel'" class="text-aquamarine font-semibold">#</span>
          <span v-else-if="row.busy" class="i-carbon-circle-dash animate-spin text-aquamarine" />
          <StatusDot v-else :status="row.status" />
        </span>
        <span class="flex-1 min-w-0">
          <span class="flex items-center gap-1.5 min-w-0">
            <span class="truncate text-[15px]" :class="row.active ? 'font-semibold text-iolite dark:text-iolite-light' : 'text-warm-800 dark:text-warm-100'">{{ row.name }}</span>
            <span v-if="row.privileged" class="i-carbon-security text-iolite/80 shrink-0" :title="t('privileged')" />
          </span>
          <span v-if="row.detail" class="block truncate text-xs text-warm-500" :class="row.kind === 'creature' ? 'font-mono' : ''">{{ row.detail }}</span>
        </span>
        <span v-if="row.unread" class="px-2 rounded-full bg-amber text-white text-[11px] leading-5 font-bold shrink-0">{{ row.unread }}</span>
        <span v-else-if="row.active" class="i-carbon-checkmark text-iolite shrink-0" />
      </button>
    </section>
  </PhoneSheet>
</template>

<script setup>
import { computed } from "vue"

import StatusDot from "@/components/common/StatusDot.vue"
import { conversationRows } from "@/components/session-v2/model/phone/phoneModel"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import PhoneSheet from "@/components/session-v2/phone/PhoneSheet.vue"

/** The phone's agent list: every agent (privileged first) then every channel, one scrolling column; a row opens that conversation. */
const ctx = useSessionV2()
const t = useV2T()

const rows = computed(() => conversationRows(ctx.instance.value, ctx.chat))
const groups = computed(() => ["creature", "channel"].map((kind) => ({ kind, rows: rows.value.filter((r) => r.kind === kind) })).filter((g) => g.rows.length || g.kind === "creature"))

function pick(key) {
  ctx.chat.openTab(key)
  ctx.setTab("chat")
  ctx.closeSheet()
}
</script>
