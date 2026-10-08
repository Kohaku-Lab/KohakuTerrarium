<template>
  <div class="h-full flex flex-col min-h-0" data-test="v2-side-canvas">
    <div v-if="!canvas.artifacts.length" class="flex-1 flex items-center justify-center px-6 text-center text-xs text-warm-400">{{ t("side.canvas.empty") }}</div>
    <template v-else>
      <div class="shrink-0 flex items-center gap-0.5 px-2 h-9 border-b kt-v2-line overflow-x-auto">
        <button v-for="a in canvas.artifacts" :key="a.id" class="group flex items-center gap-1 h-6 px-2 rounded text-[11px] shrink-0 transition-colors" :class="canvas.activeId === a.id ? 'bg-iolite/15 text-iolite' : 'text-warm-500 hover:text-warm-800 dark:hover:text-warm-200 hover:bg-warm-100 dark:hover:bg-warm-800'" :title="a.name" @click="canvas.setActive(a.id)">
          <span :class="artifactIcon(a.type)" />
          <span class="truncate max-w-36">{{ a.name }}</span>
          <span class="i-carbon-close text-[10px] opacity-0 group-hover:opacity-60 hover:!opacity-100" :title="t('side.canvas.remove')" @click.stop="canvas.dismissArtifact(a.id)" />
        </button>
      </div>
      <div class="flex-1 min-h-0">
        <ArtifactViewer v-if="active" :key="active.id" :artifact="active" :source="showSource" />
      </div>
      <div v-if="active" class="shrink-0 flex items-center gap-2 px-3 h-9 border-t kt-v2-line text-[11px] text-warm-500">
        <span class="font-mono">{{ active.lang || active.type }}</span>
        <span v-if="active.type !== 'image'">· {{ t("side.canvas.lines", { n: lineCount }) }}</span>
        <span class="flex-1" />
        <button v-if="active.type === 'html' || active.type === 'markdown'" class="h-6 px-2 rounded hover:bg-warm-100 dark:hover:bg-warm-800" :class="showSource ? 'text-iolite' : ''" @click="showSource = !showSource"><span class="i-carbon-code" /></button>
        <button class="h-6 px-2 rounded hover:bg-warm-100 dark:hover:bg-warm-800 flex items-center gap-1" @click="copy"><span class="i-carbon-copy" />{{ t("side.canvas.copy") }}</button>
        <button class="h-6 px-2 rounded hover:bg-warm-100 dark:hover:bg-warm-800 flex items-center gap-1" @click="download"><span class="i-carbon-download" />{{ t("side.canvas.download") }}</button>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, ref, watch } from "vue"

import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import { artifactFileName, artifactIcon } from "@/components/session-v2/model/widgets/widgetData"
import ArtifactViewer from "@/components/session-v2/side/canvas/ArtifactViewer.vue"
import { useCanvasStore } from "@/stores/canvas"

/** The canvas beside the chat: artifact tabs, the active artifact full size, copy and download. `payload.index` picks one. */
const props = defineProps({ payload: { type: Object, default: () => ({}) } })

const ctx = useSessionV2()
const t = useV2T()
const canvas = useCanvasStore(ctx.instanceId.value)
const showSource = ref(false)

const active = computed(() => canvas.activeArtifact)
const lineCount = computed(() => (typeof active.value?.content === "string" ? active.value.content.split("\n").length : 0))

watch(
  () => props.payload,
  (payload) => {
    const index = payload?.index
    const a = index != null ? canvas.artifacts[index] : null
    if (a) canvas.setActive(a.id)
  },
  { immediate: true },
)
watch(
  () => active.value?.id,
  () => (showSource.value = false),
)

function copy() {
  const a = active.value
  if (!a?.content) return
  if (a.type === "image" && navigator.clipboard?.write && typeof window.ClipboardItem === "function") {
    fetch(a.content)
      .then((r) => r.blob())
      .then((blob) => navigator.clipboard.write([new ClipboardItem({ [blob.type || "image/png"]: blob })]))
      .catch(() => navigator.clipboard?.writeText(a.content).catch(() => {}))
    return
  }
  navigator.clipboard?.writeText(a.content).catch(() => {})
}

function download() {
  const a = active.value
  if (!a?.content) return
  const link = document.createElement("a")
  link.download = artifactFileName(a)
  if (a.type === "image") {
    link.href = a.content
    link.click()
    return
  }
  const url = URL.createObjectURL(new Blob([a.content], { type: "text/plain" }))
  link.href = url
  link.click()
  setTimeout(() => URL.revokeObjectURL(url), 0)
}
</script>
