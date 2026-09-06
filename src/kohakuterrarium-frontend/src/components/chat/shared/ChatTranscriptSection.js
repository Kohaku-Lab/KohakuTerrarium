import { cloneVNode, defineComponent, h } from "vue"

import "./chat-transcript-section.css"

const objectKeys = new WeakMap()
let nextObjectKey = 0

function objectKey(message) {
  if (!objectKeys.has(message)) objectKeys.set(message, nextObjectKey++)
  return objectKeys.get(message)
}

function explicitKey(message) {
  const explicit = message?.id ?? message?.eventId ?? message?.clientId
  return explicit == null ? null : `message:id:${typeof explicit}:${String(explicit)}`
}

function messageKey(
  message,
  absoluteIndex,
  previousKeys,
  reservedKeys,
  duplicateExplicitKeys,
  usedKeys,
) {
  const isObject = message && typeof message === "object"
  const previous = isObject ? previousKeys.get(message) : null
  if (previous && !usedKeys.has(previous)) return previous

  const explicit = explicitKey(message)
  if (
    explicit &&
    !duplicateExplicitKeys.has(explicit) &&
    !reservedKeys.has(explicit) &&
    !usedKeys.has(explicit)
  )
    return explicit
  if (explicit && isObject) return `${explicit}:object:${objectKey(message)}`
  if (isObject) return `message:object:${objectKey(message)}`
  return `message:primitive:${typeof message}:${String(message)}:index:${absoluteIndex}`
}

export default defineComponent({
  name: "ChatTranscriptSection",
  props: {
    messages: { type: Array, default: () => [] },
    messageOffset: { type: Number, default: 0 },
    totalCount: { type: Number, default: undefined },
    previousMessage: { type: Object, default: null },
    emptyTitle: { type: String, default: "" },
    emptySubtitle: { type: String, default: "" },
    processing: { type: Boolean, default: false },
    processingLabel: { type: String, default: "" },
    reconnecting: { type: Boolean, default: false },
    reconnectLabel: { type: String, default: "" },
    partial: { type: Boolean, default: false },
    partialLabel: { type: String, default: "" },
    hasNewer: { type: Boolean, default: false },
    newerLabel: { type: String, default: "" },
    resetRequired: { type: Boolean, default: false },
    resetLabel: { type: String, default: "" },
    historyBlocked: { type: Boolean, default: false },
    historyBlockedLabel: { type: String, default: "" },
    canLoadEarlier: { type: Boolean, default: false },
    earlierCount: { type: Number, default: 0 },
    earlierLabel: { type: String, default: "" },
    renderMessage: { type: Function, required: true },
  },
  emits: [
    "load-earlier",
    "reload",
    "scroll",
    "wheel",
    "keydown",
    "touchstart",
    "touchmove",
    "viewport-ready",
    "reply",
  ],
  setup(props, { emit }) {
    let viewport = null
    let previousKeys = new WeakMap()
    const setViewport = (element) => {
      if (!element || element === viewport) return
      viewport = element
      emit("viewport-ready", element)
    }
    const reply =
      (message) =>
      (actionId, values = {}) =>
        emit("reply", { message, actionId, values })

    return () => {
      const totalCount = props.totalCount ?? props.messageOffset + props.messages.length
      const content = []
      if (props.partial) {
        content.push(
          h(
            "p",
            { key: "sentinel:partial", class: "kt-transcript-partial", "data-history-partial": "" },
            props.partialLabel,
          ),
        )
      }
      if (props.hasNewer) {
        content.push(
          h(
            "button",
            {
              key: "sentinel:newer",
              type: "button",
              class: "kt-transcript-newer self-center",
              "data-history-newer": "",
              onClick: () => emit("reload"),
            },
            props.newerLabel,
          ),
        )
      }
      if (props.resetRequired) {
        content.push(
          h(
            "button",
            {
              key: "sentinel:reset",
              type: "button",
              class: "kt-transcript-reset self-center",
              "data-history-reset": "",
              onClick: () => emit("reload"),
            },
            props.resetLabel,
          ),
        )
      }
      const isEffectivelyEmpty =
        props.messages.length === 0 && totalCount === 0 && props.earlierCount === 0
      if (isEffectivelyEmpty) {
        content.push(
          h("div", { class: "kt-transcript-empty" }, [
            h("div", { class: "kt-transcript-empty__icon", "aria-hidden": "true" }, "◇"),
            h("p", { class: "kt-transcript-empty__title" }, props.emptyTitle),
            h("p", { class: "kt-transcript-empty__subtitle" }, props.emptySubtitle),
          ]),
        )
      }
      const showEarlier = props.canLoadEarlier || props.earlierCount > 0
      if (showEarlier) {
        content.push(
          h(
            "button",
            {
              key: "sentinel:earlier",
              type: "button",
              class: "kt-transcript-earlier self-center",
              onClick: () => emit("load-earlier"),
            },
            props.earlierLabel,
          ),
        )
      } else if (props.historyBlocked) {
        content.push(
          h(
            "p",
            {
              key: "sentinel:generating",
              class: "kt-transcript-generating",
              "data-history-generating": "",
            },
            props.historyBlockedLabel,
          ),
        )
      }
      const explicitCounts = new Map()
      for (const message of props.messages) {
        const explicit = explicitKey(message)
        if (explicit) explicitCounts.set(explicit, (explicitCounts.get(explicit) || 0) + 1)
      }
      const duplicateExplicitKeys = new Set(
        [...explicitCounts].filter(([, count]) => count > 1).map(([key]) => key),
      )
      const reservedKeys = new Set()
      for (const message of props.messages) {
        if (message && typeof message === "object") {
          const previous = previousKeys.get(message)
          if (previous) reservedKeys.add(previous)
        }
      }
      const usedKeys = new Set()
      const nextKeys = new WeakMap()
      props.messages.forEach((message, index) => {
        const absoluteIndex = props.messageOffset + index
        const previousMessage = index > 0 ? props.messages[index - 1] : props.previousMessage
        const context = {
          index,
          absoluteIndex,
          previousMessage,
          isFirst: absoluteIndex === 0,
          isLastAssistant: message?.role === "assistant" && absoluteIndex === totalCount - 1,
          reply: reply(message),
        }
        const explicit = message?.id ?? message?.eventId ?? message?.clientId
        const key = messageKey(
          message,
          absoluteIndex,
          previousKeys,
          reservedKeys,
          duplicateExplicitKeys,
          usedKeys,
        )
        usedKeys.add(key)
        if (message && typeof message === "object") nextKeys.set(message, key)
        const dataMessageKey = explicit == null ? key : String(explicit)
        if (explicit != null) {
          content.push(
            h("span", {
              key: `${key}:anchor`,
              class: "kt-transcript-message-anchor",
              "data-message-id": String(explicit),
              "aria-hidden": "true",
            }),
          )
        }
        const rendered = props.renderMessage(message, context)
        content.push(cloneVNode(rendered, { key, "data-message-key": dataMessageKey }))
      })
      previousKeys = nextKeys
      if (props.processing) {
        content.push(
          h(
            "div",
            {
              key: "sentinel:processing",
              class: "kt-transcript-processing",
              role: "status",
              "aria-live": "polite",
            },
            [
              h("span", { class: "kt-transcript-processing__dot", "aria-hidden": "true" }),
              h("span", props.processingLabel),
            ],
          ),
        )
      }

      return h("section", { class: "kt-transcript-section" }, [
        props.reconnecting
          ? h("div", { class: "kt-transcript-reconnect", role: "status" }, [
              h("span", { class: "kt-transcript-reconnect__icon", "aria-hidden": "true" }, "↻"),
              h("span", props.reconnectLabel),
            ])
          : null,
        h(
          "div",
          {
            ref: setViewport,
            class: "kt-transcript-viewport",
            onScroll: (event) => emit("scroll", event),
            onWheel: (event) => emit("wheel", event),
            onKeydown: (event) => emit("keydown", event),
            onTouchstart: (event) => emit("touchstart", event),
            onTouchmove: (event) => emit("touchmove", event),
          },
          [h("div", { class: "kt-conversation-list" }, content)],
        ),
      ])
    }
  },
})
