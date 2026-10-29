<template>
  <WidgetSurfaceList :store="chat" :tab="chat.activeTab || ''" surface="side-panel" data-test="widgets-panel" />
</template>

<script setup>
import WidgetSurfaceList from "@/components/chat/widgets/WidgetSurfaceList.vue"
import { useChatStore } from "@/stores/chat"

/** Layout panel showing the side-panel widgets of the active chat tab. */
const chat = useChatStore()
</script>
