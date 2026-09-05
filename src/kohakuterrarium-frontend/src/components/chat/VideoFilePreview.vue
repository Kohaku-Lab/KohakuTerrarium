<template>
  <div class="rounded-lg border border-aquamarine/20 bg-black/90 overflow-hidden max-w-3xl">
    <video v-if="url" :src="url" controls preload="metadata" class="block w-full max-h-[32rem] bg-black">
      <a :href="url">{{ name }}</a>
    </video>
    <a v-if="url" :href="url" download class="flex items-center gap-1 px-3 py-2 text-xs text-aquamarine hover:underline bg-warm-950/80">
      <span class="i-carbon-download" />
      {{ name }}
    </a>
  </div>
</template>

<script setup>
import { computed } from "vue"
import { mediaSourceUrl } from "@/utils/artifacts"

const props = defineProps({
  file: { type: Object, required: true },
})

const url = computed(() => mediaSourceUrl(props.file?.path))
const name = computed(() => props.file?.name || "video.mp4")
</script>
