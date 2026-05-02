<template>
  <div class="h-full overflow-y-auto p-4 text-sm">
    <section class="mb-6">
      <h3 class="text-warm-500 uppercase text-xs tracking-wider mb-2">Identity</h3>
      <dl class="grid grid-cols-[100px_1fr] gap-y-1">
        <dt class="text-warm-500">name</dt>
        <dd>{{ instance?.config_name ?? target }}</dd>
        <dt class="text-warm-500">kind</dt>
        <dd>{{ instance?.type ?? "—" }}</dd>
        <dt class="text-warm-500">id</dt>
        <dd class="font-mono text-xs truncate">{{ target }}</dd>
        <dt class="text-warm-500">config</dt>
        <dd class="truncate">{{ instance?.config_path ?? "—" }}</dd>
        <dt class="text-warm-500">pwd</dt>
        <dd class="truncate">{{ instance?.pwd ?? "—" }}</dd>
        <dt class="text-warm-500">model</dt>
        <dd>{{ model || "—" }}</dd>
        <dt class="text-warm-500">started</dt>
        <dd>{{ formatStarted }}</dd>
      </dl>
    </section>

    <section class="mb-6">
      <h3 class="text-warm-500 uppercase text-xs tracking-wider mb-2">
        IO bindings
        <span v-if="policyHint" class="text-warm-400 normal-case font-normal"> (informational) </span>
      </h3>
      <div v-if="policyHint === null" class="text-warm-400 italic text-xs">Policy hint unavailable</div>
      <div v-else-if="policyHint && policyHint.length > 0">
        <div class="flex flex-wrap gap-1.5">
          <span v-for="p in policyHint" :key="p" class="px-2 py-0.5 rounded text-[10px] uppercase tracking-wider bg-warm-100 dark:bg-warm-800 text-warm-600 dark:text-warm-400">
            {{ p }}
          </span>
        </div>
        <div class="text-xs text-warm-400 mt-2">
          {{ describePolicies }}
        </div>
      </div>
    </section>

    <section>
      <h3 class="text-warm-500 uppercase text-xs tracking-wider mb-2">State</h3>
      <dl class="grid grid-cols-[100px_1fr] gap-y-1">
        <dt class="text-warm-500">status</dt>
        <dd>{{ instance?.status ?? "—" }}</dd>
        <dt class="text-warm-500">jobs</dt>
        <dd>{{ status.runningJobs.length }} in flight</dd>
        <dt class="text-warm-500">tokens</dt>
        <dd>
          {{ status.tokenUsage.promptTokens + status.tokenUsage.completionTokens }} total
          <span v-if="status.tokenUsage.contextPercent" class="text-warm-400"> ({{ Math.round(status.tokenUsage.contextPercent) }}% ctx) </span>
        </dd>
        <dt class="text-warm-500">scratchpad</dt>
        <dd>{{ Object.keys(status.scratchpad).length }} key(s)</dd>
      </dl>
    </section>
  </div>
</template>

<script setup>
import { computed, onMounted, ref } from "vue"

import { useStatusStore } from "@/stores/status"
import { useTabsStore } from "@/stores/tabs"

const props = defineProps({
  target: { type: String, required: true },
  instance: { type: Object, default: null },
})

const status = useStatusStore()
const tabs = useTabsStore()
const policyHint = ref(undefined) // undefined = pending; null = error; [] = empty

onMounted(async () => {
  policyHint.value = await tabs.fetchPolicyHint(props.target)
})

const model = computed(() => status.sessionInfo.llmName || status.sessionInfo.model || props.instance?.model || "")

const formatStarted = computed(() => {
  const t = props.instance?.created_at ? new Date(props.instance.created_at) : status.sessionInfo.startTime ? new Date(status.sessionInfo.startTime) : null
  if (!t) return "—"
  return t.toLocaleString()
})

const POLICY_DESCRIPTIONS = {
  io: "Bidirectional input/output (chat works)",
  log: "Process log tail",
  observer: "Channel observation (terrariums)",
  trace: "Live event stream from session",
}

const describePolicies = computed(() => {
  if (!policyHint.value) return ""
  return policyHint.value.map((p) => `${p}: ${POLICY_DESCRIPTIONS[p] ?? "?"}`).join(" · ")
})
</script>
