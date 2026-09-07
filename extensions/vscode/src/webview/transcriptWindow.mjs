export const TRANSCRIPT_WINDOW_SIZE = 400

export function createTranscriptWindow(pageSize = TRANSCRIPT_WINDOW_SIZE) {
  let identity = ''
  let visibleCount = pageSize
  let previousSequence = []

  function matches(left, right) {
    return left.id === right?.id && left.eventId === right?.eventId
  }

  function normalize(nextIdentity, sequence) {
    const same = sequence.length === previousSequence.length && sequence.every((item, index) => matches(item, previousSequence[index]))
    const appended = sequence.length >= previousSequence.length && previousSequence.every((item, index) => matches(item, sequence[index]))
    if (nextIdentity !== identity || (!same && !appended)) visibleCount = pageSize
    identity = nextIdentity
    previousSequence = sequence
  }

  return {
    view(items = [], nextIdentity = '', sequence = createMessageSequence(items)) {
      normalize(nextIdentity, sequence)
      const start = Math.max(0, items.length - visibleCount)
      return {
        messages: items.slice(start),
        messageOffset: start,
        earlierCount: start,
        totalCount: items.length,
        previousMessage: start > 0 ? items[start - 1] : null,
      }
    },

    expandEarlier(items = [], nextIdentity = '', sequence = createMessageSequence(items)) {
      normalize(nextIdentity, sequence)
      if (visibleCount >= items.length) return false
      visibleCount = Math.min(items.length, visibleCount + pageSize)
      return true
    },
  }
}

export function createMessageSequence(items) {
  return items.map((message) => ({ id: message.id, eventId: message.eventId }))
}

export function messageSequenceKey(sequence) {
  return sequence.map((message) => `${message.id ?? ''}\u0000${message.eventId ?? ''}`).join('\u0001')
}

export function createMessageTailSignature(items) {
  const last = items[items.length - 1]
  if (!last) return '0'
  const content = typeof last.content === 'string' ? last.content : JSON.stringify(last.content ?? null)
  const parts = Array.isArray(last.parts)
    ? last.parts
        .map((part) => {
          if (part.type === 'text') return `text:${part.content || ''}`
          if (part.type === 'reasoning') return `reasoning:${part.text || ''}:${part.signature || ''}:${part.source || ''}`
          const result = typeof part.result === 'string' ? part.result : JSON.stringify(part.result ?? null)
          const children = JSON.stringify(part.children ?? null)
          return `o:${part.type || ''}:${part.id || ''}:${part.status || ''}:${result}:${children}`
        })
        .join('|')
    : ''
  return `${items.length}:${last.id}:${last.role}:${content}:${parts}`
}

export function createTranscriptBindings({ onViewportReady, onScroll, onReply }) {
  let identity = null
  let current = null

  return {
    forIdentity(nextIdentity) {
      if (current && nextIdentity === identity) return current
      identity = nextIdentity
      const boundIdentity = nextIdentity
      current = {
        onViewportReady: (viewport) => onViewportReady(viewport, boundIdentity),
        onScroll: (event) => onScroll(event, boundIdentity),
        onReply,
      }
      return current
    },
  }
}
