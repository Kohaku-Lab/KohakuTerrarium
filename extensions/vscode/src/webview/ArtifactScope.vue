<template>
  <div ref="root" class="artifact-scope"><slot /></div>
</template>
<script setup>
import { inject, onBeforeUnmount, onMounted, ref } from 'vue'
import { observeArtifactImages } from './artifactImages.mjs'
const loader = inject('ktArtifactLoader', null)
const root = ref(null)
let stop
onMounted(() => {
  if (loader) stop = observeArtifactImages(root.value, loader)
})
onBeforeUnmount(() => stop?.())
</script>
<style scoped>
.artifact-scope {
  display: contents;
}
.artifact-scope :deep(img) {
  max-width: 100%;
  height: auto;
}
.artifact-scope :deep(.artifact-image-status) {
  font-size: 0.85em;
  color: var(--vscode-descriptionForeground);
}
</style>
