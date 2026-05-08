<template>
  <span class="inline-flex items-center gap-1.5 text-[10px] font-mono text-warm-500 dark:text-warm-400 whitespace-nowrap" :title="title">
    <span class="inline-flex items-center gap-0.5">
      <span class="i-carbon-bot" />
      <span>{{ creatureCount }}</span>
    </span>
    <span v-if="showChannels" class="inline-flex items-center gap-0.5">
      <span class="i-carbon-flow-stream-reference" />
      <span>{{ channelCount }}</span>
    </span>
    <span v-if="showWires" class="inline-flex items-center gap-0.5">
      <span class="i-carbon-connect" />
      <span>{{ wireCount }}</span>
    </span>
  </span>
</template>

<script setup>
import { computed } from "vue"

const props = defineProps({
  instance: { type: Object, default: null },
  // ``compact`` hides channel/wire when zero so cards with limited
  // horizontal space don't look cluttered for solo creatures.
  compact: { type: Boolean, default: false },
})

const creatureCount = computed(() => props.instance?.creatures?.length || 0)
const channelCount = computed(() => props.instance?.channels?.length || 0)
const wireCount = computed(() => {
  const cs = props.instance?.creatures || []
  let n = 0
  for (const c of cs) {
    n += (c.listen_channels?.length || 0) + (c.send_channels?.length || 0)
  }
  return n
})

const showChannels = computed(() => !props.compact || channelCount.value > 0)
const showWires = computed(() => !props.compact || wireCount.value > 0)

const title = computed(() => `${creatureCount.value} creature${creatureCount.value === 1 ? "" : "s"} · ` + `${channelCount.value} channel${channelCount.value === 1 ? "" : "s"} · ` + `${wireCount.value} wire${wireCount.value === 1 ? "" : "s"}`)
</script>
