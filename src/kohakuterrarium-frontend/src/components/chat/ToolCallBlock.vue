<template>
  <div
    class="rounded-lg overflow-hidden min-w-0"
    :class="
      tc.kind === 'subagent'
        ? 'border border-taaffeite/25 dark:border-taaffeite/30'
        : 'border border-sapphire/20 dark:border-sapphire/25'
    "
  >
    <!-- Header -->
    <div
      role="button"
      tabindex="0"
      class="flex items-center gap-2 text-xs px-3 py-1.5 cursor-pointer select-none min-w-0"
      :class="
        tc.kind === 'subagent'
          ? 'bg-taaffeite/8 dark:bg-taaffeite/12'
          : 'bg-sapphire/8 dark:bg-sapphire/12'
      "
      @click="$emit('toggle')"
      @keydown.enter="$emit('toggle')"
      @keydown.space.prevent="$emit('toggle')"
    >
      <span :class="statusIcon.class">{{ statusIcon.icon }}</span>
      <span
        class="font-semibold font-mono shrink-0"
        :class="
          tc.kind === 'subagent'
            ? 'text-taaffeite dark:text-taaffeite-light'
            : 'text-iolite dark:text-iolite-light'
        "
      >
        {{ tc.kind === "subagent" ? `[sub] ${tc.name}` : tc.name }}
      </span>
      <span class="text-warm-400 dark:text-warm-500 truncate flex-1 font-mono min-w-0">{{
        formatArgs(tc.args)
      }}</span>
      <span v-if="elapsed" class="text-[10px] text-warm-400 font-mono shrink-0">{{ elapsed }}</span>
      <button
        v-if="canPromote"
        class="text-[10px] px-1.5 py-0.5 rounded bg-iolite/15 text-iolite hover:bg-iolite/25 shrink-0 font-mono"
        title="Move to background — agent continues working"
        aria-label="Move task to background"
        @click.stop="chat.promoteTask(tc.jobId || tc.id)"
      >
        → bg
      </button>
      <span
        v-if="tc.result || tc.tools_used?.length || tc.children?.length || tc.status === 'running'"
        class="i-carbon-chevron-down text-warm-400 transition-transform text-[10px] shrink-0"
        :class="{ 'rotate-180': expanded }"
      />
    </div>

    <!-- Expanded content -->
    <div
      v-if="expanded"
      class="border-t min-w-0"
      :class="
        tc.kind === 'subagent'
          ? 'border-taaffeite/15 dark:border-taaffeite/20'
          : 'border-sapphire/15 dark:border-sapphire/20'
      "
    >
      <template v-if="tc.kind === 'subagent'">
        <!-- Sub-agent nested tool calls (warm recessed bg — sapphire tool items pop against it) -->
        <div
          v-if="tc.children?.length"
          class="px-2 py-1.5 space-y-1 bg-warm-100 dark:bg-warm-800/80 border-b border-taaffeite/15 dark:border-taaffeite/20 max-h-48 overflow-y-auto overflow-x-hidden min-w-0"
        >
          <ToolCallBlock
            v-for="(child, i) in tc.children"
            :key="i"
            :tc="child"
            :expanded="childExpanded[i]"
            :depth="depth + 1"
            @toggle="toggleChild(i)"
          />
        </div>
        <!-- Sub-agent result (taaffeite tinted) -->
        <div v-if="tc.result && tc.status !== 'interrupted'" class="relative">
          <div
            class="px-3 py-2 bg-taaffeite/8 dark:bg-taaffeite/12 text-xs max-h-48 overflow-y-auto scroll-smooth sa-result"
          >
            <MarkdownRenderer :content="tc.result" />
          </div>
        </div>
        <div
          v-else-if="tc.status === 'interrupted'"
          class="px-3 py-2 text-xs text-amber dark:text-amber-light bg-amber/6 dark:bg-amber/10"
        >
          (interrupted)
        </div>
        <div
          v-else-if="tc.status === 'running'"
          class="px-3 py-2 text-xs text-warm-400 bg-taaffeite/4 dark:bg-taaffeite/6"
        >
          (running...)
        </div>
        <!-- Sub-agent stats bar (solid dark strip) -->
        <div
          v-if="tc.turns || tc.total_tokens || tc.duration || tc.status === 'running'"
          class="px-3 py-1 text-[10px] text-taaffeite-shadow dark:text-taaffeite-light font-mono border-t border-taaffeite/20 dark:border-taaffeite/25 bg-taaffeite/15 dark:bg-taaffeite/20 flex gap-3"
        >
          <template v-if="tc.status === 'running'">
            <span v-if="tc.children?.length">{{ tc.children.length }} tool calls</span>
            <span v-if="tc.total_tokens">{{ tc.total_tokens.toLocaleString() }} tokens</span>
            <span v-if="tc.prompt_tokens"
              >({{ tc.prompt_tokens.toLocaleString() }} in /
              {{ (tc.completion_tokens || 0).toLocaleString() }} out)</span
            >
            <span v-if="elapsed">{{ elapsed }}</span>
          </template>
          <template v-else>
            <span v-if="tc.turns">{{ tc.turns }} turns</span>
            <span v-if="tc.total_tokens">{{ tc.total_tokens.toLocaleString() }} tokens</span>
            <span v-if="tc.prompt_tokens"
              >({{ tc.prompt_tokens.toLocaleString() }} in /
              {{ (tc.completion_tokens || 0).toLocaleString() }} out)</span
            >
            <span v-if="tc.duration">{{ tc.duration.toFixed(1) }}s</span>
          </template>
        </div>
      </template>
      <template v-else>
        <!-- Tool raw output, scrollable accordion -->
        <div
          class="text-xs font-mono px-3 py-2 text-warm-500 dark:text-warm-400 whitespace-pre-wrap max-h-64 overflow-y-auto overflow-x-hidden bg-sapphire/4 dark:bg-sapphire/6 min-w-0 break-all"
        >
          {{ tc.result || "(no output)" }}
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import MarkdownRenderer from "@/components/common/MarkdownRenderer.vue"
import { useChatStore } from "@/stores/chat"

const props = defineProps({
  tc: { type: Object, required: true },
  expanded: { type: Boolean, default: false },
  depth: { type: Number, default: 0 },
})

const emit = defineEmits(["toggle"])
const chat = useChatStore()

// Track expanded state for child tool blocks
const childExpanded = reactive({})

function toggleChild(index) {
  childExpanded[index] = !childExpanded[index]
}

// Elapsed time - use store's _jobTick for consistent reactivity
const elapsed = computed(() => {
  if (props.tc.status === "running" && props.tc.startedAt) {
    // Reference _jobTick to re-evaluate every second
    void chat._jobTick
    const secs = Math.floor((Date.now() - props.tc.startedAt) / 1000)
    return secs > 0 ? `${secs}s` : ""
  }
  if (props.tc.status !== "running" && props.tc.duration) {
    return `${props.tc.duration.toFixed(1)}s`
  }
  return ""
})

// Auto-expand running sub-agents when children FIRST appear (one-shot)
const _didAutoExpand = ref(false)
watch(
  () => props.tc.children?.length,
  (len) => {
    if (
      len > 0 &&
      !_didAutoExpand.value &&
      props.tc.kind === "subagent" &&
      props.tc.status === "running" &&
      !props.expanded
    ) {
      _didAutoExpand.value = true
      emit("toggle")
    }
  },
)

// Show "→ bg" button for running direct tasks after 1 second
const canPromote = computed(() => {
  if (props.tc.status !== "running") return false
  const jobId = props.tc.jobId || props.tc.id
  const job = chat.runningJobs[jobId]
  if (!job || !job.promotable) return false
  // Show after 1 second elapsed
  void chat._jobTick
  const elapsed = Date.now() - (props.tc.startedAt || 0)
  return elapsed > 1000
})

const statusIcon = computed(() => {
  if (props.tc.status === "running") return { icon: "\u2699", class: "text-amber kohaku-pulse" }
  if (props.tc.status === "error") return { icon: "\u2717", class: "text-coral" }
  if (props.tc.status === "interrupted") return { icon: "\u25cb", class: "text-amber" }
  return { icon: "\u2713", class: "text-sage" }
})

function formatArgs(args) {
  if (!args) return ""
  if (typeof args === "string") return args.slice(0, 80)
  return Object.entries(args)
    .filter(([k, v]) => k !== "info" || v)
    .map(([k, v]) => {
      const val = typeof v === "string" && v.length > 50 ? v.slice(0, 50) + "..." : v
      return `${k}=${val}`
    })
    .join(" ")
}
</script>

<style scoped>
/* Fade hint at bottom when content is scrollable */
.sa-result {
  mask-image: linear-gradient(to bottom, black calc(100% - 24px), transparent 100%);
  -webkit-mask-image: linear-gradient(to bottom, black calc(100% - 24px), transparent 100%);
}
.sa-result:hover {
  mask-image: none;
  -webkit-mask-image: none;
}
</style>
