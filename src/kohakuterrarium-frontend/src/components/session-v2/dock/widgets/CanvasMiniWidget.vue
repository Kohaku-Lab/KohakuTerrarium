<template>
  <div class="h-full min-h-0 overflow-y-auto p-2" data-test="v2-widget-canvas">
    <div v-if="!canvas.artifacts.length" class="px-1 py-6 text-center text-xs text-warm-400">{{ t("widget.canvas.empty") }}</div>
    <div class="grid gap-2" :class="mode === 'side' ? 'grid-cols-3' : 'grid-cols-2'">
      <button v-for="(a, index) in canvas.artifacts" :key="a.id" class="flex flex-col rounded-lg border kt-v2-line overflow-hidden text-left hover:border-iolite/60 transition-colors" :class="canvas.activeId === a.id ? 'ring-1 ring-iolite/50' : ''" :title="a.name" @click="open(index)">
        <div class="h-20 flex items-center justify-center bg-warm-100 dark:bg-warm-800 overflow-hidden">
          <img v-if="a.type === 'image'" :src="a.content" :alt="a.name" class="w-full h-full object-cover" loading="lazy" />
          <pre v-else class="w-full h-full p-1.5 text-[8px] leading-[10px] font-mono text-warm-500 overflow-hidden whitespace-pre">{{ head(a) }}</pre>
        </div>
        <div class="flex items-center gap-1 px-1.5 py-1 min-w-0">
          <span :class="artifactIcon(a.type)" class="text-[11px] text-warm-400 shrink-0" />
          <span class="text-[11px] text-warm-700 dark:text-warm-200 truncate">{{ a.name }}</span>
        </div>
      </button>
    </div>
  </div>
</template>

<script setup>
import { useSessionV2 } from "@/components/session-v2/model/sessionContext"
import { useV2T } from "@/components/session-v2/model/v2Strings"
import { artifactIcon } from "@/components/session-v2/model/widgets/widgetData"
import { useCanvasStore } from "@/stores/canvas"

/** Thumbnails of the session's canvas artifacts; one opens in the canvas side view. */
defineProps({ mode: { type: String, default: "widget" } })

const ctx = useSessionV2()
const t = useV2T()
const canvas = useCanvasStore(ctx.instanceId.value)

// The first lines of a text artifact, as its thumbnail.
function head(a) {
  return typeof a.content === "string" ? a.content.slice(0, 400) : ""
}

function open(index) {
  ctx.closeWidget()
  ctx.openSide("canvas", { index })
}
</script>
