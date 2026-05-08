<template>
  <div class="card p-4 flex flex-col gap-2.5 text-sm">
    <header class="flex items-center gap-2">
      <span class="w-2 h-2 rounded-full" :class="statusColor" />
      <span class="font-medium truncate text-warm-800 dark:text-warm-200">
        {{ instance.config_name }}
      </span>
      <GraphCounts :instance="instance" compact />
    </header>

    <dl class="grid grid-cols-[80px_1fr] gap-y-0.5 text-xs">
      <dt class="text-warm-500">model</dt>
      <dd class="truncate">{{ instance.model ?? "—" }}</dd>
      <dt class="text-warm-500">io</dt>
      <dd>{{ ioBindings }}</dd>
      <dt class="text-warm-500">uptime</dt>
      <dd>{{ uptime }}</dd>
    </dl>

    <footer class="flex gap-1.5 mt-1">
      <button class="flex-1 text-xs px-2 py-1.5 rounded border border-warm-300 dark:border-warm-700 hover:border-iolite hover:text-iolite flex items-center justify-center gap-1" @click="openSurface('chat')"><span class="i-carbon-chat" /> Chat</button>
      <button class="flex-1 text-xs px-2 py-1.5 rounded border border-warm-300 dark:border-warm-700 hover:border-iolite hover:text-iolite flex items-center justify-center gap-1" @click="openSurface('inspector')"><span class="i-carbon-radar" /> Inspect</button>
      <button class="text-xs px-2 py-1.5 rounded border border-warm-300 dark:border-warm-700 hover:border-coral hover:text-coral" :title="'Stop instance'" :disabled="stopping" @click="confirmStop = true">
        <span class="i-carbon-stop-filled" />
      </button>
    </footer>

    <ConfirmStopDialog v-if="confirmStop" :instance="instance" @close="confirmStop = false" @stopped="onStopped" />
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from "vue"

import ConfirmStopDialog from "@/components/shell/tabs/ConfirmStopDialog.vue"
import GraphCounts from "@/components/common/GraphCounts.vue"
import { useTabsStore } from "@/stores/tabs"

const props = defineProps({ instance: { type: Object, required: true } })
const tabs = useTabsStore()
const policyHint = ref(null)
const confirmStop = ref(false)
const stopping = ref(false)

onMounted(async () => {
  policyHint.value = await tabs.fetchPolicyHint(props.instance.id)
})

const statusColor = computed(
  () =>
    ({
      running: "bg-iolite",
      paused: "bg-amber",
      stopped: "bg-warm-400",
      errored: "bg-coral",
    })[props.instance.status] ?? "bg-warm-400",
)

const ioBindings = computed(() => {
  if (!policyHint.value || policyHint.value.length === 0) return "—"
  return policyHint.value.join(" · ")
})

const uptime = computed(() => {
  const start = props.instance.created_at ? new Date(props.instance.created_at).getTime() : null
  if (!start) return "—"
  const sec = Math.floor((Date.now() - start) / 1000)
  if (sec < 60) return `${sec}s`
  const m = Math.floor(sec / 60)
  if (m < 60) return `${m}m`
  const h = Math.floor(m / 60)
  return `${h}h ${m % 60}m`
})

async function openSurface(surface) {
  await tabs.openSurface(props.instance.id, surface, {
    config_name: props.instance.config_name,
    type: props.instance.type,
  })
}

function onStopped() {
  confirmStop.value = false
}
</script>
