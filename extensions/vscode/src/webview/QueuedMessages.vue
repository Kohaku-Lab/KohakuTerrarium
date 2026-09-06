<template>
  <section v-if="items.length" class="kt-queued-messages" aria-label="Queued messages">
    <div role="status" aria-live="polite">{{ items.length }} queued message{{ items.length === 1 ? '' : 's' }}</div>
    <div v-for="item in visible" :key="item.eventId" class="kt-queued-item">
      <template v-if="editing === item">
        <textarea
          :ref="focusEditor"
          v-model="text"
          aria-label="Edit queued message text"
          :disabled="pending === item"
          @keydown.esc.prevent="editing = null"
          @keydown.ctrl.enter.prevent="save(item)"
          @keydown.meta.enter.prevent="save(item)"
        />
        <button aria-label="Save queued message" :disabled="blocked(item) || !editableParts(item).length" @click="save(item)">Save</button>
        <button :disabled="pending === item" @click="editing = null">Discard edit</button>
      </template>
      <template v-else>
        <span class="kt-queued-preview">{{ item.content || 'Attachment message' }}</span>
        <span v-for="(part, index) in files(item)" :key="index" class="kt-queued-file">{{ fileLabel(part) }}</span>
        <span v-if="item.queueActionUncertain">Outcome unknown — check server state before retrying</span>
        <span v-else-if="pending === item || item.cancelling">Awaiting confirmation</span>
        <span v-else>{{ item.backendQueued ? 'Queued' : 'Awaiting queue acknowledgement' }}</span>
        <button aria-label="Edit queued message" :disabled="blocked(item)" @click="start(item)">Edit</button>
        <button aria-label="Cancel queued message" :disabled="blocked(item)" @click="cancel(item)">Cancel</button>
      </template>
    </div>
    <button v-if="items.length > 3" @click="expanded = !expanded">{{ expanded ? 'Show fewer' : `Show ${items.length - 3} more` }}</button>
    <p v-if="!connected">Chat is disconnected. Queued input may still execute on the server.</p>
    <p class="kt-queue-lifetime">
      This view shows locally observed queued input only. Refresh or switching Creature clears the view, not the server queue.
    </p>
    <p v-if="error" role="alert">{{ error }}</p>
  </section>
</template>

<script setup>
import { computed, ref, watch } from 'vue'

const props = defineProps({
  items: { type: Array, default: () => [] },
  connected: { type: Boolean, default: false },
  edit: { type: Function, required: true },
  cancel: { type: Function, required: true },
})
const editing = ref(null)
const text = ref('')
const pending = ref(null)
const expanded = ref(false)
const error = ref('')
const visible = computed(() => (expanded.value ? props.items : props.items.slice(-3)))
const files = (item) => (item.contentParts || []).filter((part) => part.type !== 'text')
const fileLabel = (part) =>
  part.file?.filename || part.file?.name || part.filename || (part.type === 'image_url' ? 'Image attachment' : 'File attachment')
const blocked = (item) => !props.connected || !item.backendQueued || !!pending.value || item.cancelling || item.queueActionUncertain
let editor = null
const focusEditor = (element) => {
  if (element && element !== editor) element.focus()
  editor = element
}
const editableParts = (item) => [...(text.value.trim() ? [{ type: 'text', text: text.value }] : []), ...files(item)]
function start(item) {
  if (blocked(item)) return
  editing.value = item
  text.value =
    (item.contentParts || [])
      .filter((part) => part.type === 'text')
      .map((part) => part.text)
      .join('\n') || ''
  error.value = ''
}
async function act(item, operation) {
  if (blocked(item) || !props.items.includes(item)) return
  pending.value = item
  error.value = ''
  try {
    const status = await operation()
    if (status === 'already_sent') error.value = 'The message already entered processing; the requested change was not applied.'
    editing.value = null
  } catch (cause) {
    error.value = cause?.message || String(cause)
  } finally {
    pending.value = null
  }
}
const save = (item) => editableParts(item).length && act(item, () => props.edit(item, editableParts(item)))
const cancel = (item) => act(item, () => props.cancel(item))
watch(
  () => props.items.map((item) => item.eventId),
  () => {
    if (editing.value && !props.items.includes(editing.value)) editing.value = null
  },
)
</script>

<style scoped>
.kt-queued-messages {
  max-height: 35vh;
  overflow-y: auto;
  padding: 0.5rem;
  border-top: 1px solid var(--vscode-notificationsWarningIcon-foreground, #cca700);
}
.kt-queued-item {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 0.4rem;
  padding: 0.4rem;
  margin-top: 0.3rem;
  background: var(--vscode-inputValidation-warningBackground, #cca70020);
}
.kt-queued-preview {
  flex: 1 1 8rem;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.kt-queued-file {
  font-size: 0.85em;
  overflow-wrap: anywhere;
}
textarea {
  width: 100%;
  min-height: 3rem;
  background: var(--vscode-input-background);
  color: var(--vscode-input-foreground);
}
button {
  cursor: pointer;
  color: var(--vscode-button-foreground);
  background: var(--vscode-button-background);
  border: 1px solid transparent;
  padding: 0.2rem 0.4rem;
}
button:disabled {
  opacity: 0.5;
  cursor: default;
}
button:focus-visible,
textarea:focus-visible {
  outline: 2px solid var(--vscode-focusBorder, #007fd4);
  outline-offset: 1px;
}
</style>
