import { onBeforeUnmount, provide, watch } from 'vue'
import { createArtifactLoader } from './artifactImages.mjs'

export function useArtifactImages(request, getFence, getOwner) {
  const loader = createArtifactLoader({ request, getFence })
  provide('ktArtifactLoader', loader)
  watch(getOwner, () => loader.reset(), { flush: 'sync' })
  onBeforeUnmount(() => loader.dispose())
  return loader
}
