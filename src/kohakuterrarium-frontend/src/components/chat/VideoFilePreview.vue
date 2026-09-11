<template>
  <div class="rounded-lg border border-aquamarine/20 bg-black/90 overflow-hidden max-w-3xl">
    <p v-if="state === 'error'" class="kt-media-status is-error" role="status">{{ error }}</p>
    <p v-else-if="state === 'loading'" class="kt-media-status" role="status">{{ labels.loading }}</p>
    <template v-else-if="url">
      <video :src="url" controls preload="metadata" class="block w-full max-h-[32rem] bg-black" @error="onPlaybackError">
        <a :href="url">{{ name }}</a>
      </video>
      <p v-if="playbackError" class="kt-media-status is-error" role="status">{{ playbackError }}</p>
      <div class="flex items-center gap-2 px-3 py-2 text-xs bg-warm-950/80">
        <button v-if="canOpen" type="button" class="text-aquamarine hover:underline" @click="open()">{{ labels.open }}</button>
        <button v-if="canSave" type="button" class="text-aquamarine hover:underline" @click="save()">{{ labels.save }}</button>
        <a :href="url" download class="flex items-center gap-1 text-aquamarine hover:underline">
          <span class="i-carbon-download" />
          {{ name }}
        </a>
      </div>
    </template>
  </div>
</template>

<script setup>
import { computed, ref } from "vue"

import { useMediaResource } from "@/public/chat/mediaResolver.js"
import { useI18n } from "@/utils/i18n"

const props = defineProps({
  file: { type: Object, required: true },
  // Optional per-instance overrides; the production default resolves through the
  // shared ``chat.media.*`` dictionary so BOTH hosts (Dashboard + VS Code
  // webview) render genuine localized labels instead of hardcoded English.
  labels: { type: Object, default: () => ({}) },
})

const { t } = useI18n()
const name = computed(() => props.file?.name || "video.mp4")
// The URL is host-resolved: the browser resolver returns the direct same-origin
// route (a synchronous no-lease URL), while the VS Code resolver returns the
// Host-spooled webview URI and exposes the explicit open/save controls.
const { url, state, error, canOpen, canSave, open, save } = useMediaResource(() => props.file?.path, {
  kind: "media",
  name: () => name.value,
})

const labels = computed(() => ({
  loading: props.labels?.loading || t("chat.media.loading"),
  open: props.labels?.open || t("chat.media.open"),
  save: props.labels?.save || t("chat.media.save"),
  unavailable: props.labels?.unavailable || t("chat.media.videoUnavailable"),
}))

// A codec the host view cannot play surfaces an honest status instead of a
// silently blank frame; the explicit open/save/download actions stay available
// so the media is never stranded behind a broken inline preview.
const playbackError = ref("")
function onPlaybackError() {
  playbackError.value = labels.value.unavailable
}
</script>
