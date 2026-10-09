<template>
  <div class="flex flex-col gap-1.5">
    <div v-if="queue.length" class="flex flex-col gap-1.5">
      <div v-for="qm in visibleQueue" :key="qm.id" class="flex items-center gap-2 px-3 py-1.5 kt-v2-b rounded-lg bg-amber/5 border border-amber/20 text-sm" :class="{ 'opacity-50': qm.cancelling }">
        <span class="i-carbon-time text-amber/60 text-xs shrink-0" />
        <template v-if="editingId === qm.eventId">
          <input v-model="editText" class="flex-1 min-w-0 kt-v2-b bg-transparent border border-amber/30 rounded px-2 py-0.5 text-sm text-warm-800 dark:text-warm-100 focus:outline-none focus:border-amber" @keydown.enter.prevent="saveEdit(qm)" @keydown.esc.stop.prevent="editingId = null" />
          <button class="text-xs text-iolite dark:text-iolite-light hover:underline shrink-0" @click="saveEdit(qm)">{{ it("common.save") }}</button>
          <button class="text-xs text-warm-500 hover:underline shrink-0" @click="editingId = null">{{ t("composer.cancelEdit") }}</button>
        </template>
        <template v-else>
          <span class="text-warm-500 dark:text-warm-400 truncate flex-1">{{ qm.content }}</span>
          <span class="text-warm-300 dark:text-warm-600 text-xs shrink-0">{{ it("chat.queued") }}</span>
          <button class="i-carbon-edit text-warm-400 hover:text-iolite text-sm shrink-0" :title="it('chat.queueEdit')" :disabled="qm.cancelling" @click="startEdit(qm)" />
          <button class="i-carbon-close text-warm-400 hover:text-coral text-sm shrink-0" :title="it('chat.queueCancel')" :disabled="qm.cancelling" @click="chat.cancelQueuedMessage(tabKey, qm.eventId)" />
        </template>
      </div>
      <button v-if="queue.length > QUEUE_VISIBLE" class="self-start text-xs text-amber-shadow dark:text-amber-light hover:underline" @click="queueExpanded = !queueExpanded">
        {{ queueExpanded ? it("chat.queueCollapse") : it("chat.queueShowMore", { count: queue.length - QUEUE_VISIBLE }) }}
      </button>
    </div>

    <div v-if="pendingCount" class="flex items-center gap-2 px-2.5 py-1.5 kt-v2-b rounded-lg bg-amber/10 border border-amber/30 text-xs">
      <span class="i-carbon-warning-alt text-amber" />
      <span class="text-amber-shadow dark:text-amber-light">{{ it("chat.pendingBanner", { count: pendingCount }) }}</span>
      <button class="ml-auto text-amber-shadow dark:text-amber-light hover:underline" @click="emit('show-pending', pending[pending.length - 1].id)">{{ t("composer.pendingShow") }}</button>
    </div>

    <ChatComposer :ref="setComposerEl" v-model="inputText" v-model:attachments="attachments" :processing="composer.processing.value" :compact-mode="isCompact" :managed-submit="true" :max-attachment-bytes="MAX_ATTACHMENT_BYTES" :max-image-bytes="MAX_IMAGE_BYTES" :placeholder="composer.placeholder.value" :labels="composer.labels.value" aria-autocomplete="list" :aria-expanded="slash.open.value" aria-controls="v2-slash-menu" :aria-activedescendant="slash.activeDescendant.value" input-role="combobox" :attachment-transform="composer.transformAttachment" @update:attachments="composer.onAttachmentsChanged" @submit="composer.send" @interrupt="composer.interrupt" @compact="composer.compact" @clear="composer.clear" @error="composer.onAttachmentError" @input="composer.onInputChanged" @keydown="onKeydown" @focus="slash.reopen()">
      <template #toolbar>
        <ComposerModelPicker />
        <span class="flex-1" />
        <ComposerContextMeter />
      </template>
      <template #suggestions><SlashCommandMenu id="v2-slash-menu" :open="slash.open.value" :loading="slash.loading.value" :error="slash.error.value" :entries="slash.entries.value" :selected-index="slash.selectedIndex.value" @choose="slash.choose" @select-index="slash.selectedIndex.value = $event" /></template>
      <template #attachment-icon="{ attachment }"><span :class="attachment.kind === 'image' ? 'i-carbon-image text-iolite dark:text-iolite-light' : 'i-carbon-document text-aquamarine'" /></template>
      <template #remove-icon><span class="i-carbon-close" /></template>
      <template #file-icon><span class="i-carbon-add" /></template>
      <template #image-icon><span class="i-carbon-image" /></template>
      <template #more-icon><span class="i-carbon-add" /></template>
      <template #compact-icon><span class="i-carbon-collapse-all" /></template>
      <template #clear-icon><span class="i-carbon-clean" /></template>
      <template #stop-icon><span class="i-carbon-stop-filled" /></template>
      <template #send-icon><span class="i-carbon-send" /></template>
    </ChatComposer>
  </div>
</template>

<script setup>
import { computed, ref } from "vue"

import SlashCommandMenu from "@/components/chat/SlashCommandMenu.vue"
import ComposerContextMeter from "@/components/session-v2/chat/ComposerContextMeter.vue"
import ComposerModelPicker from "@/components/session-v2/chat/ComposerModelPicker.vue"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import { useDensity } from "@/composables/useDensity"
import { ChatComposer, handleSlashKeydown, shouldSendOnEnter } from "@kohakuterrarium/chat-ui"
import { MAX_ATTACHMENT_BYTES, MAX_IMAGE_BYTES } from "@/utils/chatAttachments"
import { useI18n } from "@/utils/i18n"

/**
 * The v2 composer: queued messages, the pending-question banner, and the
 * chat-ui composer with the slash menu, the model pill and the context
 * meter in its toolbar. State and actions come from the
 * column's `useComposerSend` (the `composer` prop). Emits `show-pending`
 * with the newest unanswered question's message id.
 */
const props = defineProps({
  composer: { type: Object, required: true },
  chat: { type: Object, required: true },
  tabKey: { type: String, default: null },
})
const emit = defineEmits(["show-pending"])

const QUEUE_VISIBLE = 5
const { t: it } = useI18n()
const t = useV2T()
const { isCompact } = useDensity()
const { slash, inputText, attachments, composerEl } = props.composer
const setComposerEl = (el) => (composerEl.value = el)
const queueExpanded = ref(false)
const editingId = ref(null)
const editText = ref("")

const queue = computed(() => (props.tabKey ? props.chat.queuedMessagesByTab[props.tabKey] || [] : []))
const visibleQueue = computed(() => (queueExpanded.value ? queue.value : queue.value.slice(0, QUEUE_VISIBLE)))

const pending = computed(() => {
  const list = props.tabKey ? props.chat.messagesByTab?.[props.tabKey] || [] : []
  return list.filter((m) => m.role === "ui_event" && m.interactive && !m.replied && !m.superseded && !m.timedOut)
})
// Shown only while there is a draft that would not answer them.
const pendingCount = computed(() => (inputText.value ? pending.value.length : 0))

function startEdit(qm) {
  editingId.value = qm.eventId
  editText.value = qm.content || ""
}

function saveEdit(qm) {
  if (editText.value.trim()) props.chat.editQueuedMessage(props.tabKey, qm.eventId, editText.value)
  editingId.value = null
}

function onKeydown(e) {
  const handled = handleSlashKeydown(e, {
    open: slash.open.value,
    entries: slash.entries.value,
    selectedIndex: slash.selectedIndex.value,
    move: slash.move,
    choose: slash.choose,
    dismiss: slash.dismiss,
  })
  if (handled) return
  if (shouldSendOnEnter(e, { isCompact: isCompact.value })) {
    e.preventDefault()
    props.composer.send()
  }
}
</script>

<style scoped src="../../chat/chat-panel.css"></style>
