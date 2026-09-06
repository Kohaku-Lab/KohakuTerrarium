<template>
  <div class="kt-chat-composer" :class="{ 'is-dragging': dragOver }" @dragenter.prevent="onDragEnter" @dragleave.prevent="onDragLeave" @dragover.prevent @drop.prevent.stop="onDrop">
    <div v-if="showAttachmentActions && attachments.length" class="kt-chat-composer__attachments">
      <span v-for="(attachment, index) in attachments" :key="attachment.id || `${attachment.name}:${index}`" class="kt-chat-composer__chip">
        <slot name="attachment-icon" :attachment="attachment"
          ><span aria-hidden="true">{{ attachment.kind === "image" ? "▧" : "▤" }}</span></slot
        >
        <span class="kt-chat-composer__name">{{ attachment.name }}</span>
        <button type="button" :aria-label="label('removeAttachment', { name: attachment.name })" :disabled="disabled" @click="removeAttachment(index)"><slot name="remove-icon">×</slot></button>
      </span>
    </div>

    <div class="kt-chat-composer__shell" :class="{ 'is-active': active }">
      <template v-if="showAttachmentActions">
        <input ref="imageInput" class="kt-chat-composer__file-input" type="file" accept="image/*" :disabled="disabled" @change="onFileChange($event, 'image')" />
        <input ref="fileInput" class="kt-chat-composer__file-input" type="file" :disabled="disabled" @change="onFileChange($event, 'file')" />
      </template>

      <button v-if="showAttachmentActions && compactMode && active" type="button" :aria-label="label('moreActions')" :title="label('moreActions')" :disabled="disabled" @click="secondaryOpen = !secondaryOpen"><slot name="more-icon">＋</slot></button>
      <template v-else-if="showAttachmentActions">
        <button type="button" :aria-label="label('attachFile')" :title="label('attachFile')" :disabled="disabled" @click="openFile"><slot name="file-icon">＋</slot></button>
        <button type="button" :aria-label="label('attachImage')" :title="label('attachImage')" :disabled="disabled" @click="openImage"><slot name="image-icon">▧</slot></button>
      </template>

      <slot name="suggestions" />
      <textarea ref="textarea" :value="modelValue" rows="1" :placeholder="placeholder" :aria-label="label('message', {}, placeholder || 'Message')" :aria-autocomplete="ariaAutocomplete" :aria-expanded="ariaExpanded" :aria-controls="ariaControls" :aria-activedescendant="ariaActivedescendant" :role="inputRole" :disabled="disabled" @input="onInput" @keydown="onKeydown" @paste="onPaste" @focus="onFocus" @blur="onBlur" />

      <template v-if="showContextActions && !(compactMode && active)">
        <button type="button" :aria-label="label('compact')" :title="label('compact')" :disabled="disabled || contextActionsDisabled" @click="$emit('compact')"><slot name="compact-icon">⇤</slot></button>
        <button type="button" :aria-label="label('clear')" :title="label('clear')" :disabled="disabled || contextActionsDisabled" @click="$emit('clear')"><slot name="clear-icon">⌫</slot></button>
      </template>
      <button v-if="processing" type="button" class="kt-chat-composer__primary" :aria-label="label('stop')" :title="label('stop')" :disabled="disabled" @click="$emit('interrupt')"><slot name="stop-icon">■</slot></button>
      <button v-else type="button" class="kt-chat-composer__primary" :aria-label="label('send')" :title="label('send')" :disabled="disabled || !canSubmit" @click="submit"><slot name="send-icon">➤</slot></button>

      <template v-if="compactMode && secondaryOpen">
        <div class="kt-chat-composer__menu-backdrop" @click="secondaryOpen = false" />
        <div class="kt-chat-composer__menu" @click.stop>
          <button type="button" :aria-label="label('attachFile')" @click="secondaryAction(openFile)">
            <slot name="file-icon">＋</slot><span>{{ label("attachFile") }}</span>
          </button>
          <button type="button" :aria-label="label('attachImage')" @click="secondaryAction(openImage)">
            <slot name="image-icon">▧</slot><span>{{ label("attachImage") }}</span>
          </button>
          <button v-if="showContextActions" type="button" :aria-label="label('compact')" :disabled="disabled || contextActionsDisabled" @click="secondaryAction(() => $emit('compact'))"><slot name="compact-icon">⇤</slot></button>
          <button v-if="showContextActions" type="button" :aria-label="label('clear')" :disabled="disabled || contextActionsDisabled" @click="secondaryAction(() => $emit('clear'))"><slot name="clear-icon">⌫</slot></button>
        </div>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed, nextTick, ref, watch } from "vue"

import { buildMessageParts, validateAttachments } from "./chatAttachments.js"
import { shouldSendOnEnter } from "./chatInput.js"
import "./chat-composer.css"

const props = defineProps({
  modelValue: { type: String, default: "" },
  processing: { type: Boolean, default: false },
  disabled: { type: Boolean, default: false },
  compactMode: { type: Boolean, default: false },
  showContextActions: { type: Boolean, default: true },
  contextActionsDisabled: { type: Boolean, default: false },
  showAttachmentActions: { type: Boolean, default: true },
  touch: { type: Boolean, default: false },
  managedSubmit: { type: Boolean, default: false },
  attachments: { type: Array, default: () => [] },
  maxAttachmentBytes: { type: Number, default: undefined },
  maxImageBytes: { type: Number, default: undefined },
  attachmentTransform: { type: Function, default: null },
  placeholder: { type: String, default: "" },
  labels: { type: Object, default: () => ({}) },
  ariaAutocomplete: { type: String, default: undefined },
  ariaExpanded: { type: [Boolean, String], default: undefined },
  ariaControls: { type: String, default: undefined },
  ariaActivedescendant: { type: String, default: undefined },
  inputRole: { type: String, default: undefined },
})
const emit = defineEmits(["update:modelValue", "submit", "interrupt", "update:attachments", "remove", "compact", "clear", "error", "input", "keydown", "focus", "blur", "paste", "drag-state"])
const textarea = ref(null)
const imageInput = ref(null)
const fileInput = ref(null)
const focused = ref(false)
const secondaryOpen = ref(false)
const dragOver = ref(false)
let dragDepth = 0

const active = computed(() => focused.value || props.modelValue.length > 0)
const canSubmit = computed(() => props.modelValue.trim().length > 0 || props.attachments.length > 0)
function label(key, values = {}, fallback = key) {
  let value = props.labels[key] || fallback
  for (const [name, replacement] of Object.entries(values)) value = value.replace(`{${name}}`, replacement)
  return value
}
function resize() {
  if (!textarea.value) return
  textarea.value.style.height = "auto"
  textarea.value.style.height = `${Math.min(textarea.value.scrollHeight, 128)}px`
}
function resetHeight() {
  if (textarea.value) textarea.value.style.height = "auto"
}
function focus() {
  textarea.value?.focus()
}
function onInput(event) {
  emit("update:modelValue", event.target.value)
  emit("input", event)
  resize()
}
function onFocus(event) {
  focused.value = true
  emit("focus", event)
}
function onBlur(event) {
  focused.value = false
  emit("blur", event)
}
function onKeydown(event) {
  emit("keydown", event)
  if (!event.defaultPrevented && !props.disabled && shouldSendOnEnter(event, { isCompact: props.compactMode, isTouch: props.touch })) {
    event.preventDefault()
    submit()
  }
}
async function submit() {
  if (props.disabled || !canSubmit.value) return
  const text = props.modelValue
  const attachments = [...props.attachments]
  if (props.managedSubmit) {
    emit("submit", { text, attachments })
    return
  }
  try {
    const parts = await buildMessageParts(text, attachments)
    emit("submit", { text, attachments, parts })
    emit("update:modelValue", "")
    emit("update:attachments", [])
    await nextTick()
    resetHeight()
  } catch (error) {
    emit("error", { code: "read-failed", error })
  }
}
function openFile() {
  fileInput.value?.click()
}
function openImage() {
  imageInput.value?.click()
}
function secondaryAction(fn) {
  secondaryOpen.value = false
  fn()
}
async function addFiles(files, kind, source = "file") {
  try {
    const normalized = await Promise.all(Array.from(files || []).map(async (file) => (await props.attachmentTransform?.(file, kind || undefined, source)) || file))
    const result = validateAttachments(normalized, { kind, maxAttachmentBytes: props.maxAttachmentBytes, maxImageBytes: props.maxImageBytes })
    for (const error of result.errors) emit("error", error)
    if (result.attachments.length) emit("update:attachments", [...props.attachments, ...result.attachments])
    return result.attachments.length > 0
  } catch (error) {
    if (!error?.silent) emit("error", { code: "read-failed", error })
    return false
  }
}
function onFileChange(event, kind) {
  addFiles(event.target.files, kind, "file")
  event.target.value = ""
}
function removeAttachment(index) {
  emit("remove", index)
  emit(
    "update:attachments",
    props.attachments.filter((_, itemIndex) => itemIndex !== index),
  )
}
async function onPaste(event) {
  emit("paste", event)
  if (event.defaultPrevented || props.disabled || !event.clipboardData) return
  const files = Array.from(event.clipboardData.files || [])
  if (!files.length) {
    for (const item of Array.from(event.clipboardData.items || [])) {
      if (item.kind === "file") {
        const file = item.getAsFile()
        if (file) files.push(file)
      }
    }
  }
  if (files.length) {
    event.preventDefault()
    await addFiles(files, undefined, "paste")
  }
}
function setDragOver(value) {
  dragOver.value = value
  emit("drag-state", value)
}
function onDragEnter(event) {
  if (props.disabled || !Array.from(event.dataTransfer?.types || []).includes("Files")) return
  dragDepth += 1
  setDragOver(true)
}
function onDragLeave() {
  dragDepth = Math.max(0, dragDepth - 1)
  if (!dragDepth) setDragOver(false)
}
function onDrop(event) {
  dragDepth = 0
  setDragOver(false)
  if (!props.disabled) addFiles(event.dataTransfer?.files || [], undefined, "drop")
}
watch(
  () => props.modelValue,
  () => nextTick(resize),
)
defineExpose({ textarea, focus, resize, resetHeight, openFile, openImage, addFiles })
</script>
