<template>
  <!-- System message -->
  <div v-if="message.role === 'system'" class="text-center text-xs text-warm-400 dark:text-warm-500 py-1">
    {{ message.content }}
  </div>

  <!-- Context cleared banner -->
  <div v-else-if="message.role === 'clear'" class="flex items-center gap-3 py-2">
    <div class="flex-1 border-t border-warm-300 dark:border-warm-600 border-dashed" />
    <span class="text-xs text-warm-400 dark:text-warm-500 shrink-0"> Context Cleared{{ message.messagesCleared ? ` — ${message.messagesCleared} messages` : "" }} </span>
    <div class="flex-1 border-t border-warm-300 dark:border-warm-600 border-dashed" />
  </div>

  <!-- Context compacted (accordion) -->
  <div v-else-if="message.role === 'compact'" class="rounded-lg overflow-hidden" :class="message.status === 'running' ? 'bg-amber/6 dark:bg-amber/8 border border-amber/15 dark:border-amber/20' : 'bg-iolite/6 dark:bg-iolite/8 border border-iolite/15 dark:border-iolite/20'">
    <div role="button" tabindex="0" :aria-expanded="!!expandedTools['compact_' + message.id]" class="flex items-center gap-2 py-1.5 px-3 cursor-pointer select-none" @click="toggleTool('compact_' + message.id)" @keydown.enter="toggleTool('compact_' + message.id)" @keydown.space.prevent="toggleTool('compact_' + message.id)">
      <span v-if="message.status === 'running'" class="w-1.5 h-1.5 rounded-full bg-amber kohaku-pulse shrink-0" />
      <span class="text-xs font-medium" :class="message.status === 'running' ? 'text-amber dark:text-amber-light' : 'text-iolite dark:text-iolite-light'">
        {{ message.status === "running" ? "Compacting context..." : `Context Compacted (round ${message.round || "?"})` }}
      </span>
      <span v-if="message.messagesCompacted" class="text-[10px] text-warm-400"> {{ message.messagesCompacted }} messages summarized </span>
      <span class="flex-1" />
      <span v-if="message.summary" class="i-carbon-chevron-down text-warm-400 text-[10px] transition-transform" :class="{ 'rotate-180': expandedTools['compact_' + message.id] }" />
    </div>
    <div v-if="expandedTools['compact_' + message.id] && message.summary" class="px-3 py-2 border-t border-iolite/10 dark:border-iolite/15 text-xs max-h-48 overflow-y-auto">
      <MarkdownRenderer :content="message.summary" />
    </div>
  </div>

  <!-- Processing error -->
  <div v-else-if="message.role === 'error'" class="rounded-lg bg-coral/8 dark:bg-coral/12 border border-coral/25 dark:border-coral/30 overflow-hidden">
    <div role="button" tabindex="0" :aria-expanded="errorExpanded" class="flex items-center gap-2 py-2 px-3 cursor-pointer select-none hover:bg-coral/12 dark:hover:bg-coral/18" @click="errorExpanded = !errorExpanded" @keydown.enter="errorExpanded = !errorExpanded" @keydown.space.prevent="errorExpanded = !errorExpanded">
      <span class="text-coral font-bold text-sm">&#x2717;</span>
      <span class="text-coral dark:text-coral-light font-semibold text-xs flex-1">
        {{ message.errorType || "Processing Error" }}
      </span>
      <span v-if="errorFirstLine" class="text-xs text-coral-shadow dark:text-coral-light/70 font-mono truncate max-w-[60%]">
        {{ errorFirstLine }}
      </span>
      <span class="i-carbon-chevron-down text-coral/60 transition-transform text-[10px]" :class="{ 'rotate-180': errorExpanded }" />
    </div>
    <div v-if="errorExpanded" class="px-3 pb-2 text-xs text-coral-shadow dark:text-coral-light/80 font-mono whitespace-pre-wrap border-t border-coral/20">
      {{ message.content }}
    </div>
  </div>

  <!-- Trigger fired (expandable if has message content) -->
  <!-- Inbound output-wiring delivery — another creature's turn-end
       fired this creature via output_wiring. Rendered as a compact
       accordion so the user knows why this creature suddenly started
       processing without typing anything in this tab. -->
  <div v-else-if="message.role === 'wire_inbound'" class="rounded-lg bg-iolite/6 dark:bg-iolite/8 border border-iolite/15 dark:border-iolite/20 overflow-hidden">
    <div :role="message.preview ? 'button' : undefined" :tabindex="message.preview ? 0 : undefined" :aria-expanded="message.preview ? !!expandedTools['wire_' + message.id] : undefined" class="flex items-center gap-2 py-1.5 px-3" :class="message.preview ? 'cursor-pointer select-none' : ''" @click="message.preview && toggleTool('wire_' + message.id)" @keydown.enter="message.preview && toggleTool('wire_' + message.id)" @keydown.space.prevent="message.preview && toggleTool('wire_' + message.id)">
      <span class="i-carbon-connect text-iolite dark:text-iolite-light text-xs shrink-0" />
      <span class="text-xs text-iolite-shadow dark:text-iolite-light flex-1">
        Inbound from <span class="font-semibold">{{ message.from }}</span>
        <span v-if="!message.withContent" class="opacity-60"> · ping (no content)</span>
      </span>
      <span v-if="message.preview" class="i-carbon-chevron-down text-iolite/50 text-[10px] transition-transform" :class="{ 'rotate-180': expandedTools['wire_' + message.id] }" />
    </div>
    <div v-if="expandedTools['wire_' + message.id] && message.preview" class="px-3 py-2 border-t border-iolite/10 dark:border-iolite/15 text-xs max-h-32 overflow-y-auto">
      <MarkdownRenderer :content="message.preview" />
    </div>
  </div>

  <div v-else-if="message.role === 'trigger'" class="rounded-lg bg-amber/6 dark:bg-amber/8 border border-amber/15 dark:border-amber/20 overflow-hidden">
    <div :role="message.triggerContent ? 'button' : undefined" :tabindex="message.triggerContent ? 0 : undefined" :aria-expanded="message.triggerContent ? !!expandedTools['trig_' + message.id] : undefined" class="flex items-center gap-2 py-1.5 px-3" :class="message.triggerContent ? 'cursor-pointer select-none' : ''" @click="message.triggerContent && toggleTool('trig_' + message.id)" @keydown.enter="message.triggerContent && toggleTool('trig_' + message.id)" @keydown.space.prevent="message.triggerContent && toggleTool('trig_' + message.id)">
      <span class="w-1.5 h-1.5 rounded-full bg-amber shrink-0" />
      <span class="text-xs text-amber-shadow dark:text-amber-light flex-1">
        Triggered by <span class="font-semibold">{{ message.content }}</span>
      </span>
      <span v-if="message.triggerContent" class="i-carbon-chevron-down text-amber/50 text-[10px] transition-transform" :class="{ 'rotate-180': expandedTools['trig_' + message.id] }" />
    </div>
    <div v-if="expandedTools['trig_' + message.id] && message.triggerContent" class="px-3 py-2 border-t border-amber/10 dark:border-amber/15 text-xs max-h-32 overflow-y-auto">
      <MarkdownRenderer :content="message.triggerContent" />
    </div>
  </div>

  <!-- User message -->
  <div v-else-if="message.role === 'user'" class="ml-auto group relative" :class="editing ? 'w-[min(760px,92%)] max-w-[92%]' : 'max-w-[80%]'">
    <div class="user-message" :class="{ 'opacity-70': message.queued, 'user-message-editing': editing }">
      <div class="text-xs text-warm-400 mb-1 flex items-center gap-1.5">
        <span>You</span>
        <span v-if="message.queued" class="px-1.5 py-0.5 rounded text-[9px] font-medium bg-amber/15 text-amber leading-none">Queued</span>
      </div>
      <!-- Edit mode -->
      <div v-if="editing" class="flex flex-col gap-2.5">
        <div v-if="editAttachments.length" class="flex flex-wrap gap-2">
          <div v-for="(attachment, idx) in editAttachments" :key="attachment.id || attachment.name + ':' + idx" class="flex items-center gap-2 px-2.5 py-1 rounded-lg bg-iolite/8 dark:bg-iolite/12 border border-iolite/20 text-xs">
            <span :class="attachment.kind === 'image' ? 'i-carbon-image text-iolite dark:text-iolite-light' : 'i-carbon-document text-aquamarine'" />
            <span class="text-warm-700 dark:text-warm-200 max-w-52 truncate">{{ attachment.name }}</span>
            <button class="text-warm-400 hover:text-coral" :disabled="editSaving" @click="removeEditAttachment(idx)">
              <span class="i-carbon-close" />
            </button>
          </div>
        </div>
        <div class="flex gap-2 pl-2 pr-3 py-2 rounded-xl bg-warm-50 dark:bg-warm-800 border border-warm-200 dark:border-warm-700 focus-within:border-iolite/40 dark:focus-within:border-iolite-light/30 transition-colors items-end">
          <input ref="editImageInputEl" type="file" accept="image/*" class="hidden" @change="(e) => onEditFileChange(e, 'image')" />
          <input ref="editFileInputEl" type="file" class="hidden" @change="(e) => onEditFileChange(e, 'file')" />
          <div class="flex items-center gap-0 shrink-0 mb-0.5">
            <button class="w-7 h-7 flex items-center justify-center rounded-md transition-colors shrink-0 text-warm-400 hover:text-aquamarine dark:hover:text-aquamarine hover:bg-aquamarine/10 disabled:opacity-50" title="Attach file" aria-label="Attach file" :disabled="editSaving" @click="editFileInputEl?.click()">
              <span class="i-carbon-add text-xs" />
            </button>
            <button class="w-7 h-7 flex items-center justify-center rounded-md transition-colors shrink-0 text-warm-400 hover:text-iolite dark:hover:text-iolite-light hover:bg-iolite/10 disabled:opacity-50" title="Attach image" aria-label="Attach image" :disabled="editSaving" @click="editImageInputEl?.click()">
              <span class="i-carbon-image text-xs" />
            </button>
          </div>
          <textarea ref="editTextareaEl" v-model="editText" class="message-edit-textarea message-edit-inline" :rows="Math.min(16, Math.max(6, editText.split('\n').length))" :disabled="editSaving" @keydown.meta.enter="confirmEdit" @keydown.ctrl.enter="confirmEdit" @keydown.esc="cancelEdit" />
        </div>
        <div class="flex flex-wrap items-center gap-2 text-xs">
          <span class="text-warm-400 dark:text-warm-500 mr-auto">Ctrl/Cmd+Enter to rerun · Esc to cancel</span>
          <button class="px-2.5 py-1 rounded hover:bg-warm-100 dark:hover:bg-warm-800 disabled:opacity-50" :disabled="editSaving" @click="cancelEdit">Cancel</button>
          <button class="px-2.5 py-1 rounded bg-sapphire text-white hover:bg-sapphire-dark disabled:opacity-60" :disabled="editSaving || (!editText.trim() && editAttachments.length === 0)" @click="confirmEdit">
            {{ editSaving ? "Saving..." : "Save & Rerun" }}
          </button>
        </div>
      </div>
      <div v-else class="text-body break-words overflow-wrap-anywhere min-w-0">
        <template v-if="message.contentParts?.length">
          <div class="flex flex-col gap-2">
            <template v-for="(part, i) in message.contentParts" :key="i">
              <MarkdownRenderer v-if="part.type === 'text'" :content="part.text || ''" :breaks="true" />
              <img v-else-if="part.type === 'image_url'" :src="part.image_url?.url" class="chat-inline-image" />
              <div v-else-if="part.type === 'file'" class="px-3 py-2 rounded-lg border border-aquamarine/20 bg-aquamarine/5 text-xs text-warm-600 dark:text-warm-300">
                <span class="i-carbon-document mr-1 text-aquamarine" />
                {{ part.file?.name || part.file?.path || "file" }}
              </div>
            </template>
          </div>
        </template>
        <template v-else>
          <div class="whitespace-pre-wrap">{{ message.content }}</div>
        </template>
      </div>
    </div>
    <!-- Hover actions for user messages -->
    <div v-if="!editing && !message.queued && messageIdx != null" class="absolute -bottom-5 right-2 flex gap-1 items-center opacity-0 group-hover:opacity-100 transition-opacity">
      <!-- Branch navigator on user message: shown only when this turn
           has multiple distinct user contents (i.e. an edit produced
           a sibling branch at this divergence point). -->
      <div v-if="hasUserGroups" class="flex items-center gap-0.5 mr-1 select-none">
        <button class="msg-action-btn" title="Previous edit" aria-label="Previous user edit" :disabled="!hasPrevUserGroup" @click="goToPrevUserGroup">
          <span class="i-carbon-chevron-left text-xs" />
        </button>
        <span class="text-[10px] tabular-nums text-warm-500 px-1">{{ message.currentUserGroupIdx + 1 }}/{{ message.userGroupCount }}</span>
        <button class="msg-action-btn" title="Next edit" aria-label="Next user edit" :disabled="!hasNextUserGroup" @click="goToNextUserGroup">
          <span class="i-carbon-chevron-right text-xs" />
        </button>
      </div>
      <button class="msg-action-btn" title="Copy" aria-label="Copy message" @click="copyMessage">
        <span class="i-carbon-copy text-xs" />
      </button>
      <button class="msg-action-btn" title="Edit & rerun" aria-label="Edit and rerun message" @click="startEdit">
        <span class="i-carbon-edit text-xs" />
      </button>
    </div>
  </div>

  <!-- Assistant message (parts-based: ordered text + tools + images) -->
  <div v-else-if="message.role === 'assistant' && message.parts" class="max-w-[90%] group relative">
    <template v-for="(part, pi) in message.parts" :key="pi">
      <!-- Text part -->
      <div v-if="part.type === 'text' && part.content" class="text-body mb-1">
        <MarkdownRenderer :content="part.content" />
      </div>
      <!-- Tool/subagent part -->
      <div v-else-if="part.type === 'tool'" class="mb-1.5">
        <ToolCallBlock :tc="part" :expanded="expandedTools[part.id]" @toggle="toggleTool(part.id)" />
      </div>
      <!-- Image part (same render + CSS as user-side attached images) -->
      <div v-else-if="part.type === 'image_url'" class="mb-1.5">
        <img :src="part.image_url?.url" class="chat-inline-image" :alt="part.meta?.source_name || 'generated image'" />
      </div>
    </template>
    <!-- Hover actions -->
    <div class="absolute -bottom-5 left-2 flex gap-1 items-center opacity-0 group-hover:opacity-100 transition-opacity">
      <!-- Branch navigator on the assistant bubble: shown only when
           the current user-content group has more than one regen
           alternative. Edit-only branching does NOT light this up —
           that's the user-side navigator's job. -->
      <div v-if="hasAssistantBranches" class="flex items-center gap-0.5 mr-1 select-none">
        <button class="msg-action-btn" title="Previous regen" aria-label="Previous regen" :disabled="!hasPrevAssistantBranch" @click="goToPrevAssistantBranch">
          <span class="i-carbon-chevron-left text-xs" />
        </button>
        <span class="text-[10px] tabular-nums text-warm-500 px-1">{{ message.currentAssistantIdx + 1 }}/{{ message.assistantBranchCount }}</span>
        <button class="msg-action-btn" title="Next regen" aria-label="Next regen" :disabled="!hasNextAssistantBranch" @click="goToNextAssistantBranch">
          <span class="i-carbon-chevron-right text-xs" />
        </button>
      </div>
      <button class="msg-action-btn" title="Copy" aria-label="Copy response" @click="copyAssistantText">
        <span class="i-carbon-copy text-xs" />
      </button>
      <!-- Regenerate: opens a new branch of this turn. Always visible
           on assistant messages — the previous duplicate "Retry"
           button was identical and only hid the affordance when an
           interrupt left the turn in a non-"last" state. -->
      <button class="msg-action-btn" title="Regenerate" aria-label="Regenerate response" @click="regenerate">
        <span class="i-carbon-renew text-xs" />
      </button>
    </div>
  </div>

  <!-- Assistant message (legacy: content + tool_calls) -->
  <div v-else-if="message.role === 'assistant'" class="max-w-[90%]">
    <div v-if="message.tool_calls?.length" class="mb-2 flex flex-col gap-1.5">
      <ToolCallBlock v-for="tc in message.tool_calls" :key="tc.id" :tc="tc" :expanded="expandedTools[tc.id]" @toggle="toggleTool(tc.id)" />
    </div>
    <div v-if="message.content" class="text-body">
      <MarkdownRenderer :content="message.content" />
    </div>
  </div>

  <!-- Channel message (group chat style) -->
  <div v-else-if="message.role === 'channel'" class="max-w-[90%]">
    <div v-if="showSenderHeader" class="flex items-center gap-2 mb-1" :class="{ 'mt-2': !isFirst }">
      <span class="w-5 h-5 rounded-md flex items-center justify-center text-[10px] font-bold text-white" :style="{ background: senderGemColor }">
        {{ message.sender.charAt(0).toUpperCase() }}
      </span>
      <span class="text-xs font-semibold" :style="{ color: senderGemColor }">{{ message.sender }}</span>
      <span class="text-[10px] text-warm-400">{{ message.timestamp }}</span>
    </div>
    <div class="pl-7 text-body">
      <template v-if="message.contentParts?.length">
        <div class="flex flex-col gap-2">
          <template v-for="(part, i) in message.contentParts" :key="i">
            <MarkdownRenderer v-if="part.type === 'text'" :content="part.text || ''" :breaks="true" />
            <img v-else-if="part.type === 'image_url'" :src="part.image_url?.url" class="chat-inline-image" />
            <div v-else-if="part.type === 'file'" class="px-3 py-2 rounded-lg border border-aquamarine/20 bg-aquamarine/5 text-xs text-warm-600 dark:text-warm-300">
              <span class="i-carbon-document mr-1 text-aquamarine" />
              {{ part.file?.name || part.file?.path || "file" }}
            </div>
          </template>
        </div>
      </template>
      <MarkdownRenderer v-else :content="message.content" :breaks="true" />
    </div>
  </div>

  <!-- Phase B output-event kinds (ask_text, confirm, selection, progress, notification, card) -->
  <UIEventBlock v-else-if="message.role === 'ui_event'" :message="message" @reply="onUIEventReply" />
</template>

<script setup>
import { ElMessage } from "element-plus"

import MarkdownRenderer from "@/components/common/MarkdownRenderer.vue"
import ToolCallBlock from "@/components/chat/ToolCallBlock.vue"
import UIEventBlock from "@/components/chat/UIEventBlock.vue"
import { useChatStore } from "@/stores/chat"
import { GEM } from "@/utils/colors"
import { buildMessageParts, contentToEditableDraft, formatBytes, MAX_ATTACHMENT_BYTES, MAX_IMAGE_BYTES } from "@/utils/chatAttachments"

// Module-scoped so colors are stable across all ChatMessage instances.
// If this were declared inside <script setup>, each message would have
// its own cache and the same sender would cycle through colors.
const SENDER_GEMS = [GEM.iolite.main, GEM.aquamarine.main, GEM.taaffeite.main, GEM.amber.main, GEM.sapphire.main]
const _senderColorCache = {}
let _nextColorIdx = 0

function _gemForSender(name) {
  if (!name) return GEM.iolite.main
  if (!_senderColorCache[name]) {
    _senderColorCache[name] = SENDER_GEMS[_nextColorIdx % SENDER_GEMS.length]
    _nextColorIdx++
  }
  return _senderColorCache[name]
}

/** Extract plain text from content that may be a string or array of content parts. */
function contentToText(content) {
  if (typeof content === "string") return content
  if (Array.isArray(content)) {
    return content
      .filter((p) => p?.type === "text")
      .map((p) => p.text || "")
      .join("\n")
  }
  return ""
}

const props = defineProps({
  message: { type: Object, required: true },
  prevMessage: { type: Object, default: null },
  isFirst: { type: Boolean, default: false },
  messageIdx: { type: Number, default: null },
  isLastAssistant: { type: Boolean, default: false },
})

const expandedTools = reactive({})
const editing = ref(false)
const editText = ref("")
const editAttachments = ref([])
const editTextareaEl = ref(null)
const editImageInputEl = ref(null)
const editFileInputEl = ref(null)
const editSaving = ref(false)
const errorExpanded = ref(false)

const errorFirstLine = computed(() => {
  if (props.message.role !== "error") return ""
  const content = contentToText(props.message.content)
  const firstLine = content.split("\n")[0] || ""
  return firstLine.length > 80 ? firstLine.slice(0, 80) + "…" : firstLine
})

function toggleTool(id) {
  expandedTools[id] = !expandedTools[id]
}

// Phase B UI event reply: forward to chat store, which sends
// ``{type: "ui_reply", ...}`` over the per-tab WebSocket.
function onUIEventReply({ actionId, values }) {
  if (!props.message?.eventId) return
  const tab = props.message.tab || ""
  chat.submitUIReply(tab, props.message.eventId, actionId, values || {})
}

const showSenderHeader = computed(() => {
  if (props.message.role !== "channel") return false
  if (!props.prevMessage || props.prevMessage.role !== "channel") return true
  return props.prevMessage.sender !== props.message.sender
})

const senderGemColor = computed(() => _gemForSender(props.message.sender))

// ── Message actions (copy / edit / regenerate) ──

const chat = useChatStore()

function copyMessage() {
  const text = contentToText(props.message.contentParts || props.message.content)
  navigator.clipboard.writeText(text)
}

function copyAssistantText() {
  let text = ""
  if (props.message.parts) {
    for (const part of props.message.parts) {
      if (part.type === "text" && part.content) {
        text += part.content
      }
    }
  } else if (props.message.content) {
    text = props.message.content
  }
  navigator.clipboard.writeText(text)
}

function startEdit() {
  const draft = contentToEditableDraft(props.message.contentParts || props.message.content)
  editText.value = draft.text
  editAttachments.value = draft.attachments
  editing.value = true
  nextTick(() => editTextareaEl.value?.focus())
}

function cancelEdit() {
  if (editSaving.value) return
  editing.value = false
  editText.value = ""
  editAttachments.value = []
}

function _pushEditAttachment(file, kind) {
  const limit = kind === "image" ? MAX_IMAGE_BYTES : MAX_ATTACHMENT_BYTES
  if (file.size > limit) {
    ElMessage.error(`${file.name} is too large (${formatBytes(file.size)} > ${formatBytes(limit)})`)
    return false
  }
  if (kind === "image" && file.type && !file.type.startsWith("image/")) {
    ElMessage.error(`${file.name} is not an image file`)
    return false
  }
  editAttachments.value.push({
    id: `new_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
    file,
    name: file.name,
    kind,
  })
  return true
}

function onEditFileChange(e, kind = "file") {
  const files = Array.from(e.target.files || [])
  for (const file of files) _pushEditAttachment(file, kind)
  e.target.value = ""
}

function removeEditAttachment(index) {
  editAttachments.value.splice(index, 1)
}

async function confirmEdit() {
  if (editSaving.value || (!editText.value.trim() && editAttachments.value.length === 0)) return
  editSaving.value = true
  try {
    const newContent = await buildMessageParts(editText.value, editAttachments.value)
    const ok = await chat.editMessage(props.messageIdx, newContent, {
      turnIndex: props.message.turnIndex,
      userPosition: props.message.userPosition,
      latestBranch: props.message.latestBranch,
    })
    if (!ok) return
    editing.value = false
    editText.value = ""
    editAttachments.value = []
  } catch (err) {
    console.error("Failed to prepare edited message:", err)
  } finally {
    editSaving.value = false
  }
}

function regenerate() {
  chat.regenerateLastResponse()
}

// ── Branch navigator ──
//
// User-side <x/N>: walks distinct user_message contents (edits).
// Assistant-side <x/N>: walks regens within the current user content.
// The two are independent — a turn can have neither, either, or both.

const hasUserGroups = computed(() => props.message.branchAnchor === "user" && typeof props.message.userGroupCount === "number" && props.message.userGroupCount > 1)
const hasPrevUserGroup = computed(() => hasUserGroups.value && (props.message.currentUserGroupIdx ?? 0) > 0)
const hasNextUserGroup = computed(() => hasUserGroups.value && (props.message.currentUserGroupIdx ?? 0) < props.message.userGroupCount - 1)

function _switchUserGroup(delta) {
  const idx = props.message.currentUserGroupIdx ?? 0
  const target = idx + delta
  const groups = props.message.userGroupBranches || []
  if (target < 0 || target >= groups.length) return
  chat.selectBranch(props.message.turnIndex, groups[target])
}
function goToPrevUserGroup() {
  if (hasPrevUserGroup.value) _switchUserGroup(-1)
}
function goToNextUserGroup() {
  if (hasNextUserGroup.value) _switchUserGroup(1)
}

const hasAssistantBranches = computed(() => props.message.branchAnchor === "assistant" && typeof props.message.assistantBranchCount === "number" && props.message.assistantBranchCount > 1)
const hasPrevAssistantBranch = computed(() => hasAssistantBranches.value && (props.message.currentAssistantIdx ?? 0) > 0)
const hasNextAssistantBranch = computed(() => hasAssistantBranches.value && (props.message.currentAssistantIdx ?? 0) < props.message.assistantBranchCount - 1)

function _switchAssistantBranch(delta) {
  const idx = props.message.currentAssistantIdx ?? 0
  const target = idx + delta
  const branches = props.message.assistantBranches || []
  if (target < 0 || target >= branches.length) return
  chat.selectBranch(props.message.turnIndex, branches[target])
}
function goToPrevAssistantBranch() {
  if (hasPrevAssistantBranch.value) _switchAssistantBranch(-1)
}
function goToNextAssistantBranch() {
  if (hasNextAssistantBranch.value) _switchAssistantBranch(1)
}
</script>

<style scoped>
.chat-inline-image {
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
  .chat-inline-image {
    max-width: 65cqw;
    max-height: 50cqh;
  }
}

.dark .chat-inline-image {
  border-color: rgb(89 75 61 / 1);
}

.message-edit-textarea {
  width: 100%;
  min-height: 160px;
  max-height: 50vh;
  padding: 0.75rem 0.85rem;
  border-radius: 0.75rem;
  border: 1px solid var(--color-border);
  background: var(--color-card);
  color: var(--color-text);
  font-size: 0.92rem;
  line-height: 1.6;
  resize: vertical;
  outline: none;
  box-shadow: inset 0 1px 2px rgb(0 0 0 / 0.04);
}

.message-edit-inline {
  min-height: 7rem;
  max-height: 40vh;
  border: none;
  background: transparent;
  padding: 0.25rem 0;
  box-shadow: none;
}

.message-edit-textarea:focus {
  border-color: rgb(124 103 184 / 0.55);
  box-shadow:
    0 0 0 2px rgb(124 103 184 / 0.12),
    inset 0 1px 2px rgb(0 0 0 / 0.04);
}

.message-edit-inline:focus {
  border-color: transparent;
  box-shadow: none;
}

.user-message-editing {
  width: 100%;
}

.msg-action-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 4px;
  background: var(--color-card);
  border: 1px solid var(--color-border);
  color: var(--color-text-muted);
  cursor: pointer;
  transition:
    background 0.15s,
    color 0.15s,
    border-color 0.15s;
}
.msg-action-btn:hover {
  background: var(--color-card-hover);
  color: var(--color-text);
  border-color: var(--color-border-hover);
}
</style>
