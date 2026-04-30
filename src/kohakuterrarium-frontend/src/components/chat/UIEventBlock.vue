<template>
  <!--
    UIEventBlock — renders Phase B output-event kinds (ask_text,
    confirm, selection, progress, notification, card) inline in the
    chat scroll. Replies are submitted via the chat store's
    ``submitUIReply`` action which sends ``{type: "ui_reply", ...}``
    over the existing chat WebSocket.

    The component dispatches on ``message.uiEventType`` and reads
    payload fields directly. Replied / superseded / timed-out events
    render in a dimmed state.

    Each event has a small minimize button in the upper right.
    Minimizing is local-only — no backend signal — so the user can
    temporarily collapse a long modal-style prompt and reopen it.
    To fully cancel an interactive event, use its Cancel button
    (which fires a ``cancel`` reply through the bus).
  -->
  <div class="ui-event-wrapper" :class="{ 'ui-event-collapsed-wrapper': collapsed }">
    <button v-if="canCollapse" class="ui-event-minimize" :title="collapsed ? 'Expand' : 'Minimize'" :aria-expanded="!collapsed" @click="collapsed = !collapsed">
      <span :class="collapsed ? 'i-carbon-add' : 'i-carbon-subtract'" />
    </button>
    <div v-if="collapsed" class="ui-event-collapsed-summary">
      <span :class="collapsedIcon" class="text-xs" />
      <span class="text-xs">{{ collapsedSummary }}</span>
      <span v-if="!isResolved && interactive" class="text-[10px] text-amber ml-2">pending</span>
    </div>
    <div v-show="!collapsed" class="ui-event-body">
      <!-- ── ask_text ────────────────────────────────────────────────── -->
      <div v-if="message.uiEventType === 'ask_text'" class="ui-event-card" :class="{ 'ui-event-done': isResolved }">
        <div class="ui-event-header">
          <span class="i-carbon-chat text-iolite text-sm" />
          <span class="text-xs font-medium text-iolite">Input requested</span>
          <span v-if="isResolved" class="text-[10px] text-warm-400 ml-auto">{{ resolvedLabel }}</span>
        </div>
        <div v-if="message.payload?.prompt" class="text-sm py-2 px-1">{{ message.payload.prompt }}</div>
        <div v-if="!isResolved" class="flex gap-2 items-stretch">
          <el-input v-model="textValue" :placeholder="message.payload?.placeholder || 'Type your reply…'" :type="message.payload?.multiline ? 'textarea' : 'text'" :rows="message.payload?.multiline ? 3 : 1" autofocus class="flex-1" @keydown.enter.exact.prevent="onSubmitText" />
          <el-button type="primary" :disabled="!textValue.trim()" @click="onSubmitText">Send</el-button>
          <el-button @click="onCancel">Cancel</el-button>
        </div>
        <div v-else-if="repliedValue" class="text-sm text-warm-500 italic px-1">→ {{ repliedValue }}</div>
      </div>

      <!-- ── confirm ─────────────────────────────────────────────────── -->
      <div v-else-if="message.uiEventType === 'confirm'" class="ui-event-card" :class="['accent-' + (message.payload?.accent || 'warning'), { 'ui-event-done': isResolved }]">
        <div class="ui-event-header">
          <span class="i-carbon-warning-alt text-amber text-sm" />
          <span class="text-xs font-medium text-amber">Confirm</span>
          <span v-if="isResolved" class="text-[10px] text-warm-400 ml-auto">{{ resolvedLabel }}</span>
        </div>
        <div v-if="message.payload?.prompt" class="text-sm py-2 px-1 font-medium">{{ message.payload.prompt }}</div>
        <div v-if="message.payload?.detail" class="text-xs text-warm-500 px-1 mb-2 whitespace-pre-wrap">{{ message.payload.detail }}</div>
        <div v-if="!isResolved" class="flex gap-2 flex-wrap">
          <el-button v-for="opt in message.payload?.options || []" :key="opt.id" :type="confirmButtonType(opt.style)" :plain="opt.id !== (message.payload?.default || '')" @click="onConfirmChoice(opt.id)">{{ opt.label || opt.id }}</el-button>
        </div>
        <div v-else-if="repliedActionId" class="text-sm text-warm-500 italic px-1">→ {{ repliedActionId }}</div>
      </div>

      <!-- ── selection ───────────────────────────────────────────────── -->
      <div v-else-if="message.uiEventType === 'selection'" class="ui-event-card" :class="{ 'ui-event-done': isResolved }">
        <div class="ui-event-header">
          <span class="i-carbon-list-checked text-iolite text-sm" />
          <span class="text-xs font-medium text-iolite">{{ message.payload?.multi ? "Pick options" : "Pick one" }}</span>
          <span v-if="isResolved" class="text-[10px] text-warm-400 ml-auto">{{ resolvedLabel }}</span>
        </div>
        <div v-if="message.payload?.prompt" class="text-sm py-2 px-1">{{ message.payload.prompt }}</div>
        <div v-if="!isResolved" class="flex flex-col gap-2">
          <template v-if="message.payload?.multi">
            <el-checkbox-group v-model="multiSelectedValues">
              <div v-for="opt in message.payload?.options || []" :key="opt.id" class="flex flex-col py-1">
                <el-checkbox :label="opt.id" :value="opt.id">{{ opt.label || opt.id }}</el-checkbox>
                <span v-if="opt.description" class="text-xs text-warm-400 ml-7">{{ opt.description }}</span>
              </div>
            </el-checkbox-group>
          </template>
          <template v-else>
            <el-radio-group v-model="singleSelectedValue">
              <div v-for="opt in message.payload?.options || []" :key="opt.id" class="flex flex-col py-1">
                <el-radio :label="opt.id" :value="opt.id">{{ opt.label || opt.id }}</el-radio>
                <span v-if="opt.description" class="text-xs text-warm-400 ml-6">{{ opt.description }}</span>
              </div>
            </el-radio-group>
          </template>
          <div class="flex gap-2 mt-2">
            <el-button type="primary" :disabled="!hasSelection" @click="onSubmitSelection">Submit</el-button>
            <el-button @click="onCancel">Cancel</el-button>
          </div>
        </div>
        <div v-else-if="repliedSelectionLabel" class="text-sm text-warm-500 italic px-1">→ {{ repliedSelectionLabel }}</div>
      </div>

      <!-- ── progress ────────────────────────────────────────────────── -->
      <div v-else-if="message.uiEventType === 'progress'" class="ui-event-card ui-event-progress">
        <div class="ui-event-header">
          <span :class="message.payload?.complete ? 'i-carbon-checkmark text-aquamarine' : 'i-carbon-time text-iolite'" class="text-sm" />
          <span class="text-xs font-medium" :class="message.payload?.complete ? 'text-aquamarine' : 'text-iolite'">
            {{ message.payload?.label || "Progress" }}
          </span>
          <span v-if="progressPct != null" class="text-[10px] text-warm-400 ml-auto">{{ progressPct }}%</span>
        </div>
        <el-progress v-if="!message.payload?.indeterminate" :percentage="progressPct ?? 0" :status="message.payload?.complete ? 'success' : ''" :show-text="false" :stroke-width="6" />
        <div v-else class="text-xs text-warm-400 italic">working…</div>
      </div>

      <!-- ── notification ────────────────────────────────────────────── -->
      <!-- (rendered via ElMessage in the store; this branch is only for
       ``surface=chat`` notifications that should appear inline) -->
      <div v-else-if="message.uiEventType === 'notification'" class="ui-event-card" :class="['accent-' + (message.payload?.level || 'info')]">
        <div class="ui-event-header">
          <span :class="notificationIconClass" class="text-sm" />
          <span class="text-xs font-medium">{{ message.payload?.title || message.payload?.level || "info" }}</span>
        </div>
        <div class="text-sm py-1 px-1">{{ message.payload?.text || "" }}</div>
        <div v-if="message.payload?.action && !isResolved" class="mt-2">
          <el-button :type="confirmButtonType(message.payload.action.style)" size="small" @click="onConfirmChoice(message.payload.action.id)">
            {{ message.payload.action.label || message.payload.action.id }}
          </el-button>
        </div>
      </div>

      <!-- ── card ────────────────────────────────────────────────────── -->
      <div v-else-if="message.uiEventType === 'card'" class="ui-event-card" :class="['accent-' + (message.payload?.accent || 'neutral'), { 'ui-event-done': isResolved && hasActions }]">
        <div class="ui-event-header">
          <span v-if="message.payload?.icon" class="text-sm">{{ message.payload.icon }}</span>
          <span class="text-sm font-semibold flex-1">{{ message.payload?.title || "" }}</span>
          <span v-if="message.payload?.subtitle" class="text-xs text-warm-500">{{ message.payload.subtitle }}</span>
        </div>
        <div v-if="message.payload?.body" class="text-sm py-2 px-1">
          <MarkdownRenderer :content="message.payload.body" :breaks="true" />
        </div>
        <div v-if="(message.payload?.fields || []).length" class="grid gap-x-3 gap-y-1 my-2 px-1" :style="{ gridTemplateColumns: anyInlineField ? '1fr 1fr' : '1fr' }">
          <div v-for="(f, i) in message.payload.fields" :key="i" class="text-xs" :style="!f.inline ? { gridColumn: '1 / -1' } : {}">
            <span class="font-semibold text-warm-500">{{ f.label }}:</span> {{ f.value }}
          </div>
        </div>
        <div v-if="message.payload?.footer" class="text-[10px] text-warm-400 mt-2 px-1 italic">{{ message.payload.footer }}</div>
        <div v-if="hasActions && !isResolved" class="flex gap-2 flex-wrap mt-2 items-center">
          <template v-for="a in message.payload.actions" :key="a.id">
            <el-link v-if="a.style === 'link'" :href="a.url" target="_blank" rel="noopener" type="primary" :underline="true" class="card-link">
              <span class="i-carbon-launch text-xs mr-1" />
              {{ a.label || a.id }}
            </el-link>
            <el-button v-else :type="confirmButtonType(a.style)" @click="onConfirmChoice(a.id)">
              {{ a.label || a.id }}
            </el-button>
          </template>
        </div>
        <div v-else-if="hasActions && repliedActionId" class="text-sm text-warm-500 italic px-1 mt-2">→ {{ repliedActionId }}</div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from "vue"
import { ElButton, ElCheckbox, ElCheckboxGroup, ElInput, ElLink, ElProgress, ElRadio, ElRadioGroup } from "element-plus"

import MarkdownRenderer from "@/components/common/MarkdownRenderer.vue"

const props = defineProps({
  message: { type: Object, required: true },
})

const emit = defineEmits(["reply"])

// ── Local form state (per-event) ────────────────────────────────
const textValue = ref(props.message.payload?.default || "")
const singleSelectedValue = ref(props.message.payload?.default || "")
const multiSelectedValues = ref(Array.isArray(props.message.payload?.default) ? [...props.message.payload.default] : [])

// ── Local UI state ──────────────────────────────────────────────
// ``collapsed`` is local to the component instance and intentionally
// not persisted: it's a transient UX affordance for the user to free
// scroll real-estate without losing the event. No backend signal is
// sent on minimize. To fully cancel an event, the user clicks the
// in-event Cancel button which fires a ``cancel`` reply via the bus.
const collapsed = ref(false)

// ── Derived state ───────────────────────────────────────────────
const isResolved = computed(() => props.message.replied === true || props.message.superseded === true || props.message.timedOut === true)

const resolvedLabel = computed(() => {
  if (props.message.timedOut) return "timed out"
  if (props.message.superseded) return "answered elsewhere"
  if (props.message.replied) return "answered"
  return ""
})

const repliedActionId = computed(() => props.message.repliedActionId || "")
const repliedValue = computed(() => props.message.repliedValues?.text || "")
const repliedSelectionLabel = computed(() => {
  const sel = props.message.repliedValues?.selected
  if (Array.isArray(sel)) return sel.join(", ")
  return sel || ""
})

const hasActions = computed(() => (props.message.payload?.actions || []).length > 0)

const hasSelection = computed(() => {
  if (props.message.payload?.multi) return multiSelectedValues.value.length > 0
  return Boolean(singleSelectedValue.value)
})

const progressPct = computed(() => {
  const v = props.message.payload?.value
  const m = props.message.payload?.max
  if (v == null || !m) return null
  return Math.max(0, Math.min(100, Math.round((v * 100) / m)))
})

const anyInlineField = computed(() => (props.message.payload?.fields || []).some((f) => f.inline))

const notificationIconClass = computed(() => {
  const level = props.message.payload?.level || "info"
  return (
    {
      info: "i-carbon-information text-iolite",
      success: "i-carbon-checkmark-filled text-aquamarine",
      warning: "i-carbon-warning-alt text-amber",
      error: "i-carbon-error-filled text-coral",
    }[level] || "i-carbon-information text-iolite"
  )
})

function confirmButtonType(style) {
  if (style === "primary") return "primary"
  if (style === "danger") return "danger"
  if (style === "link") return ""
  return ""
}

// ── Collapse state derivations ──────────────────────────────────
const interactive = computed(() => {
  if (props.message.uiEventType === "ask_text") return true
  if (props.message.uiEventType === "confirm") return true
  if (props.message.uiEventType === "selection") return true
  if (props.message.uiEventType === "card") return hasActions.value
  if (props.message.uiEventType === "notification") return Boolean(props.message.payload?.action)
  return false
})

// All event kinds support local collapse — including non-interactive
// progress / notification / card so users can quietly tidy chat.
const canCollapse = computed(() => true)

const collapsedIcon = computed(() => {
  switch (props.message.uiEventType) {
    case "ask_text":
      return "i-carbon-chat text-iolite"
    case "confirm":
      return "i-carbon-warning-alt text-amber"
    case "selection":
      return "i-carbon-list-checked text-iolite"
    case "progress":
      return props.message.payload?.complete ? "i-carbon-checkmark text-aquamarine" : "i-carbon-time text-iolite"
    case "notification":
      return notificationIconClass.value
    case "card":
      return "i-carbon-document text-warm-500"
    default:
      return "i-carbon-information text-warm-500"
  }
})

const collapsedSummary = computed(() => {
  switch (props.message.uiEventType) {
    case "ask_text":
      return props.message.payload?.prompt || "Input requested"
    case "confirm":
      return props.message.payload?.prompt || "Confirm"
    case "selection":
      return props.message.payload?.prompt || "Selection"
    case "progress":
      return props.message.payload?.label || "Progress"
    case "notification":
      return props.message.payload?.text || ""
    case "card":
      return props.message.payload?.title || "Card"
    default:
      return ""
  }
})

// ── Reply handlers ──────────────────────────────────────────────
function onSubmitText() {
  if (!textValue.value.trim()) return
  emit("reply", { actionId: "submit", values: { text: textValue.value } })
}

function onConfirmChoice(actionId) {
  emit("reply", { actionId, values: {} })
}

function onSubmitSelection() {
  const selected = props.message.payload?.multi ? multiSelectedValues.value : singleSelectedValue.value
  emit("reply", { actionId: "submit", values: { selected } })
}

function onCancel() {
  emit("reply", { actionId: "cancel", values: {} })
}
</script>

<style scoped>
.ui-event-wrapper {
  position: relative;
}
.ui-event-collapsed-wrapper {
  margin: 0.25rem 0;
}
.ui-event-minimize {
  position: absolute;
  top: 0.5rem;
  right: 0.5rem;
  width: 1.25rem;
  height: 1.25rem;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 0.25rem;
  color: var(--color-text-muted, rgba(160, 160, 160, 0.7));
  background: transparent;
  border: 0;
  cursor: pointer;
  font-size: 0.75rem;
  z-index: 1;
  transition:
    background 0.15s,
    color 0.15s;
}
.ui-event-minimize:hover {
  background: var(--color-card-hover, rgba(160, 160, 160, 0.12));
  color: var(--color-text, inherit);
}
.ui-event-collapsed-summary {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  padding: 0.375rem 0.625rem;
  border: 1px dashed var(--color-border, rgba(160, 160, 160, 0.2));
  border-radius: 0.375rem;
  color: var(--color-text-muted, rgba(160, 160, 160, 0.85));
  background: var(--color-card, rgba(160, 160, 160, 0.03));
}
.ui-event-card {
  border: 1px solid var(--color-border, rgba(160, 160, 160, 0.18));
  border-radius: 0.5rem;
  padding: 0.625rem 0.75rem;
  margin: 0.5rem 0;
  background: var(--color-card, rgba(160, 160, 160, 0.04));
}
.ui-event-card.ui-event-done {
  opacity: 0.55;
}
.ui-event-header {
  display: flex;
  align-items: center;
  gap: 0.4rem;
  margin-bottom: 0.25rem;
}
.ui-event-card.accent-warning {
  border-color: rgba(212, 146, 10, 0.4);
  background: rgba(212, 146, 10, 0.06);
}
.ui-event-card.accent-danger {
  border-color: rgba(231, 76, 60, 0.4);
  background: rgba(231, 76, 60, 0.06);
}
.ui-event-card.accent-success {
  border-color: rgba(76, 153, 137, 0.4);
  background: rgba(76, 153, 137, 0.06);
}
.ui-event-card.accent-info {
  border-color: rgba(15, 82, 186, 0.4);
  background: rgba(15, 82, 186, 0.06);
}
.ui-event-card.accent-primary {
  border-color: rgba(90, 79, 207, 0.4);
  background: rgba(90, 79, 207, 0.06);
}
.ui-event-card.accent-error {
  border-color: rgba(231, 76, 60, 0.4);
  background: rgba(231, 76, 60, 0.06);
}
.ui-event-progress {
  padding-bottom: 0.5rem;
}
</style>
