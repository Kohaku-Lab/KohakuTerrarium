<template>
  <div class="h-full overflow-y-auto p-4 font-mono text-xs space-y-4">
    <!-- Tools in flight -->
    <section>
      <h3 class="text-warm-500 mb-1">
        Tools <span class="text-warm-400">({{ runningTools.length }} running)</span>
      </h3>
      <div v-if="runningTools.length === 0" class="text-warm-400 italic">No tools running.</div>
      <div v-for="job in runningTools" :key="job.jobId" class="bg-warm-100 dark:bg-warm-900 rounded p-2 mb-1">
        <div>
          <span class="text-warm-700 dark:text-warm-300">{{ job.name }}</span>
          <span class="text-warm-400"> · {{ job.elapsed }}s · {{ job.type }}</span>
        </div>
      </div>
    </section>

    <!-- Sub-agents in flight -->
    <section>
      <h3 class="text-warm-500 mb-1">
        Sub-agents <span class="text-warm-400">({{ runningSubagents.length }} in flight)</span>
      </h3>
      <div v-if="runningSubagents.length === 0" class="text-warm-400 italic">None.</div>
      <div v-for="job in runningSubagents" :key="job.jobId" class="bg-warm-100 dark:bg-warm-900 rounded p-2 mb-1">
        <span class="text-warm-700 dark:text-warm-300">{{ job.name }}</span>
        <span class="text-warm-400"> · {{ job.elapsed }}s</span>
      </div>
    </section>

    <!-- Token usage live -->
    <section>
      <h3 class="text-warm-500 mb-1">Tokens</h3>
      <dl class="grid grid-cols-[140px_1fr] gap-y-0.5">
        <dt class="text-warm-500">prompt</dt>
        <dd>{{ status.tokenUsage.promptTokens.toLocaleString() }}</dd>
        <dt class="text-warm-500">completion</dt>
        <dd>{{ status.tokenUsage.completionTokens.toLocaleString() }}</dd>
        <dt class="text-warm-500">cached</dt>
        <dd>{{ status.tokenUsage.cachedTokens.toLocaleString() }}</dd>
        <dt class="text-warm-500">context %</dt>
        <dd>
          <span class="inline-block w-32 h-2 bg-warm-200 dark:bg-warm-800 rounded mr-2 align-middle">
            <span class="block h-2 rounded bg-iolite" :style="{ width: Math.min(100, contextPercent) + '%' }" />
          </span>
          {{ contextPercent }}%
        </dd>
      </dl>
    </section>

    <!-- Scratchpad -->
    <section v-if="scratchpadKeys.length > 0">
      <h3 class="text-warm-500 mb-1">
        Scratchpad <span class="text-warm-400">({{ scratchpadKeys.length }} keys)</span>
      </h3>
      <dl class="grid grid-cols-[140px_1fr] gap-y-0.5">
        <template v-for="k in scratchpadKeys" :key="k">
          <dt class="text-warm-500 truncate">{{ k }}</dt>
          <dd class="truncate">{{ String(status.scratchpad[k]).slice(0, 80) }}</dd>
        </template>
      </dl>
    </section>

    <!-- Empty state when no chat WS bound to this target -->
    <section v-if="liveMismatch" class="text-warm-400 italic text-xs">
      Live data above reflects whichever creature is currently bound to the chat WebSocket. Open a chat tab for <code>{{ target }}</code> to see its live activity here.
    </section>
  </div>
</template>

<script setup>
import { computed } from "vue"

import { useStatusStore } from "@/stores/status"

const props = defineProps({
  target: { type: String, required: true },
  instance: { type: Object, default: null },
})

const status = useStatusStore()

const runningTools = computed(() => status.runningJobs.filter((j) => j.type === "tool"))
const runningSubagents = computed(() => status.runningJobs.filter((j) => j.type === "subagent"))

const contextPercent = computed(() => Math.round(status.tokenUsage.contextPercent || 0))

const scratchpadKeys = computed(() => Object.keys(status.scratchpad))

const liveMismatch = computed(() => {
  if (!status.sessionInfo.sessionId) return true
  return status.sessionInfo.sessionId !== props.target
})
</script>
