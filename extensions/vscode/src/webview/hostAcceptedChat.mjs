import { createQueuedActions } from './queuedActions.mjs'

function parseFrame(frame) {
  if (typeof frame !== 'string') return null
  try {
    return JSON.parse(frame)
  } catch {
    return null
  }
}

function removeEvent(chat, tab, eventId) {
  for (const bucket of [chat.messagesByTab?.[tab], chat.queuedMessagesByTab?.[tab]]) {
    if (!Array.isArray(bucket)) continue
    const index = bucket.findIndex((message) => message?.eventId === eventId)
    if (index >= 0) bucket.splice(index, 1)
  }
}

function snapshotReply(prompt) {
  return prompt
    ? Object.fromEntries(
        ['replied', 'superseded', 'timedOut', 'repliedActionId', 'repliedValues']
          .filter((key) => key in prompt)
          .map((key) => [key, prompt[key]]),
      )
    : null
}

function restoreReply(prompt, snapshot, actionId) {
  if (!prompt || prompt.repliedActionId !== actionId) return
  Object.assign(prompt, snapshot)
  for (const key of ['replied', 'superseded', 'timedOut', 'repliedActionId', 'repliedValues']) {
    if (!(key in snapshot)) delete prompt[key]
  }
}

async function settleSend(sent) {
  const [value, confirmation] = await Promise.allSettled([Promise.resolve(sent.value), sent.confirmation || Promise.resolve()])
  if (sent.error) throw sent.error
  if (value.status === 'rejected') throw value.reason
  if (confirmation.status === 'rejected') throw confirmation.reason
  return value.value
}

export function createHostAcceptedChat({ BridgeWebSocket, chat }) {
  const queued = createQueuedActions({ chat, BridgeWebSocket })
  const pendingInputs = new Map()
  const pendingReplies = new Map()

  const observe = (message, socket = chat._ws) => {
    if (socket !== chat._ws) return
    queued.observe(message, socket)
    const source = message?.source
    if (['user_input', 'input_queued'].includes(message?.type)) {
      const pending = pendingInputs.get(message.event_id)
      if (pending && pending.tab === source) pending.inputAccepted = true
    }
    if (message?.type === 'processing_start') {
      for (const pending of pendingInputs.values()) {
        if (pending.tab === source) pending.processingObserved = true
      }
    }
    if (message?.type === 'ui_reply_ack') {
      const pending = pendingReplies.get(`${source}:${message.event_id}`)
      if (pending) pending.backendSettled = true
    }
  }

  const send = async (content) => {
    const tab = chat.activeTab
    const previousProcessing = chat.processingByTab?.[tab]
    const previousMessages = chat.messagesByTab?.[tab]
    const previousQueue = chat.queuedMessagesByTab?.[tab]
    const before = [previousMessages, previousQueue].filter(Array.isArray).map((bucket) => ({ bucket, length: bucket.length }))
    let sent
    try {
      sent = BridgeWebSocket.captureSend(() => chat.send(content))
    } catch (error) {
      sent = { error }
    }
    if (!sent.frame) {
      const after = [chat.messagesByTab?.[tab], chat.queuedMessagesByTab?.[tab]].filter(Array.isArray)
      const added = after.flatMap((bucket) =>
        bucket
          .slice(before.find((entry) => entry.bucket === bucket)?.length || 0)
          .filter((item) => item.role === 'user')
          .map((item) => ({ bucket, item })),
      )
      try {
        return await settleSend(sent)
      } catch (error) {
        for (const { bucket, item } of added) {
          if (bucket !== chat.messagesByTab?.[tab] && bucket !== chat.queuedMessagesByTab?.[tab]) continue
          const index = bucket.indexOf(item)
          if (index >= 0) bucket.splice(index, 1)
        }
        throw error
      }
    }

    const frame = parseFrame(sent.frame)
    const eventId = frame?.type === 'input' ? frame.event_id : null
    const pending = { tab, inputAccepted: false, processingObserved: false }
    if (eventId) pendingInputs.set(eventId, pending)
    try {
      const result = await settleSend(sent)
      queued.trackInput(
        tab,
        chat.queuedMessagesByTab?.[tab]?.find((message) => message.eventId === eventId),
      )
      return result
    } catch (cause) {
      if (!pending.inputAccepted && eventId) removeEvent(chat, tab, eventId)
      const sameBinding = chat.messagesByTab?.[tab] === previousMessages && chat.queuedMessagesByTab?.[tab] === previousQueue
      if (!pending.processingObserved && sameBinding && chat.processingByTab?.[tab] === true) {
        if (previousProcessing === undefined) delete chat.processingByTab[tab]
        else chat.processingByTab[tab] = previousProcessing
      }
      throw cause
    } finally {
      if (eventId) pendingInputs.delete(eventId)
    }
  }

  const submitUIReply = async (tab, eventId, actionId, values) => {
    const key = `${tab}:${eventId}`
    if (pendingReplies.has(key)) throw Error('UI reply is already pending Host acceptance')
    const prompt = (chat.messagesByTab?.[tab] || []).find((message) => message?.role === 'ui_event' && message.eventId === eventId)
    const snapshot = snapshotReply(prompt)
    const pending = { backendSettled: false }
    pendingReplies.set(key, pending)
    try {
      const sent = BridgeWebSocket.captureSend(() => chat.submitUIReply(tab, eventId, actionId, values), {
        requireConfirmation: true,
      })
      return await settleSend(sent)
    } catch (cause) {
      if (!pending.backendSettled) restoreReply(prompt, snapshot, actionId)
      throw cause
    } finally {
      pendingReplies.delete(key)
    }
  }

  return { observe, send, submitUIReply, queued }
}

export function createObservedWebSocket(BridgeWebSocket, observe) {
  return class ObservedWebSocket extends BridgeWebSocket {
    _messageHandler = null

    set onmessage(handler) {
      this._messageHandler = handler
    }

    get onmessage() {
      return (event) => {
        try {
          observe(JSON.parse(event.data), this)
        } catch {}
        this._messageHandler?.(event)
      }
    }
  }
}
