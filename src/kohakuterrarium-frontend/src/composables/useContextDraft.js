import { computed, ref, toValue, watch } from "vue"

import { contextSuggestions, contextText, exceedsWindow, parseContext } from "@/utils/contextLength"

/**
 * The context-length field of a model switcher. The field shows the
 * draft model's effective length (`defaultContext`) until the user types or
 * picks one, which pins it; only a pinned length becomes `context=<n>` in
 * the selector. Clearing the field unpins it, meaning "preset default".
 * `contextWindow` is the model's known window, used for the warning only.
 */
export function useContextDraft(defaultContext, contextWindow) {
  const text = ref("")
  const pinned = ref(false)
  const value = computed(() => parseContext(text.value))
  const invalid = computed(() => pinned.value && value.value === null)
  const override = computed(() => (pinned.value ? value.value : null))
  const window = computed(() => parseContext(toValue(contextWindow)))
  const exceeds = computed(() => exceedsWindow(value.value, window.value))
  const suggestions = computed(() => contextSuggestions(window.value))
  const placeholder = computed(() => contextText(toValue(defaultContext)))

  function prefill() {
    pinned.value = false
    text.value = contextText(toValue(defaultContext))
  }

  /** Restarts from a selector's `context=` value, or the default when it has none. */
  function start(raw) {
    if (parseContext(raw)) {
      pinned.value = true
      text.value = String(raw)
    } else prefill()
  }

  function edit(next) {
    text.value = String(next ?? "")
    pinned.value = text.value.trim() !== ""
  }

  watch(
    () => toValue(defaultContext),
    () => {
      if (!pinned.value) prefill()
    },
  )

  return {
    text,
    pinned,
    value,
    invalid,
    override,
    window,
    exceeds,
    suggestions,
    placeholder,
    start,
    edit,
    pick: (count) => edit(contextText(count)),
    clear: () => edit(""),
  }
}
