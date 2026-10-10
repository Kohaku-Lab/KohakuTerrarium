<template>
  <div class="kt-v2-canvas h-full flex min-h-0" data-test="v2-workspace">
    <div v-if="filesOpen" class="relative shrink-0 flex min-h-0" :style="{ width: `${files.width.value}px` }" data-test="v2-ws-files">
      <FileColumn class="kt-v2-panel kt-v2-edge flex-1 min-w-0 border-r" @collapse="setFiles(false)" @open-file="onOpenFile" @open-diff="onOpenDiff" />
      <div class="kt-v2-grip -right-[5px]" :class="{ 'is-dragging': files.dragging.value }" :title="t('ws.resize')" data-test="v2-ws-files-grip" @pointerdown="files.startDrag" @dblclick="files.reset" />
    </div>
    <button v-else class="kt-v2-panel kt-v2-edge w-8 shrink-0 flex flex-col items-center gap-2 pt-2 border-r text-warm-400 hover:text-warm-700 dark:hover:text-warm-200" :title="t('ws.showFiles')" data-test="v2-files-expand" @click="setFiles(true)">
      <span class="i-carbon-side-panel-open" />
      <span class="i-carbon-folder" />
    </button>

    <EditorColumn ref="editorEl" class="kt-v2-canvas flex-1" />

    <section v-if="chatOpen" class="kt-v2-canvas kt-v2-edge relative shrink-0 min-h-0 flex flex-col border-l" :style="{ width: `${chat.width.value}px` }" data-test="v2-ws-chat">
      <div class="kt-v2-grip -left-[5px]" :class="{ 'is-dragging': chat.dragging.value }" :title="t('ws.resize')" data-test="v2-ws-chat-grip" @pointerdown="chat.startDrag" @dblclick="chat.reset" />
      <header class="kt-v2-panel kt-v2-line h-10 shrink-0 flex items-center gap-2 px-3 border-b text-xs font-medium text-warm-700 dark:text-warm-200">
        <span class="i-carbon-chat" />
        <span class="flex-1">{{ t("tab.chat") }}</span>
        <button class="i-carbon-side-panel-close text-sm text-warm-400 hover:text-warm-700 dark:hover:text-warm-200 rotate-180" :title="t('ws.hideChat')" @click="setChat(false)" />
      </header>
      <div class="flex-1 min-h-0 flex flex-col">
        <ChatColumn narrow />
      </div>
    </section>
    <button v-else class="kt-v2-panel kt-v2-edge w-8 shrink-0 flex flex-col items-center gap-2 pt-2 border-l text-warm-400 hover:text-warm-700 dark:hover:text-warm-200" :title="t('ws.showChat')" data-test="v2-chat-expand" @click="setChat(true)">
      <span class="i-carbon-side-panel-open rotate-180" />
      <span class="i-carbon-chat" />
    </button>
  </div>
</template>

<script setup>
import { ref } from "vue"

import ChatColumn from "@/components/session-v2/chat/ChatColumn.vue"
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import EditorColumn from "@/components/session-v2/workspace/EditorColumn.vue"
import FileColumn from "@/components/session-v2/workspace/FileColumn.vue"
import { useResizableWidth } from "@/composables/useResizableWidth"
import { readLocalPref, writeLocalPref } from "@/utils/uiPrefs"

/**
 * The Workspace tab: files | editor | the session's chat. Files and chat
 * collapse per session and resize by their grips (widths shared by every
 * session).
 */
const t = useV2T()
const session = useSessionV2()
const editorEl = ref(null)
const files = useResizableWidth({ key: "kt.v2.ws.filesWidth", min: 160, max: 520, initial: 256, edge: "right" })
const chat = useResizableWidth({ key: "kt.v2.ws.chatWidth", min: 300, max: 760, initial: 416, edge: "left" })

const key = (part) => `kt.v2.ws.${session.instanceId.value}.${part}`
const filesOpen = ref(readLocalPref(key("files")) !== "0")
const chatOpen = ref(readLocalPref(key("chat")) !== "0")

function setFiles(open) {
  filesOpen.value = open
  writeLocalPref(key("files"), open ? "1" : "0")
}

function setChat(open) {
  chatOpen.value = open
  writeLocalPref(key("chat"), open ? "1" : "0")
}

function onOpenFile(path) {
  editorEl.value?.openFile(path)
}

function onOpenDiff(diff) {
  editorEl.value?.showDiff(diff)
}
</script>
