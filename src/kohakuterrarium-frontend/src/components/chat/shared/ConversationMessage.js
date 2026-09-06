import { computed, defineComponent, h, reactive, ref } from "vue"

import "./conversation-message.css"

import { computeRenderGroups } from "../../../public/chat/chatToolGrouping.js"

function plainText(content) {
  return h("div", { class: "kt-conversation-text" }, content || "")
}

function renderedText(renderer, content, breaks = false) {
  return renderer ? renderer(content || "", breaks) : plainText(content)
}

function safeExternalUrl(value) {
  if (typeof value !== "string") return ""
  try {
    const url = new URL(value)
    return url.protocol === "http:" || url.protocol === "https:" ? url.href : ""
  } catch {
    return ""
  }
}

function safeImageUrl(value) {
  if (typeof value !== "string") return ""
  if (value.startsWith("data:image/") || value.startsWith("blob:")) return value
  if (value.startsWith("/") && !value.startsWith("//")) return value
  if (!/^[a-z][a-z\d+.-]*:/i.test(value) && !value.startsWith("//")) return value
  return safeExternalUrl(value)
}

function toolResult(tool) {
  if (tool.result == null) return ""
  return typeof tool.result === "string" ? tool.result : JSON.stringify(tool.result, null, 2)
}

function compactLabel(message) {
  if (message.status === "running") return "Compacting context..."
  if (message.status === "skipped") {
    return `Compaction skipped${message.reason ? ` (${message.reason})` : ""}`
  }
  return `Context Compacted (round ${message.round || "?"})`
}

let compactSummaryId = 0

const NativeTool = defineComponent({
  name: "NativeConversationTool",
  props: { tool: { type: Object, required: true } },
  setup(props) {
    const expanded = ref(false)
    return () =>
      h("section", { class: "kt-conversation-tool" }, [
        h(
          "button",
          {
            class: "kt-conversation-tool__header",
            type: "button",
            "aria-expanded": expanded.value,
            onClick: () => (expanded.value = !expanded.value),
          },
          [
            h("span", { class: `kt-conversation-tool__status is-${props.tool.status || "idle"}` }),
            h("strong", props.tool.name || "tool"),
            h("span", { class: "kt-conversation-tool__summary" }, props.tool.status || ""),
            h("span", { class: "kt-conversation-tool__chevron" }, expanded.value ? "−" : "+"),
          ],
        ),
        expanded.value
          ? h(
              "pre",
              { class: "kt-conversation-tool__result" },
              toolResult(props.tool) || "(no output)",
            )
          : null,
      ])
  },
})

const NativeToolBatch = defineComponent({
  name: "NativeConversationToolBatch",
  props: { tools: { type: Array, required: true } },
  setup(props) {
    const expanded = ref(false)
    return () =>
      h("section", { class: "kt-conversation-tool-batch" }, [
        h(
          "button",
          {
            type: "button",
            class: "kt-conversation-tool__header",
            "aria-expanded": expanded.value,
            onClick: () => (expanded.value = !expanded.value),
          },
          [
            h("strong", `${props.tools.length} tool calls`),
            h(
              "span",
              { class: "kt-conversation-tool__summary" },
              props.tools.map((tool) => tool.name || "tool").join(", "),
            ),
            h("span", { class: "kt-conversation-tool__chevron" }, expanded.value ? "−" : "+"),
          ],
        ),
        expanded.value
          ? h(
              "div",
              { class: "kt-conversation-tool-batch__items" },
              props.tools.map((tool) => h(NativeTool, { key: tool.id || tool.name, tool })),
            )
          : null,
      ])
  },
})

const NativeUIEvent = defineComponent({
  name: "NativeConversationUIEvent",
  props: { message: { type: Object, required: true } },
  emits: ["reply"],
  setup(props, { emit }) {
    const text = ref(props.message.payload?.default || "")
    const selected = ref(props.message.payload?.default || "")
    const multiSelected = ref(
      Array.isArray(props.message.payload?.default) ? [...props.message.payload.default] : [],
    )
    const payload = () => props.message.payload || {}
    const resolved = () =>
      props.message.replied || props.message.superseded || props.message.timedOut
    const reply = (actionId, values = {}) => emit("reply", { actionId, values })

    return () => {
      const type = props.message.uiEventType
      const body = []
      if (payload().prompt)
        body.push(h("p", { class: "kt-conversation-event__prompt" }, payload().prompt))
      if (payload().detail)
        body.push(h("pre", { class: "kt-conversation-event__detail" }, payload().detail))
      if (type === "notification" && payload().text)
        body.push(h("p", { class: "kt-conversation-event__prompt" }, payload().text))
      if (type === "card") {
        if (payload().body)
          body.push(h("div", { class: "kt-conversation-event__body" }, payload().body))
        if (payload().fields?.length)
          body.push(
            h(
              "dl",
              { class: "kt-conversation-event__fields" },
              payload().fields.flatMap((field) => [
                h("dt", field.label || ""),
                h("dd", field.value || ""),
              ]),
            ),
          )
        if (payload().footer)
          body.push(h("footer", { class: "kt-conversation-event__footer" }, payload().footer))
      }
      if (type === "ask_text" && !resolved()) {
        body.push(
          h(
            "form",
            {
              class: "kt-conversation-event__form",
              onSubmit: (event) => {
                event.preventDefault()
                if (text.value.trim()) reply("submit", { text: text.value.trim() })
              },
            },
            [
              payload().multiline
                ? h("textarea", {
                    value: text.value,
                    "aria-label": payload().prompt || "Reply",
                    placeholder: payload().placeholder || "Type your reply…",
                    onInput: (event) => (text.value = event.target.value),
                  })
                : h("input", {
                    value: text.value,
                    "aria-label": payload().prompt || "Reply",
                    placeholder: payload().placeholder || "Type your reply…",
                    onInput: (event) => (text.value = event.target.value),
                  }),
              h("button", { type: "submit", disabled: !text.value.trim() }, "Send"),
              h("button", { type: "button", onClick: () => reply("cancel") }, "Cancel"),
            ],
          ),
        )
      } else if (
        (type === "confirm" || type === "card" || type === "notification") &&
        !resolved()
      ) {
        body.push(
          h(
            "div",
            { class: "kt-conversation-event__actions" },
            (payload().options || payload().actions || (payload().action ? [payload().action] : []))
              .map((option) => {
                if (option.style !== "link") {
                  return h(
                    "button",
                    { type: "button", onClick: () => reply(option.id, {}) },
                    option.label || option.id,
                  )
                }
                const href = safeExternalUrl(option.url)
                return href
                  ? h(
                      "a",
                      { href, target: "_blank", rel: "noopener noreferrer" },
                      option.label || option.id,
                    )
                  : null
              })
              .filter(Boolean),
          ),
        )
      } else if (type === "selection" && !resolved()) {
        const options = payload().options || []
        const controls = payload().multi
          ? h(
              "div",
              { class: "kt-conversation-event__choices" },
              options.map((option) => {
                const value = option.id ?? option.value
                return h("label", { class: "kt-conversation-event__choice" }, [
                  h("input", {
                    type: "checkbox",
                    value,
                    checked: multiSelected.value.includes(value),
                    onChange: (event) => {
                      multiSelected.value = event.target.checked
                        ? [...multiSelected.value, value]
                        : multiSelected.value.filter((item) => item !== value)
                    },
                  }),
                  h("span", option.label ?? option.value),
                ])
              }),
            )
          : h(
              "select",
              {
                "aria-label": payload().prompt || "Select an option",
                value: selected.value,
                onChange: (event) => (selected.value = event.target.value),
              },
              [
                h("option", { value: "" }, "Choose…"),
                ...options.map((option) =>
                  h("option", { value: option.id ?? option.value }, option.label ?? option.value),
                ),
              ],
            )
        const chosen = () => (payload().multi ? multiSelected.value : selected.value)
        body.push(
          h("div", { class: "kt-conversation-event__form" }, [
            controls,
            h(
              "button",
              {
                type: "button",
                disabled: payload().multi ? multiSelected.value.length === 0 : !selected.value,
                onClick: () => reply("submit", { selected: chosen() }),
              },
              "Submit",
            ),
            h("button", { type: "button", onClick: () => reply("cancel", {}) }, "Cancel"),
          ]),
        )
      } else if (type === "progress") {
        const max = Number(payload().max || 0)
        const value = Number(payload().value || 0)
        body.push(
          payload().indeterminate
            ? h("span", { class: "kt-conversation-event__status" }, "working…")
            : h("progress", { max: max || 100, value }),
        )
      }
      return h(
        "section",
        { class: `kt-conversation-event is-${type || "info"} ${resolved() ? "is-resolved" : ""}` },
        [
          h(
            "header",
            { class: "kt-conversation-event__header" },
            payload().title || payload().label || type?.replaceAll("_", " ") || "Event",
          ),
          ...body,
        ],
      )
    }
  },
})

export default defineComponent({
  name: "ConversationMessage",
  props: {
    message: { type: Object, required: true },
    renderText: { type: Function, default: null },
    renderTool: { type: Function, default: null },
    renderUiEvent: { type: Function, default: null },
    renderContentPart: { type: Function, default: null },
    bare: { type: Boolean, default: false },
  },
  emits: ["reply"],
  setup(props, { emit }) {
    const compactExpanded = ref(false)
    const expandedReasoning = reactive(new Set())
    const compactContentId = `kt-compact-summary-${++compactSummaryId}`
    const assistantParts = computed(() => {
      const message = props.message
      const rawParts = message.parts?.length
        ? message.parts
        : [
            ...(message.content ? [{ type: "text", content: message.content }] : []),
            ...(message.tool_calls || []).map((tool) => ({ ...tool, type: "tool" })),
          ]
      return computeRenderGroups(rawParts).map((group) =>
        group.type === "tool-batch"
          ? { type: "tool-batch", id: group.id, tools: group.tools }
          : group.part,
      )
    })

    function renderTool(tool) {
      return props.renderTool ? props.renderTool(tool) : h(NativeTool, { tool })
    }

    function renderPart(part, index, textBreaks = false) {
      let content = props.renderContentPart ? props.renderContentPart(part) : null
      if (!content && part.type === "text")
        content = renderedText(props.renderText, part.content || part.text, textBreaks)
      else if (!content && part.type === "reasoning") {
        const key = part.id ?? `reasoning_${index}`
        const text = part.text || ""
        const previewSlice = text.slice(0, 240)
        const lastCodeUnit = previewSlice.charCodeAt(previewSlice.length - 1)
        const safeSlice =
          lastCodeUnit >= 0xd800 && lastCodeUnit <= 0xdbff
            ? previewSlice.slice(0, -1)
            : previewSlice
        const preview = text.length > 240 ? `${safeSlice}…` : text
        const fullText = part.signature
          ? text
            ? `${text}\n[signature: ${part.signature}]`
            : `[signature: ${part.signature}]`
          : text
        content = h(
          "details",
          {
            class: "kt-conversation-reasoning reasoning-details",
            open: expandedReasoning.has(key),
            onToggle: (event) => {
              if (event.currentTarget.open) expandedReasoning.add(key)
              else expandedReasoning.delete(key)
            },
          },
          [
            h("summary", [
              h(
                "span",
                {
                  class:
                    "reasoning-summary-row inline-flex items-center gap-2 min-w-0 align-middle",
                },
                [
                  h("span", [
                    "Thinking",
                    part.source
                      ? h("span", { class: "reasoning-source" }, ` · ${part.source}`)
                      : null,
                  ]),
                  h(
                    "span",
                    {
                      class:
                        "reasoning-preview truncate flex-1 min-w-0 text-warm-600 dark:text-warm-400 font-mono",
                    },
                    preview,
                  ),
                ],
              ),
            ]),
            expandedReasoning.has(key) ? h("pre", { class: "reasoning-full" }, fullText) : null,
          ],
        )
      } else if (!content && part.type === "tool") content = renderTool(part)
      else if (!content && part.type === "tool-batch") {
        content = h(NativeToolBatch, { tools: part.tools || [] })
      } else if (!content && part.type === "image_url" && part.image_url?.url) {
        const src = safeImageUrl(part.image_url.url)
        if (src) {
          content = h("img", {
            class: "kt-conversation-image",
            src,
            alt: part.meta?.source_name || "generated image",
          })
        }
      } else if (!content && part.type === "file") {
        content = h(
          "div",
          { class: "kt-conversation-file" },
          part.file?.name || part.file?.path || "file",
        )
      }
      return content
        ? h("div", { class: `kt-conversation-part is-${part.type}`, key: part.id || index }, [
            content,
          ])
        : null
    }

    return () => {
      const message = props.message
      const role = message.role || "unknown"
      const rootClass = ["kt-conversation-message", `kt-conversation-message--${role}`]
      let content

      if (role === "ui_event") {
        content = props.renderUiEvent
          ? props.renderUiEvent(message, (reply) => emit("reply", reply))
          : h(NativeUIEvent, {
              message,
              onReply: (reply) => emit("reply", reply),
            })
      } else if (role === "assistant") {
        const parts = assistantParts.value
        content = parts.length
          ? h(
              "div",
              { class: "kt-conversation-parts" },
              parts.map((part, index) => renderPart(part, index)),
            )
          : renderedText(props.renderText, message.content)
      } else if (role === "user") {
        const userContent = message.contentParts?.length
          ? h(
              "div",
              { class: "kt-conversation-parts" },
              message.contentParts.map((part, index) => renderPart(part, index, true)),
            )
          : renderedText(props.renderText, message.content, true)
        content = props.bare
          ? userContent
          : h("div", { class: "kt-conversation-user-bubble" }, [
              h("div", { class: "kt-conversation-author" }, "You"),
              userContent,
            ])
      } else if (role === "clear") {
        content = h("div", { class: "kt-conversation-divider" }, [
          h(
            "span",
            `Context Cleared${message.messagesCleared ? ` — ${message.messagesCleared} messages` : ""}`,
          ),
        ])
      } else if (role === "compact") {
        const hasSummary = Boolean(message.summary)
        content = h("section", { class: "kt-conversation-banner is-compact" }, [
          h(
            "button",
            {
              type: "button",
              class: "kt-conversation-compact__header",
              "aria-expanded": hasSummary ? compactExpanded.value : false,
              "aria-controls": hasSummary ? compactContentId : undefined,
              disabled: !hasSummary,
              onClick: () => {
                if (hasSummary) compactExpanded.value = !compactExpanded.value
              },
            },
            [
              message.status === "running"
                ? h("span", { class: "kt-conversation-compact__status", "aria-hidden": "true" })
                : null,
              h("strong", { class: "kt-conversation-compact__label" }, compactLabel(message)),
              message.messagesCompacted
                ? h(
                    "span",
                    { class: "kt-conversation-compact__metadata" },
                    `${message.messagesCompacted} messages summarized`,
                  )
                : null,
              h("span", { class: "kt-conversation-compact__spacer" }),
              hasSummary
                ? h("span", {
                    class: [
                      "kt-conversation-compact__chevron",
                      compactExpanded.value ? "is-expanded" : "",
                    ],
                    "aria-hidden": "true",
                  })
                : null,
            ],
          ),
          hasSummary && compactExpanded.value
            ? h(
                "div",
                { id: compactContentId, class: "kt-conversation-compact__summary" },
                renderedText(props.renderText, message.summary),
              )
            : null,
        ])
      } else if (role === "error") {
        content = h("section", { class: "kt-conversation-banner is-error" }, [
          h("strong", message.errorType || "Processing Error"),
          h("pre", message.content || ""),
        ])
      } else if (role === "channel") {
        const channelContent = message.contentParts?.length
          ? h(
              "div",
              { class: "kt-conversation-parts" },
              message.contentParts.map((part, index) => renderPart(part, index, true)),
            )
          : renderedText(props.renderText, message.content, true)
        content = h("section", { class: "kt-conversation-channel" }, [
          h("header", { class: "kt-conversation-author" }, message.sender || "channel"),
          channelContent,
        ])
      } else if (role === "wire_inbound") {
        content = h("section", { class: "kt-conversation-banner is-compact" }, [
          h("strong", `Inbound from ${message.from || "another Creature"}`),
          message.preview ? renderedText(props.renderText, message.preview) : null,
        ])
      } else if (role === "trigger") {
        content = h("section", { class: "kt-conversation-banner is-compact" }, [
          h("strong", `Triggered by ${message.content || "event"}`),
          message.triggerContent ? renderedText(props.renderText, message.triggerContent) : null,
        ])
      } else if (role === "bg_result") {
        content = h("div", { class: "kt-conversation-divider" }, [
          h(
            "span",
            `${message.kind === "subagent" ? "Sub-agent" : "Tool"} result · ${message.label || "background"}`,
          ),
        ])
      } else {
        content = h("div", { class: "kt-conversation-system" }, message.content || "")
      }

      return props.bare ? content : h("article", { class: rootClass }, [content])
    }
  },
})
