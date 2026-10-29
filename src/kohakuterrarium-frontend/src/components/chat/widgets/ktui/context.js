/** Injection key for the kt.ui actions (``setVar``, ``setVars``, ``send``, ``disabled``). */

import { inject } from "vue"

export const KTUI_KEY = Symbol("kt-ui")

export function useKtUi() {
  const ctx = inject(KTUI_KEY, null)
  if (!ctx) throw new Error("kt.ui node rendered outside UiTree")
  return ctx
}
