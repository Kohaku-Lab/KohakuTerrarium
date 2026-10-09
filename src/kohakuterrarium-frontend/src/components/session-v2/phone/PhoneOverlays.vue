<template>
  <ConversationSheet v-if="sheet?.kind === 'conversations'" />
  <PhoneMenuSheet v-else-if="sheet?.kind === 'menu'" @stop="$emit('stop')" />
  <PhoneModelSheet v-else-if="sheet?.kind === 'model'" :key="`model:${sheet.payload.agent || ''}`" :initial-agent="sheet.payload.agent || ''" />
  <PhoneAgentSheet v-else-if="sheet?.kind === 'agent'" :key="`agent:${sheet.payload.name}`" :name="sheet.payload.name" />

  <PhoneSheet v-if="ctx.widget.value && WIDGETS[ctx.widget.value]" full :title="t(`widget.${ctx.widget.value}.title`)" :close-label="t('close')" body-class="flex flex-col" :test-id="`phone-widget-${ctx.widget.value}`" @close="ctx.closeWidget()">
    <component :is="WIDGETS[ctx.widget.value]" :key="ctx.widget.value" mode="side" class="flex-1 min-h-0" />
  </PhoneSheet>

  <Teleport v-if="side" to="body">
    <div class="kt-v2 fixed inset-0 z-[1900]" data-test="phone-side">
      <PhonePage :title="sideTitle" :icon="SIDE_ICONS[side.kind] || ''" :back-label="t('phone.back')" :scroll="false" @back="ctx.closeSide()">
        <component :is="WIDGETS[side.payload.id]" v-if="side.kind === 'widget' && WIDGETS[side.payload.id]" mode="side" class="flex-1 min-h-0" />
        <component :is="SIDES[side.kind]" v-else-if="SIDES[side.kind]" :payload="side.payload" class="flex-1 min-h-0" />
      </PhonePage>
    </div>
  </Teleport>

  <Teleport v-if="ctx.addKind.value" to="body">
    <div class="kt-v2">
      <AddDialog :key="ctx.addKind.value" :kind="ctx.addKind.value" />
    </div>
  </Teleport>
</template>

<script setup>
import { computed } from "vue"

import AddDialog from "@/components/session-v2/add/AddDialog.vue"
import { SIDES, WIDGETS } from "@/components/session-v2/model/registry"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import ConversationSheet from "@/components/session-v2/phone/ConversationSheet.vue"
import PhoneAgentSheet from "@/components/session-v2/phone/PhoneAgentSheet.vue"
import PhoneMenuSheet from "@/components/session-v2/phone/PhoneMenuSheet.vue"
import PhoneModelSheet from "@/components/session-v2/phone/PhoneModelSheet.vue"
import PhonePage from "@/components/session-v2/phone/PhonePage.vue"
import PhoneSheet from "@/components/session-v2/phone/PhoneSheet.vue"

/**
 * Everything a phone session shows over its tabs: the one open sheet
 * (conversations, menu, model, agent), a widget as a full-height sheet, a
 * side view as a full-screen page, and the "Add to session" dialog.
 */
defineEmits(["stop"])

const SIDE_ICONS = {
  canvas: "i-carbon-image",
  subagent: "i-carbon-bot",
  peek: "i-carbon-chat",
  drive: "i-carbon-task",
  terminal: "i-carbon-terminal",
  graph: "i-carbon-network-3",
  widget: "i-carbon-apps",
}

const ctx = useSessionV2()
const t = useV2T()
const sheet = computed(() => ctx.sheet.value)
const side = computed(() => ctx.side.value)
const sideTitle = computed(() => (side.value?.kind === "widget" ? t(`widget.${side.value.payload.id}.title`) : t(`side.${side.value?.kind}.title`)))
</script>
