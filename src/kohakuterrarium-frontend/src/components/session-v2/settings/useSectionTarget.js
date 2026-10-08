/**
 * Shared plumbing of the per-creature settings sections.
 *
 * - `useSectionTarget(session)`: the creature a section targets — starts on
 *   the creature whose conversation is open in chat, falls back when that
 *   creature leaves the session, and is the user's to change from there.
 * - `useSectionLoad(load, deps)`: runs `load(isCurrent)` on mount, when
 *   `deps` change, and when the (kept-alive) Settings tab is shown again;
 *   `isCurrent()` turns false once a newer load started, so stale responses
 *   are dropped.
 * - `onShownAgain(fn)`: runs `fn` when a kept-alive section is shown after
 *   being hidden (never on first mount, which may come after activation).
 */

import { computed, onActivated, onDeactivated, ref, watch } from "vue"

import { creatureNames, defaultTarget } from "@/components/session-v2/model/settings/settingsModel"

export function useSectionTarget(session) {
  const pick = () =>
    defaultTarget(session.instance.value, session.chat.activeTab, session.chat._rootSourceName)
  const target = ref(pick())
  const names = computed(() => creatureNames(session.instance.value))
  watch(names, (list) => {
    if (!list.includes(target.value)) target.value = pick()
  })
  return target
}

export function onShownAgain(fn) {
  let hidden = false
  onDeactivated(() => (hidden = true))
  onActivated(() => {
    if (!hidden) return
    hidden = false
    fn()
  })
}

export function useSectionLoad(load, deps) {
  let seq = 0
  const run = () => {
    const id = ++seq
    return load(() => id === seq)
  }
  watch(deps, run, { immediate: true })
  onShownAgain(run)
  return run
}
