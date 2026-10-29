<template>
  <WidgetSurfaceList :store="ctx.chat" :tab="ctx.chat.activeTab || ''" surface="side-panel" :focus-id="payload?.eventId || ''" data-test="v2-side-widgets" />
</template>

<script setup>
import WidgetSurfaceList from "@/components/chat/widgets/WidgetSurfaceList.vue"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"

/** Side-panel widgets of the focused chat; `payload.eventId` highlights one. */
defineProps({ payload: { type: Object, default: () => ({}) } })

const ctx = useSessionV2()
</script>
