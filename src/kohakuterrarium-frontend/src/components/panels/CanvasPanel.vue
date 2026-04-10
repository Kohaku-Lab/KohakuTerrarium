<template>
  <div class="h-full flex flex-col bg-white dark:bg-warm-900 overflow-hidden">
    <!-- Tab strip + actions -->
    <div
      class="flex items-center gap-0.5 px-2 h-8 border-b border-warm-200 dark:border-warm-700 overflow-x-auto shrink-0 text-[11px]"
    >
      <div
        v-if="canvas.artifacts.length === 0"
        class="text-warm-400 italic"
      >
        No artifacts yet
      </div>
      <button
        v-for="a in canvas.artifacts"
        :key="a.id"
        class="flex items-center gap-1 px-2 py-0.5 rounded transition-colors shrink-0"
        :class="canvas.activeId === a.id
          ? 'bg-iolite/15 text-iolite'
          : 'text-warm-500 hover:text-warm-700 dark:hover:text-warm-300 hover:bg-warm-100 dark:hover:bg-warm-800'"
        :title="`${a.name} · ${a.type}`"
        @click="canvas.setActive(a.id)"
      >
        <span :class="typeIcon(a.type)" class="text-[11px]" />
        <span class="truncate max-w-32">{{ a.name }}</span>
      </button>

      <div class="flex-1" />

      <!-- Copy + Download buttons (only when an artifact is active) -->
      <template v-if="canvas.activeArtifact">
        <button
          class="w-6 h-6 flex items-center justify-center rounded text-warm-400 hover:text-warm-600 dark:hover:text-warm-300 transition-colors shrink-0"
          title="Copy to clipboard"
          @click="copyContent"
        >
          <div class="i-carbon-copy text-[12px]" />
        </button>
        <button
          class="w-6 h-6 flex items-center justify-center rounded text-warm-400 hover:text-warm-600 dark:hover:text-warm-300 transition-colors shrink-0"
          title="Download file"
          @click="downloadContent"
        >
          <div class="i-carbon-download text-[12px]" />
        </button>
      </template>
    </div>

    <!-- Viewer -->
    <div class="flex-1 min-h-0 overflow-hidden">
      <div
        v-if="!canvas.activeArtifact"
        class="h-full flex items-center justify-center text-warm-400 text-xs"
      >
        <div class="text-center">
          <div class="i-carbon-canvas text-3xl mb-2 mx-auto opacity-30" />
          <p>Artifacts appear here automatically.</p>
          <p class="text-[10px] mt-1 opacity-70">
            Long code blocks (&gt;= 15 lines) or
            <code class="font-mono">##canvas##</code> markers.
          </p>
        </div>
      </div>

      <CodeViewer
        v-else-if="viewerType === 'code' || viewerType === 'svg' || viewerType === 'diagram'"
        :content="canvas.activeArtifact.content"
        :lang="canvas.activeArtifact.lang"
      />
      <MarkdownViewer
        v-else-if="viewerType === 'markdown'"
        :content="canvas.activeArtifact.content"
      />
      <HtmlViewer
        v-else-if="viewerType === 'html'"
        :content="canvas.activeArtifact.content"
      />
      <CodeViewer
        v-else
        :content="canvas.activeArtifact.content"
        :lang="canvas.activeArtifact.lang"
      />
    </div>

    <!-- Bottom info strip -->
    <div
      v-if="canvas.activeArtifact"
      class="flex items-center gap-2 px-2 h-6 border-t border-warm-200 dark:border-warm-700 text-[10px] text-warm-500 shrink-0"
    >
      <span class="font-mono">{{ canvas.activeArtifact?.lang || 'text' }}</span>
      <span class="opacity-50">·</span>
      <span>{{ lineCount }} lines</span>
    </div>
  </div>
</template>

<script setup>
import { computed } from "vue";

import CodeViewer from "@/components/panels/canvas/CodeViewer.vue";
import HtmlViewer from "@/components/panels/canvas/HtmlViewer.vue";
import MarkdownViewer from "@/components/panels/canvas/MarkdownViewer.vue";
import { useCanvasStore } from "@/stores/canvas";

const canvas = useCanvasStore();

const viewerType = computed(() => canvas.activeArtifact?.type || "code");
const lineCount = computed(() => {
  const c = canvas.activeArtifact?.content;
  return c ? c.split("\n").length : 0;
});

function typeIcon(t) {
  return {
    code: "i-carbon-code",
    markdown: "i-carbon-document",
    html: "i-carbon-html",
    svg: "i-carbon-image",
    diagram: "i-carbon-flow-connection",
    table: "i-carbon-data-table",
  }[t] || "i-carbon-document";
}

function copyContent() {
  const text = canvas.activeArtifact?.content;
  if (!text) return;
  navigator.clipboard?.writeText(text).catch(() => {});
}

function downloadContent() {
  const art = canvas.activeArtifact;
  const ver = canvas.activeArtifact;
  if (!art || !ver) return;
  const ext = {
    code: ver.lang || "txt",
    markdown: "md",
    html: "html",
    svg: "svg",
    diagram: "mmd",
    table: "csv",
  }[art.type] || "txt";
  const name = (art.name || "artifact").replace(/[^a-zA-Z0-9_.-]/g, "_") + "." + ext;
  const blob = new Blob([ver.content], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}
</script>
