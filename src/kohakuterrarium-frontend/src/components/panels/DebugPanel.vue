<template>
  <div class="h-full flex flex-col bg-warm-50 dark:bg-warm-900 overflow-hidden">
    <!-- Horizontal tab bar -->
    <div
      class="flex items-center gap-0.5 px-2 h-7 border-b border-warm-200 dark:border-warm-700 shrink-0 text-[11px]"
    >
      <button
        v-for="t in tabs"
        :key="t.id"
        class="px-2 py-0.5 rounded transition-colors"
        :class="
          activeTab === t.id
            ? 'bg-iolite/10 text-iolite'
            : 'text-warm-500 hover:text-warm-700 dark:hover:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800'
        "
        @click="activeTab = t.id"
      >
        <div :class="t.icon" class="inline-block text-[12px] mr-1" />
        {{ t.label }}
      </button>
      <span class="flex-1" />
      <slot name="header-right" />
    </div>

    <div class="flex-1 min-h-0">
      <LogsTab v-if="activeTab === 'logs'" :instance="instance" />
      <TraceTab v-else-if="activeTab === 'trace'" :instance="instance" />
      <PromptTab v-else-if="activeTab === 'prompt'" :instance="instance" />
      <EventsTab v-else-if="activeTab === 'events'" :instance="instance" />
    </div>
  </div>
</template>

<script setup>
import { ref } from "vue"

import EventsTab from "./debug/EventsTab.vue"
import LogsTab from "./debug/LogsTab.vue"
import PromptTab from "./debug/PromptTab.vue"
import TraceTab from "./debug/TraceTab.vue"

defineProps({
  instance: { type: Object, default: null },
})

const tabs = [
  { id: "logs", label: "Logs", icon: "i-carbon-catalog" },
  { id: "trace", label: "Trace", icon: "i-carbon-flow-connection" },
  { id: "prompt", label: "Prompt", icon: "i-carbon-document" },
  { id: "events", label: "Events", icon: "i-carbon-event" },
]

const activeTab = ref("logs")
</script>
