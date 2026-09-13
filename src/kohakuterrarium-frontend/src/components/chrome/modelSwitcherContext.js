import { inject, provide } from "vue"

// Narrow host seam for the shared ModelSwitcher. Only the routing/instance-store
// concerns (which instance is selected, how a target/model is read, how a switch
// and a target selection are dispatched, the host-keyed inventory and the
// open/host-change subscriptions) cross this boundary; the template, search,
// provider grouping and variation logic stay in the one shared leaf both hosts
// render.
export const MODEL_SWITCHER_CONTEXT = Symbol("model-switcher-context")

export function provideModelSwitcherContext(context) {
  provide(MODEL_SWITCHER_CONTEXT, context)
}

export function useModelSwitcherContext() {
  const context = inject(MODEL_SWITCHER_CONTEXT, null)
  if (!context) throw Error("ModelSwitcher must be rendered inside a host context provider")
  return context
}
