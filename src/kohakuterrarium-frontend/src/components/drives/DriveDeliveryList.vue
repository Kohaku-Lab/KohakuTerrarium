<template>
  <div class="flex flex-col gap-1.5">
    <div v-if="!deliveries.length" class="text-[11px] text-warm-400 italic py-2">No delivery attempts recorded.</div>
    <div v-for="d in ordered" :key="d.delivery_id" class="rounded border px-2 py-1.5 text-[11px] flex flex-col gap-1" :class="rowClass(d)">
      <div class="flex items-center gap-2">
        <span :class="[stateDisplay(d.state).icon, stateDisplay(d.state).text, 'shrink-0']" />
        <span class="font-medium" :class="stateDisplay(d.state).text">{{ stateDisplay(d.state).label }}</span>
        <span class="text-warm-400">attempt {{ d.attempt }}</span>
        <span v-if="d.reason" class="text-warm-400">· {{ d.reason }}</span>
        <span class="flex-1" />
        <span class="text-warm-400 font-mono">{{ relativeTime(d.acknowledged_at || d.admitted_at || d.claimed_at || d.created_at) }}</span>
      </div>
      <div v-if="d.last_error" class="text-coral font-mono break-words">{{ d.last_error }}</div>
      <div v-if="d.state === 'dead_letter'" class="flex items-center gap-2">
        <span class="text-coral">Delivery exhausted retries and was dead-lettered.</span>
        <span class="flex-1" />
        <el-button v-if="canReplay" size="small" type="warning" plain :loading="replaying === d.delivery_id" @click="$emit('replay', d.delivery_id)"> Replay </el-button>
        <span v-else class="text-warm-400 italic">replay requires admin</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue"

import { relativeTime } from "@/utils/driveStatus"

const props = defineProps({
  deliveries: { type: Array, default: () => [] },
  canReplay: { type: Boolean, default: false },
  replaying: { type: String, default: null },
})

defineEmits(["replay"])

// Newest first — created_at descending, falling back to insertion order.
const ordered = computed(() =>
  [...props.deliveries].sort((a, b) => {
    const ta = Date.parse(a.created_at || "") || 0
    const tb = Date.parse(b.created_at || "") || 0
    return tb - ta
  }),
)

const STATE_DISPLAY = {
  pending: { label: "Pending", icon: "i-carbon-time", text: "text-warm-500" },
  claimed: { label: "Claimed", icon: "i-carbon-in-progress", text: "text-sapphire dark:text-sapphire-light" },
  admitted: { label: "Delivered", icon: "i-carbon-delivery", text: "text-sapphire dark:text-sapphire-light" },
  acknowledged: { label: "Acknowledged", icon: "i-carbon-checkmark", text: "text-aquamarine" },
  retry_wait: { label: "Retrying", icon: "i-carbon-renew", text: "text-amber-shadow dark:text-amber-light" },
  dead_letter: { label: "Dead-letter", icon: "i-carbon-warning-square", text: "text-coral" },
  superseded: { label: "Superseded", icon: "i-carbon-subtract-alt", text: "text-warm-400" },
}

function stateDisplay(state) {
  return STATE_DISPLAY[state] || { label: state, icon: "i-carbon-help", text: "text-warm-500" }
}

function rowClass(d) {
  if (d.state === "dead_letter") return "border-coral/40 bg-coral/5"
  if (d.state === "retry_wait") return "border-amber/40 bg-amber/5"
  return "border-warm-200 dark:border-warm-700"
}
</script>
