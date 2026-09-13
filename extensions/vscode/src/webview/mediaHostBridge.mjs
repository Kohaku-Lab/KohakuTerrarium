// Installs the Host-backed media resolver into the current component tree so
// every shared media leaf below resolves through ``media.prepare`` (and cancels
// an in-flight read on unmount). Kept out of the entry so the entry stays small
// and under the file-size guard.
import { fileReferencePath, provideMediaResolver } from '@kohakuterrarium/chat-ui'
import { ref, watch } from 'vue'
import { useI18n } from '@/utils/i18n'

import { createHostMediaResolver } from './mediaResources.mjs'

export function installHostMediaResolver({ request, getFence, getOwner, error }) {
  const { t } = useI18n()
  // Reactive ready/selection generation: bumped whenever the composer owner
  // (ready epoch + target identity) changes. A shared leaf watches it and
  // re-resolves an unchanged reference against the new fence rather than
  // replaying a spooled URI the Host has already superseded.
  const generation = ref(0)
  // ``file://`` references are decoded by the shared host-neutral helper so the
  // VS Code resolver derives the same local path the Dashboard's raw route uses.
  const resolver = createHostMediaResolver({
    request,
    getFence,
    getOwner,
    translate: t,
    generation,
    filePathOf: fileReferencePath,
  })
  resolver.onError = (cause) => (error.value = cause?.message || String(cause))
  if (typeof getOwner === 'function') watch(getOwner, () => (generation.value += 1), { flush: 'sync' })
  provideMediaResolver(resolver)
  return resolver
}
