import { computed, ref } from "vue"

export const CHAT_RENDER_UNIT_BUDGET = 1000
export const CHAT_RENDER_MESSAGE_LIMIT = 200
export const CHAT_RENDER_MIN_MESSAGES = 2
// Automatic expansion (scroll-to-top / idle lookahead) grows the window
// in smaller steps than the explicit "show earlier" button so the mount
// cost never lands on the interaction path all at once.
export const CHAT_RENDER_EXPAND_UNIT_BUDGET = 500
export const CHAT_RENDER_EXPAND_MESSAGE_LIMIT = 100

function directChildCount(items) {
  if (!Array.isArray(items)) return 0
  return items.reduce((total, item) => {
    const children = Array.isArray(item?.children) ? item.children.length : 0
    const resultParts = Array.isArray(item?.resultParts) ? item.resultParts.length : 0
    return total + children + resultParts
  }, 0)
}

export function messageRenderUnits(message) {
  const parts = Array.isArray(message?.parts) ? message.parts : []
  if (message?.role === "assistant" && parts.length) {
    return 1 + parts.length + directChildCount(parts)
  }

  const contentParts = Array.isArray(message?.contentParts) ? message.contentParts : []
  const toolCalls = Array.isArray(message?.tool_calls) ? message.tool_calls : []
  return 1 + contentParts.length + toolCalls.length + directChildCount(toolCalls)
}

export function findRenderWindowStart(
  messages,
  end = messages.length,
  { unitBudget = CHAT_RENDER_UNIT_BUDGET, messageLimit = CHAT_RENDER_MESSAGE_LIMIT } = {},
) {
  const boundedEnd = Math.max(0, Math.min(end, messages.length))
  let start = boundedEnd
  let units = 0
  let count = 0

  while (start > 0 && count < messageLimit) {
    const nextUnits = messageRenderUnits(messages[start - 1])
    if (count >= CHAT_RENDER_MIN_MESSAGES && units + nextUnits > unitBudget) break
    start -= 1
    count += 1
    units += nextUnits
  }

  return start
}

export function useChatRenderWindow(messages, getScopeKey) {
  const activeAnchorId = ref(null)
  const windowStarts = new Map()
  const tailWindowStart = computed(() => findRenderWindowStart(messages.value))

  function leaveHistory(key = getScopeKey()) {
    activeAnchorId.value = null
    if (key) windowStarts.delete(key)
  }

  const windowStart = computed(() => {
    if (!activeAnchorId.value) return tailWindowStart.value
    const index = messages.value.findIndex((message) => message.id === activeAnchorId.value)
    if (index < 0) {
      leaveHistory()
      return tailWindowStart.value
    }
    return index
  })
  const windowMessages = computed(() => messages.value.slice(windowStart.value))
  const isHistoryMode = computed(() => activeAnchorId.value != null)

  function enterHistoryAt(index) {
    const messageId = messages.value[index]?.id
    if (!messageId) {
      leaveHistory()
      return
    }
    activeAnchorId.value = messageId
    const key = getScopeKey()
    if (key) windowStarts.set(key, messageId)
  }

  function expandHistory(step = {}) {
    enterHistoryAt(findRenderWindowStart(messages.value, windowStart.value, step))
  }

  function restoreHistory(key = getScopeKey()) {
    const messageId = windowStarts.get(key)
    if (!messageId) {
      activeAnchorId.value = null
      return false
    }
    if (!messages.value.some((message) => message.id === messageId)) {
      windowStarts.delete(key)
      activeAnchorId.value = null
      return false
    }
    activeAnchorId.value = messageId
    return true
  }

  return {
    enterHistoryAt,
    expandHistory,
    isHistoryMode,
    leaveHistory,
    restoreHistory,
    windowMessages,
    windowStart,
  }
}
