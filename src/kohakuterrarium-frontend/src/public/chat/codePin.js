/**
 * "Open in canvas" for code blocks: a host that has a canvas provides a pin
 * handler; ``MarkdownRenderer`` then adds the button to every code block.
 * Hosts without a canvas provide nothing and get no button.
 */

import { inject, provide } from "vue"

const CODE_PIN_KEY = Symbol("kt-code-pin")

/** ``handler({content, lang})`` is called when the user pins a code block. */
export function provideCodePin(handler) {
  provide(CODE_PIN_KEY, typeof handler === "function" ? handler : null)
}

export function useCodePin() {
  return inject(CODE_PIN_KEY, null)
}
