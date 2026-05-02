<template>
  <!--
    Brand mark for the macro shell rail. Uses the public/kohaku-icon.png
    asset in non-test environments (the original NavRail's brand) and
    falls back to an inline gradient when the asset can't be fetched
    (e.g. in jsdom unit tests).
  -->
  <div class="relative overflow-hidden bg-gradient-to-br from-amber via-iolite-light to-iolite">
    <img v-if="showImg" :src="iconSrc" alt="Kohaku" class="absolute inset-0 w-full h-full object-cover" @error="onError" />
  </div>
</template>

<script setup>
import { ref } from "vue"

const showImg = ref(true)
const iconSrc = "/kohaku-icon.png"

// Some test runners (jsdom with file:// resource loader) reject the
// absolute /kohaku-icon.png URL. Swap to the gradient fallback in
// that case so component-mount tests don't choke.
function onError() {
  showImg.value = false
}

if (typeof window === "undefined" || window.location.protocol === "file:") {
  showImg.value = false
}
</script>
