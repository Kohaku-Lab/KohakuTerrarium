// Host adapters for clipboard, UI replies, site adornment and Markdown origin.
// An optional getViewOwner identity supplements shared instance, tab and row guards.
import { inject, provide } from "vue"

export const MESSAGE_ACTIONS_KEY = "ktMessageActions"

export function provideMessageActions(actions) {
  provide(MESSAGE_ACTIONS_KEY, actions)
}

export function useMessageActions() {
  return inject(MESSAGE_ACTIONS_KEY, null)
}
