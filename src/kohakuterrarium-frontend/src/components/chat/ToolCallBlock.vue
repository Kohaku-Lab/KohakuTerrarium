<template>
  <div class="rounded-lg overflow-hidden min-w-0" :class="tc.kind === 'subagent' ? 'border border-taaffeite/25 dark:border-taaffeite/30' : 'border border-sapphire/20 dark:border-sapphire/25'">
    <!-- Header -->
    <div role="button" tabindex="0" :aria-expanded="expanded" :aria-label="`${tc.kind === 'subagent' ? 'Sub-agent' : 'Tool'} ${tc.name}`" class="flex items-center gap-2 text-xs px-3 py-1.5 cursor-pointer select-none min-w-0" :class="tc.kind === 'subagent' ? 'bg-taaffeite/8 dark:bg-taaffeite/12' : 'bg-sapphire/8 dark:bg-sapphire/12'" @click="$emit('toggle')" @keydown.enter="$emit('toggle')" @keydown.space.prevent="$emit('toggle')">
      <span :class="statusIcon.class">{{ statusIcon.icon }}</span>
      <span class="font-semibold font-mono shrink-0" :class="tc.kind === 'subagent' ? 'text-taaffeite dark:text-taaffeite-light' : 'text-iolite dark:text-iolite-light'">
        {{ tc.kind === "subagent" ? `[sub] ${tc.name}` : tc.name }}
      </span>
      <span class="text-warm-400 dark:text-warm-500 truncate flex-1 font-mono min-w-0">{{ formatArgs(tc.args) }}</span>
      <span v-if="elapsed" class="text-[10px] text-warm-400 font-mono shrink-0">{{ elapsed }}</span>
      <button v-if="canPromote" class="text-[10px] px-1.5 py-0.5 rounded bg-iolite/15 text-iolite hover:bg-iolite/25 shrink-0 font-mono" title="Move to background — agent continues working" aria-label="Move task to background" @click.stop="chat.promoteTask(tc.jobId || tc.id)">→ bg</button>
      <span v-if="tc.result || tc.tools_used?.length || tc.children?.length || tc.status === 'running'" class="i-carbon-chevron-down text-warm-400 transition-transform text-[10px] shrink-0" :class="{ 'rotate-180': expanded }" />
    </div>

    <!-- Expanded content -->
    <div v-if="expanded" class="border-t min-w-0" :class="tc.kind === 'subagent' ? 'border-taaffeite/15 dark:border-taaffeite/20' : 'border-sapphire/15 dark:border-sapphire/20'">
      <template v-if="tc.kind === 'subagent'">
        <!-- Sub-agent nested tool calls (warm recessed bg — sapphire tool items pop against it) -->
        <div v-if="tc.children?.length" ref="childrenEl" class="px-2 py-1.5 space-y-1 bg-warm-100 dark:bg-warm-800/80 border-b border-taaffeite/15 dark:border-taaffeite/20 max-h-48 overflow-y-auto overflow-x-hidden min-w-0" @scroll="onChildrenScroll">
          <ToolCallBlock v-for="(child, i) in tc.children" :key="i" :tc="child" :expanded="childExpanded[i]" :depth="depth + 1" @toggle="toggleChild(i)" />
        </div>
        <!-- Sub-agent result (taaffeite tinted) -->
        <div v-if="tc.result && tc.status !== 'interrupted'" class="relative">
          <div ref="resultEl" class="px-3 py-2 bg-taaffeite/8 dark:bg-taaffeite/12 text-xs max-h-48 overflow-y-auto scroll-smooth sa-result" @scroll="onResultScroll">
            <template v-if="tc.resultParts?.length">
              <div class="flex flex-col gap-2">
                <template v-for="(part, i) in tc.resultParts" :key="i">
                  <MarkdownRenderer v-if="part.type === 'text'" :content="part.text || ''" />
                  <img v-else-if="part.type === 'image_url'" :src="part.image_url?.url" class="tool-inline-image" />
                </template>
              </div>
            </template>
            <MarkdownRenderer v-else :content="tc.result" />
          </div>
        </div>
        <div v-else-if="tc.status === 'interrupted'" class="px-3 py-2 text-xs text-amber dark:text-amber-light bg-amber/6 dark:bg-amber/10">(interrupted)</div>
        <div v-else-if="tc.status === 'running'" class="px-3 py-2 text-xs text-warm-400 bg-taaffeite/4 dark:bg-taaffeite/6">(running...)</div>
        <!-- Sub-agent stats bar (solid dark strip) -->
        <div v-if="tc.turns || tc.total_tokens || tc.duration || tc.status === 'running'" class="px-3 py-1 text-[10px] text-taaffeite-shadow dark:text-taaffeite-light font-mono border-t border-taaffeite/20 dark:border-taaffeite/25 bg-taaffeite/15 dark:bg-taaffeite/20 flex gap-3">
          <template v-if="tc.status === 'running'">
            <span v-if="tc.children?.length">{{ tc.children.length }} tool calls</span>
            <span v-if="tc.total_tokens">{{ tc.total_tokens.toLocaleString() }} tokens</span>
            <span v-if="tc.prompt_tokens">({{ tc.prompt_tokens.toLocaleString() }} in / {{ (tc.completion_tokens || 0).toLocaleString() }} out)</span>
            <span v-if="elapsed">{{ elapsed }}</span>
          </template>
          <template v-else>
            <span v-if="tc.turns">{{ tc.turns }} turns</span>
            <span v-if="tc.total_tokens">{{ tc.total_tokens.toLocaleString() }} tokens</span>
            <span v-if="tc.prompt_tokens">({{ tc.prompt_tokens.toLocaleString() }} in / {{ (tc.completion_tokens || 0).toLocaleString() }} out)</span>
            <span v-if="tc.duration">{{ tc.duration.toFixed(1) }}s</span>
          </template>
        </div>

        <!-- Sub-agent inner conversation (read) + send to a live run -->
        <div class="border-t border-taaffeite/15 dark:border-taaffeite/20">
          <button class="w-full flex items-center gap-2 px-3 py-1.5 text-[11px] text-taaffeite-shadow dark:text-taaffeite-light bg-taaffeite/6 dark:bg-taaffeite/8 hover:bg-taaffeite/10 dark:hover:bg-taaffeite/14 min-w-0" @click.stop="toggleConversation">
            <span class="i-carbon-chat text-[11px] shrink-0" />
            <span>{{ t("chat.subagent.conversation") }}</span>
            <span class="flex-1" />
            <span class="i-carbon-chevron-down text-[10px] transition-transform shrink-0" :class="{ 'rotate-180': convOpen }" />
          </button>
          <div v-if="convOpen" class="m-2 rounded overflow-hidden bg-taaffeite/6 dark:bg-taaffeite/10 border border-taaffeite/20 dark:border-taaffeite/25 flex flex-col gap-2 p-2 min-w-0">
            <div v-if="convLoading" class="text-[11px] text-warm-400">{{ t("common.loading") }}</div>
            <div v-else-if="convError" class="text-[11px] text-coral">{{ convError }}</div>
            <template v-else>
              <div class="max-h-72 overflow-y-auto flex flex-col gap-2 min-w-0">
                <div v-for="(item, i) in convBlocks" :key="i" class="min-w-0">
                  <!-- system: collapsed disclosure — never dumped inline -->
                  <template v-if="item.kind === 'system'">
                    <button class="w-full flex items-center gap-1.5 text-left text-[10px] text-warm-400 hover:text-warm-500" @click.stop="toggleSystem(i)">
                      <span class="i-carbon-chevron-right text-[9px] transition-transform shrink-0" :class="{ 'rotate-90': expandedSystem.has(i) }" />
                      <span class="font-mono uppercase">system</span>
                      <span class="italic">prompt ({{ item.text.length }} chars)</span>
                    </button>
                    <pre v-if="expandedSystem.has(i)" class="mt-1 font-mono whitespace-pre-wrap break-all max-h-40 overflow-y-auto bg-warm-100/70 dark:bg-warm-900/50 rounded px-2 py-1 text-[10px] text-warm-500 dark:text-warm-400">{{ item.text }}</pre>
                  </template>

                  <!-- user bubble (right-aligned, chat-style) -->
                  <div v-else-if="item.kind === 'user'" class="ml-auto max-w-[85%] rounded-lg bg-warm-100 dark:bg-warm-800/80 border border-warm-200/60 dark:border-warm-700/60 px-2.5 py-1.5 min-w-0">
                    <div class="text-[9px] uppercase tracking-wide text-warm-400 mb-0.5">user</div>
                    <div v-if="item.parts" class="flex flex-col gap-1 text-body">
                      <template v-for="(part, pi) in item.parts" :key="pi">
                        <MarkdownRenderer v-if="part.type === 'text' && part.text" :content="part.text" />
                        <img v-else-if="part.type === 'image_url'" :src="part.image_url?.url" class="tool-inline-image" />
                      </template>
                    </div>
                    <div v-else class="text-body">
                      <MarkdownRenderer :content="item.content" />
                    </div>
                  </div>

                  <!-- assistant bubble + real tool-call accordions -->
                  <div v-else class="max-w-[92%] min-w-0">
                    <div class="text-[9px] uppercase tracking-wide text-warm-400 mb-0.5">assistant</div>
                    <div v-if="item.parts" class="flex flex-col gap-1 text-body">
                      <template v-for="(part, pi) in item.parts" :key="pi">
                        <MarkdownRenderer v-if="part.type === 'text' && part.text" :content="part.text" />
                        <img v-else-if="part.type === 'image_url'" :src="part.image_url?.url" class="tool-inline-image" />
                      </template>
                    </div>
                    <div v-else-if="item.content" class="text-body">
                      <MarkdownRenderer :content="item.content" />
                    </div>
                    <div v-if="item.toolCalls.length" class="flex flex-col gap-1.5 mt-1.5 min-w-0">
                      <ToolCallBlock v-for="call in item.toolCalls" :key="call.id" :tc="call" :depth="depth + 1" :expanded="convToolExpanded.has(call.id)" @toggle="toggleConvTool(call.id)" />
                    </div>
                  </div>
                </div>
                <div v-if="!convBlocks.length" class="text-[11px] text-warm-400 italic">{{ t("chat.subagent.empty") }}</div>
              </div>
              <div v-if="canReceive" class="flex items-end gap-2 pt-1 border-t border-taaffeite/15 dark:border-taaffeite/20">
                <textarea v-model="sendText" rows="1" :placeholder="t('chat.subagent.placeholder')" class="flex-1 min-w-0 resize-none rounded border border-warm-200 dark:border-warm-700 bg-warm-50 dark:bg-warm-950 px-2 py-1 text-[11px] focus:outline-none focus:border-taaffeite" @keydown.enter.exact.prevent="submitSend" />
                <button class="text-[11px] px-2 py-1 rounded bg-taaffeite/20 text-taaffeite-shadow dark:text-taaffeite-light hover:bg-taaffeite/30 disabled:opacity-50 shrink-0" :disabled="sending || !sendText.trim()" @click.stop="submitSend">
                  {{ t("chat.subagent.send") }}
                </button>
              </div>
              <div v-else class="text-[10px] text-warm-400 italic">{{ t("chat.subagent.readOnly") }}</div>
            </template>
          </div>
        </div>
      </template>
      <template v-else>
        <!-- Tool raw output, scrollable accordion -->
        <div ref="resultEl" class="text-xs font-mono px-3 py-2 text-warm-500 dark:text-warm-400 whitespace-pre-wrap max-h-64 overflow-y-auto overflow-x-hidden bg-sapphire/4 dark:bg-sapphire/6 min-w-0 break-all" @scroll="onResultScroll">
          <template v-if="tc.resultParts?.length">
            <div class="flex flex-col gap-2">
              <template v-for="(part, i) in tc.resultParts" :key="i">
                <MarkdownRenderer v-if="part.type === 'text'" :content="part.text || ''" />
                <img v-else-if="part.type === 'image_url'" :src="part.image_url?.url" class="tool-inline-image" />
              </template>
            </div>
          </template>
          <template v-else>
            {{ tc.result || "(no output)" }}
          </template>
        </div>
        <div v-if="tc.resultMeta?.truncated" class="px-3 py-1 text-[10px] border-t border-sapphire/15 dark:border-sapphire/20 bg-sapphire/8 dark:bg-sapphire/10 text-amber-shadow dark:text-amber-light font-mono">
          Output truncated<span v-if="tc.resultMeta.omitted_text_bytes"> · {{ tc.resultMeta.omitted_text_bytes.toLocaleString() }} bytes omitted</span>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import MarkdownRenderer from "@/components/common/MarkdownRenderer.vue"
import { useChatStore } from "@/stores/chat"
import { terrariumAPI } from "@/utils/api"
import { useI18n } from "@/utils/i18n"

const props = defineProps({
  tc: { type: Object, required: true },
  expanded: { type: Boolean, default: false },
  depth: { type: Number, default: 0 },
})

const emit = defineEmits(["toggle"])
const chat = useChatStore()
const { t } = useI18n()

// ── Sub-agent inner conversation (UXI-05) ──
// Read a sub-agent run's transcript by its live job_id (fallback to its
// name). The backend's ``can_receive`` says whether the run can be
// messaged (true for ANY live, still-running sub-agent) — that gates the
// send box; completed / persisted runs stay read-only. sid/cid come from
// the scoped chat store (active creature).
const convOpen = ref(false)
const convLoading = ref(false)
const convError = ref("")
const convMessages = ref([])
const canReceive = ref(false)
const sendText = ref("")
const sending = ref(false)
// System prompts render collapsed; these track which system / tool blocks
// are expanded (system by convBlocks index, tools by tool_call id).
const expandedSystem = ref(new Set())
const convToolExpanded = ref(new Set())

// ── OpenAI-shape → main-chat block model (UXI-05) ──
// The read route returns ``{role, content: str|parts, tool_calls?, name?,
// tool_call_id?}``. We render it with the SAME idiom as the main chat:
// user/assistant bubbles (markdown) and each assistant tool_call paired
// with its ``tool`` result (by tool_call_id) into a ``ToolCallBlock``
// accordion — exactly the shape ``ChatMessage`` feeds its tool blocks.
function msgText(m) {
  if (typeof m?.content === "string") return m.content
  if (Array.isArray(m?.content)) {
    return m.content
      .filter((p) => p?.type === "text")
      .map((p) => p.text || "")
      .join("\n")
  }
  return ""
}

function msgParts(m) {
  return Array.isArray(m?.content) ? m.content : null
}

function toolText(m) {
  return typeof m?.content === "string" ? m.content : msgText(m)
}

function _parseArgs(raw) {
  if (!raw) return {}
  if (typeof raw !== "string") return raw
  try {
    return JSON.parse(raw)
  } catch {
    return { raw }
  }
}

const convBlocks = computed(() => {
  const msgs = convMessages.value
  const resultById = {}
  for (const m of msgs) {
    if (m?.role === "tool" && m.tool_call_id != null) resultById[m.tool_call_id] = toolText(m)
  }
  const items = []
  msgs.forEach((m, mi) => {
    const role = m?.role
    if (role === "tool") return // folded into the assistant tool_call below
    if (role === "system") {
      items.push({ kind: "system", text: msgText(m) })
      return
    }
    if (role === "user") {
      items.push({ kind: "user", content: msgText(m), parts: msgParts(m) })
      return
    }
    const toolCalls = (m?.tool_calls || []).map((call, ci) => ({
      type: "tool",
      id: call.id || `sa_${mi}_${ci}`,
      name: call.function?.name || "tool",
      kind: "tool",
      args: _parseArgs(call.function?.arguments),
      status: "done",
      result: call.id != null ? resultById[call.id] || "" : "",
      children: [],
    }))
    items.push({ kind: "assistant", content: msgText(m), parts: msgParts(m), toolCalls })
  })
  return items
})

function toggleConvTool(id) {
  const s = new Set(convToolExpanded.value)
  if (s.has(id)) s.delete(id)
  else s.add(id)
  convToolExpanded.value = s
}

function toggleSystem(i) {
  const s = new Set(expandedSystem.value)
  if (s.has(i)) s.delete(i)
  else s.add(i)
  expandedSystem.value = s
}

async function loadConversation({ silent = false } = {}) {
  const sid = chat._instanceGraphId
  const cid = chat.activeTab
  if (!sid || !cid) {
    convError.value = t("chat.subagent.unavailable")
    return
  }
  // Silent refresh keeps the rendered transcript (and the user's expanded
  // accordions) in place; only the first load shows the loading state.
  if (!silent) convLoading.value = true
  convError.value = ""
  try {
    const ident = props.tc.jobId ? { jobId: props.tc.jobId } : { name: props.tc.name }
    const data = await terrariumAPI.getSubagentConversation(sid, cid, ident)
    convMessages.value = data.messages || []
    canReceive.value = !!data.can_receive
  } catch (err) {
    if (silent) return // transient poll failure must not wipe the transcript
    convError.value = err?.response?.data?.detail || t("chat.subagent.unavailable")
    convMessages.value = []
    canReceive.value = false
  } finally {
    if (!silent) convLoading.value = false
  }
}

// Live refresh: while the accordion is open and the run is still live
// (block streaming or backend can_receive), poll the transcript so new
// turns appear without re-toggling. Stops when the run settles or the
// accordion closes; a status flip to terminal triggers one final load.
const CONV_POLL_MS = 1500
let convTimer = null

function _convIsLive() {
  return props.tc.status === "running" || canReceive.value
}

function _stopConvPolling() {
  if (convTimer) {
    clearInterval(convTimer)
    convTimer = null
  }
}

function _startConvPolling() {
  if (convTimer) return
  convTimer = setInterval(() => {
    if (!convOpen.value || !_convIsLive()) {
      _stopConvPolling()
      return
    }
    if (!convLoading.value && !sending.value) loadConversation({ silent: true })
  }, CONV_POLL_MS)
}

watch(convOpen, (open) => {
  if (open) _startConvPolling()
  else _stopConvPolling()
})

watch(
  () => props.tc.status,
  (status, prev) => {
    if (!convOpen.value) return
    if (prev === "running" && status !== "running") loadConversation({ silent: true })
    else if (status === "running") _startConvPolling()
  },
)

onUnmounted(_stopConvPolling)

function toggleConversation() {
  convOpen.value = !convOpen.value
  if (convOpen.value && !convMessages.value.length && !convLoading.value) loadConversation()
}

async function submitSend() {
  const text = sendText.value.trim()
  if (!text || sending.value) return
  const sid = chat._instanceGraphId
  const cid = chat.activeTab
  if (!sid || !cid) return
  sending.value = true
  convError.value = ""
  try {
    // Pass job_id so the backend targets THIS live run precisely.
    await terrariumAPI.sendSubagentMessage(sid, cid, props.tc.name, text, props.tc.jobId)
    sendText.value = ""
    await loadConversation()
  } catch (err) {
    convError.value = err?.response?.data?.detail || t("chat.subagent.sendFailed")
  } finally {
    sending.value = false
  }
}

// Track expanded state for child tool blocks
const childExpanded = reactive({})

function toggleChild(index) {
  childExpanded[index] = !childExpanded[index]
}

// ── Follow-mode auto-scroll for the two internal scroll containers. ──
// Logic mirrors ChatPanel's outer scroller: we auto-stick to the bottom
// while the user hasn't manually scrolled up. Once they do, we stop
// following until they scroll back within ~32 px of the bottom.
const NEAR_BOTTOM_PX = 32
const childrenEl = ref(null)
const resultEl = ref(null)
const childrenFollow = ref(true)
const resultFollow = ref(true)

function _isNearBottom(el) {
  if (!el) return true
  return el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX
}

function onChildrenScroll() {
  childrenFollow.value = _isNearBottom(childrenEl.value)
}
function onResultScroll() {
  resultFollow.value = _isNearBottom(resultEl.value)
}

function _stickToBottom(el, follow) {
  if (!el || !follow) return
  // Next tick so the DOM has the new child/content height.
  nextTick(() => {
    if (!el || !follow.value) return
    el.scrollTop = el.scrollHeight
  })
}

// Follow new sub-agent child tool calls.
watch(
  () => props.tc.children?.length,
  () => _stickToBottom(childrenEl.value, childrenFollow),
)

// Follow streaming tool / sub-agent result growth. The result string
// grows character by character during streaming, and resultParts
// length changes when new parts arrive.
watch(
  () => [typeof props.tc.result === "string" ? props.tc.result.length : 0, props.tc.resultParts?.length || 0],
  () => _stickToBottom(resultEl.value, resultFollow),
)

// Elapsed time — chat.getJobElapsed reads _jobTick internally so this
// recomputes every second while a job is running.
const elapsed = computed(() => {
  if (props.tc.status === "running") return chat.getJobElapsed(props.tc)
  if (props.tc.duration) return `${props.tc.duration.toFixed(1)}s`
  return ""
})

// Auto-expand running sub-agents when children FIRST appear (one-shot)
const _didAutoExpand = ref(false)
watch(
  () => props.tc.children?.length,
  (len) => {
    if (len > 0 && !_didAutoExpand.value && props.tc.kind === "subagent" && props.tc.status === "running" && !props.expanded) {
      _didAutoExpand.value = true
      emit("toggle")
    }
  },
)

// Show "→ bg" button for running direct tasks after 1 second. We
// re-read elapsed.value so this computed is invalidated whenever the
// store's job tick advances.
const canPromote = computed(() => {
  if (props.tc.status !== "running") return false
  const jobId = props.tc.jobId || props.tc.id
  const job = chat.runningJobs[jobId]
  if (!job || !job.promotable) return false
  void elapsed.value
  return Date.now() - (props.tc.startedAt || 0) > 1000
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
.tool-inline-image {
  display: block;
  max-width: min(65%, 42vw);
  max-height: 35vh;
  width: auto;
  height: auto;
  object-fit: contain;
  border-radius: 0.5rem;
  border: 1px solid rgb(231 223 211 / 1);
}

@supports (max-width: 65cqw) {
  .tool-inline-image {
    max-width: 65cqw;
    max-height: 50cqh;
  }
}

.dark .tool-inline-image {
  border-color: rgb(89 75 61 / 1);
}

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
