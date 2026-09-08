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

export function isTailRenderBudgetFull(messages) {
  if (findRenderWindowStart(messages) > 0 || messages.length >= CHAT_RENDER_MESSAGE_LIMIT)
    return true
  return (
    messages.length >= CHAT_RENDER_MIN_MESSAGES &&
    messages.reduce((units, message) => units + messageRenderUnits(message), 0) >=
      CHAT_RENDER_UNIT_BUDGET
  )
}

// A projected row's stable semantic anchor. The window boundary is keyed
// by a physical record identity (``_historyKeys``), not the positional
// ``id``, so a row whose id changes when older text/tool events merge in
// still resolves to the same DOM position after a page materializes.
export function semanticKey(message) {
  if (!message) return null
  if (Array.isArray(message._historyKeys) && message._historyKeys.length) {
    return String(message._historyKeys[0])
  }
  if (Array.isArray(message._historyKey) && message._historyKey.length) {
    return String(message._historyKey[0])
  }
  return message.id != null ? String(message.id) : null
}

// Resolve a semantic key to the current array index. A row is found by
// exact ``id`` match OR by key containment (a merged row whose
// ``_historyKeys`` include the recorded boundary key).
export function indexOfSemanticKey(messages, key) {
  if (key == null) return -1
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const message = messages[i]
    if (message?.id === key) return i
    const keys = Array.isArray(message?._historyKeys)
      ? message._historyKeys
      : Array.isArray(message?._historyKey)
        ? message._historyKey
        : []
    if (keys.includes(key)) return i
  }
  return -1
}

export function useChatRenderWindow(messages, getScopeKey) {
  const activeAnchorId = ref(null)
  const windowStarts = new Map()
  const tailWindowStart = computed(() => findRenderWindowStart(messages.value))

  // Resolve an anchor to a row index. The anchor is a stable semantic key
  // (a physical ``_history_key``) so a row that survives page-boundary
  // merging or cross-page duplicate collapse is still found by key
  // containment, even when its generated ``id`` changed. Falls back to the
  // legacy stable ``id`` match for rows without a physical key.
  function findAnchorIndex(anchor) {
    const list = messages.value
    if (anchor == null) return -1
    const byId = list.findIndex((message) => message.id === anchor)
    if (byId >= 0) return byId
    return list.findIndex((message) => {
      const keys = Array.isArray(message?._historyKeys)
        ? message._historyKeys
        : Array.isArray(message?._historyKey)
          ? message._historyKey
          : []
      return keys.includes(anchor)
    })
  }

  function rowAnchorKey(message) {
    return semanticKey(message)
  }

  function leaveHistory(key = getScopeKey()) {
    activeAnchorId.value = null
    if (key) windowStarts.delete(key)
  }

  const windowStart = computed(() => {
    if (!activeAnchorId.value) return tailWindowStart.value
    const index = findAnchorIndex(activeAnchorId.value)
    if (index < 0) {
      leaveHistory()
      return tailWindowStart.value
    }
    return index
  })
  const windowMessages = computed(() => messages.value.slice(windowStart.value))
  const isHistoryMode = computed(() => activeAnchorId.value != null)

  function enterHistoryAt(index) {
    const message = messages.value[index]
    const anchor = rowAnchorKey(message)
    if (!anchor) {
      leaveHistory()
      return
    }
    activeAnchorId.value = anchor
    const key = getScopeKey()
    if (key) windowStarts.set(key, anchor)
  }

  function expandHistory(step = {}) {
    enterHistoryAt(findRenderWindowStart(messages.value, windowStart.value, step))
  }

  function restoreHistory(key = getScopeKey()) {
    const anchor = windowStarts.get(key)
    if (!anchor) {
      activeAnchorId.value = null
      return false
    }
    if (findAnchorIndex(anchor) < 0) {
      windowStarts.delete(key)
      activeAnchorId.value = null
      return false
    }
    activeAnchorId.value = anchor
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
