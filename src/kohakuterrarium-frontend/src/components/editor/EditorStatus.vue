<template>
  <div class="h-full flex flex-col bg-white dark:bg-warm-900 overflow-hidden">
    <!-- Tab bar -->
    <div class="flex items-center gap-0 border-b border-warm-200 dark:border-warm-700 shrink-0">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        class="px-3 py-1.5 text-[11px] font-medium transition-colors border-b-2 -mb-px"
        :class="
          activeTab === tab.key
            ? 'text-iolite dark:text-iolite-light border-iolite dark:border-iolite-light'
            : 'text-warm-400 hover:text-warm-600 dark:hover:text-warm-300 border-transparent'
        "
        @click="activeTab = tab.key"
      >
        {{ tab.label }}
        <span
          v-if="tab.key === 'jobs' && jobCount > 0"
          class="ml-1 px-1 py-0.5 rounded-full bg-amber/15 text-amber text-[9px] leading-none"
          >{{ jobCount }}</span
        >
      </button>
    </div>

    <!-- Tab content -->
    <div class="flex-1 overflow-y-auto p-2 text-xs">
      <!-- Session tab -->
      <div v-if="activeTab === 'session'" class="flex flex-col gap-1.5">
        <div class="flex items-center gap-2">
          <span class="text-warm-400 w-14 shrink-0">Session</span>
          <span class="text-warm-600 dark:text-warm-300 font-mono text-[10px] truncate">
            {{ chat.sessionInfo.sessionId || instance?.session_id || "--" }}
          </span>
        </div>
        <div class="flex items-center gap-2">
          <span class="text-warm-400 w-14 shrink-0">CWD</span>
          <span class="text-warm-600 dark:text-warm-300 font-mono text-[10px] truncate">
            {{ instance?.pwd || "--" }}
          </span>
        </div>
        <!-- Creatures list for terrariums -->
        <template v-if="instance?.type === 'terrarium'">
          <div class="mt-1 text-warm-400 text-[10px] uppercase tracking-wider">Creatures</div>
          <div
            v-for="c in instance.creatures"
            :key="c.name"
            class="flex items-center gap-1.5 px-1 cursor-pointer hover:bg-warm-100 dark:hover:bg-warm-800 rounded"
            @click="chat.openTab(c.name)"
          >
            <StatusDot :status="c.status" />
            <span class="text-warm-600 dark:text-warm-300">{{ c.name }}</span>
          </div>
          <div class="mt-1 text-warm-400 text-[10px] uppercase tracking-wider">Channels</div>
          <div
            v-for="ch in instance.channels || []"
            :key="ch.name"
            class="flex items-center gap-1.5 px-1 cursor-pointer hover:bg-warm-100 dark:hover:bg-warm-800 rounded"
            @click="chat.openTab('ch:' + ch.name)"
          >
            <span class="text-aquamarine font-bold shrink-0">&rarr;</span>
            <span class="text-warm-600 dark:text-warm-300">{{ ch.name }}</span>
          </div>
        </template>
      </div>

      <!-- Tokens tab -->
      <div v-else-if="activeTab === 'tokens'" class="flex flex-col gap-1.5">
        <div class="flex items-center gap-2">
          <span class="text-warm-400 w-14 shrink-0">In</span>
          <span class="text-warm-600 dark:text-warm-300 font-mono">{{
            formatTokens(totalUsage.prompt)
          }}</span>
          <span v-if="totalUsage.cached > 0" class="text-aquamarine font-mono text-[10px]"
            >(cache {{ formatTokens(totalUsage.cached) }})</span
          >
        </div>
        <div class="flex items-center gap-2">
          <span class="text-warm-400 w-14 shrink-0">Out</span>
          <span class="text-warm-600 dark:text-warm-300 font-mono">{{
            formatTokens(totalUsage.completion)
          }}</span>
        </div>
        <!-- Context bar -->
        <div v-if="maxContext > 0" class="mt-1">
          <div class="flex items-center justify-between mb-1">
            <span class="text-warm-400">Context</span>
            <span
              class="font-mono text-[10px]"
              :class="
                contextPct >= 80 ? 'text-coral' : contextPct >= 60 ? 'text-amber' : 'text-warm-500'
              "
              >{{ formatTokens(totalUsage.lastPrompt) }}/{{ formatTokens(maxContext) }} ({{
                contextPct
              }}%)</span
            >
          </div>
          <div
            class="relative w-full h-1.5 rounded-full bg-warm-100 dark:bg-warm-800 overflow-hidden"
          >
            <div
              class="h-full rounded-full transition-all duration-300"
              :class="
                contextPct >= 80 ? 'bg-coral' : contextPct >= 60 ? 'bg-amber' : 'bg-aquamarine'
              "
              :style="{ width: Math.min(contextPct, 100) + '%' }"
            />
            <div
              v-if="compactPct > 0"
              class="absolute top-0 h-full w-0.5 bg-amber opacity-60"
              :style="{ left: compactPct + '%' }"
            />
          </div>
        </div>
      </div>

      <!-- Jobs tab -->
      <div v-else-if="activeTab === 'jobs'">
        <div v-if="jobCount === 0" class="text-warm-400 py-2 text-center">No running jobs</div>
        <div v-else class="flex flex-col gap-1">
          <div
            v-for="(job, jobId) in chat.runningJobs"
            :key="jobId"
            class="flex items-center gap-1.5 px-1.5 py-1 rounded bg-amber/10 group"
          >
            <span class="w-1.5 h-1.5 rounded-full bg-amber kohaku-pulse shrink-0" />
            <span class="text-amber-shadow dark:text-amber-light font-mono truncate">{{
              job.name
            }}</span>
            <span class="flex-1" />
            <span class="text-warm-400 shrink-0">{{ chat.getJobElapsed(job) }}</span>
            <button
              class="text-warm-400 hover:text-coral transition-colors opacity-0 group-hover:opacity-100"
              title="Stop task"
              @click="stopTask(jobId)"
            >
              <span class="i-carbon-close text-[9px]" />
            </button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import StatusDot from "@/components/common/StatusDot.vue"
import { useChatStore } from "@/stores/chat"
import { agentAPI, terrariumAPI } from "@/utils/api"

const props = defineProps({
  instance: { type: Object, default: null },
})

const chat = useChatStore()
const activeTab = ref("session")

const tabs = [
  { key: "session", label: "Session" },
  { key: "tokens", label: "Tokens" },
  { key: "jobs", label: "Jobs" },
]

const jobCount = computed(() => Object.keys(chat.runningJobs).length)

const totalUsage = computed(() => {
  let prompt = 0,
    completion = 0,
    cached = 0,
    lastPrompt = 0
  for (const usage of Object.values(chat.tokenUsage)) {
    prompt += usage.prompt || 0
    completion += usage.completion || 0
    cached += usage.cached || 0
    if ((usage.lastPrompt || 0) > lastPrompt) lastPrompt = usage.lastPrompt || 0
  }
  return { prompt, completion, cached, lastPrompt }
})

const maxContext = computed(() => chat.sessionInfo.maxContext || props.instance?.max_context || 0)

const contextPct = computed(() => {
  if (!maxContext.value || !totalUsage.value.lastPrompt) return 0
  return Math.round((totalUsage.value.lastPrompt / maxContext.value) * 100)
})

const compactThreshold = computed(() => chat.sessionInfo.compactThreshold || 0)
const compactPct = computed(() => {
  if (!maxContext.value || !compactThreshold.value) return 0
  return Math.min(100, Math.round((compactThreshold.value / maxContext.value) * 100))
})

async function stopTask(jobId) {
  try {
    if (chat._instanceType === "terrarium") {
      const tab = chat.activeTab || "root"
      await terrariumAPI.stopCreatureTask(chat._instanceId, tab, jobId)
    } else {
      await agentAPI.stopTask(chat._instanceId, jobId)
    }
  } catch (err) {
    console.error("Failed to stop task:", err)
  }
}

function formatTokens(n) {
  if (!n) return "0"
  if (n >= 1000000) return (n / 1000000).toFixed(1) + "M"
  if (n >= 1000) return (n / 1000).toFixed(1) + "K"
  return String(n)
}
</script>
