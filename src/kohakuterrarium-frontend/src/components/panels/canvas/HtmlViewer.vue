<template>
  <div class="h-full w-full flex flex-col bg-white dark:bg-warm-900">
    <div
      class="flex items-center gap-2 px-2 py-1 border-b border-warm-200 dark:border-warm-700 text-[10px] shrink-0"
    >
      <button
        class="px-2 py-0.5 rounded transition-colors"
        :class="
          mode === 'render' ? 'bg-iolite/15 text-iolite' : 'text-warm-500 hover:text-warm-700'
        "
        @click="mode = 'render'"
      >
        Render
      </button>
      <button
        class="px-2 py-0.5 rounded transition-colors"
        :class="
          mode === 'source' ? 'bg-iolite/15 text-iolite' : 'text-warm-500 hover:text-warm-700'
        "
        @click="mode = 'source'"
      >
        Source
      </button>
      <span class="text-warm-400 ml-2"> sandboxed · no scripts </span>
    </div>

    <div class="flex-1 min-h-0 overflow-auto">
      <!-- Sandboxed iframe, no allow-scripts. srcdoc avoids the
           need for object URLs and stays inert when the panel
           unmounts. -->
      <iframe
        v-if="mode === 'render'"
        :srcdoc="content"
        sandbox=""
        class="w-full h-full border-0 bg-white"
      />
      <pre
        v-else
        class="h-full m-0 p-3 text-[11px] font-mono text-warm-700 dark:text-warm-300 whitespace-pre-wrap break-words"
        >{{ content }}</pre
      >
    </div>
  </div>
</template>

<script setup>
import { ref } from "vue"

defineProps({
  content: { type: String, default: "" },
})

const mode = ref("render")
</script>
