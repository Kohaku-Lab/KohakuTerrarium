<template>
  <div class="h-full flex flex-col bg-warm-50 dark:bg-warm-900 overflow-hidden">
    <!-- Panel header -->
    <div
      class="flex items-center gap-2 px-3 py-2 border-b border-warm-200 dark:border-warm-700 shrink-0"
    >
      <div class="i-carbon-network-4 text-sm text-warm-500" />
      <span class="text-xs font-medium text-warm-500 dark:text-warm-400 flex-1">Creatures</span>
      <span
        v-if="creatures.length"
        class="text-[10px] font-mono px-1.5 py-0.5 rounded bg-warm-100 dark:bg-warm-800 text-warm-400"
        >{{ creatures.length }}</span
      >
    </div>

    <!-- Body: creature list + channels, list-only (no graph). -->
    <div class="flex-1 overflow-y-auto px-3 py-2 text-xs">
      <template v-if="isTerrarium">
        <div class="mb-3">
          <div class="text-[10px] uppercase tracking-wider text-warm-400 font-medium mb-1">
            Creatures
          </div>
          <div class="flex flex-col gap-1">
            <div
              v-for="c in creatures"
              :key="c.name"
              class="flex items-center gap-2 px-2 py-1.5 rounded cursor-pointer transition-colors hover:bg-warm-100 dark:hover:bg-warm-800"
              :class="activeTab === c.name ? 'bg-iolite/10' : ''"
              @click="onOpenTab(c.name)"
            >
              <StatusDot :status="c.status" />
              <span class="font-medium text-warm-700 dark:text-warm-300 truncate">{{
                c.name
              }}</span>
              <span class="flex-1" />
              <span
                class="text-[10px] px-1.5 py-0.5 rounded"
                :class="
                  c.status === 'running'
                    ? 'bg-aquamarine/10 text-aquamarine'
                    : 'bg-warm-100 dark:bg-warm-800 text-warm-400'
                "
                >{{ c.status }}</span
              >
            </div>
          </div>
        </div>

        <div v-if="channels.length">
          <div class="text-[10px] uppercase tracking-wider text-warm-400 font-medium mb-1">
            Channels
          </div>
          <div class="flex flex-col gap-1">
            <div
              v-for="ch in channels"
              :key="ch.name"
              class="flex items-center gap-2 px-2 py-1.5 rounded cursor-pointer transition-colors hover:bg-warm-100 dark:hover:bg-warm-800"
              :class="activeTab === 'ch:' + ch.name ? 'bg-taaffeite/10' : ''"
              @click="onOpenTab('ch:' + ch.name)"
            >
              <span
                class="w-2 h-2 rounded-sm shrink-0"
                :class="ch.type === 'broadcast' ? 'bg-taaffeite' : 'bg-aquamarine'"
              />
              <span class="font-medium text-warm-700 dark:text-warm-300 truncate">{{
                ch.name
              }}</span>
              <span class="flex-1" />
              <span
                class="text-[10px] px-1.5 py-0.5 rounded bg-warm-100 dark:bg-warm-800 text-warm-400"
                >{{ ch.type }}</span
              >
            </div>
          </div>
        </div>
      </template>

      <template v-else>
        <div class="text-warm-400 py-6 text-center text-[11px]">
          Not a terrarium — single creature runs standalone.
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue"

import StatusDot from "@/components/common/StatusDot.vue"
import { useChatStore } from "@/stores/chat"

const props = defineProps({
  instance: { type: Object, default: null },
})

const chat = useChatStore()

const isTerrarium = computed(() => props.instance?.type === "terrarium")
const creatures = computed(() => props.instance?.creatures || [])
const channels = computed(() => props.instance?.channels || [])
const activeTab = computed(() => chat.activeTab)

function onOpenTab(tabKey) {
  chat.openTab(tabKey)
}
</script>
