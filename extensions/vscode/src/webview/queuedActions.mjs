import { showNotification } from './notifications.mjs'

export function createQueuedActions({ chat, BridgeWebSocket, timeoutMs = 25_000, setTimer = setTimeout, clearTimer = clearTimeout }) {
  const pending = new Map()
  const initial = new Map()
  const uncertain = new WeakMap()
  let disposed = false
  const owns = (operation) =>
    !disposed &&
    chat._ws === operation.socket &&
    chat.queuedMessagesByTab[operation.tab] === operation.queue &&
    operation.queue.includes(operation.item)
  const capture = (tab, item, kind) => ({ tab, item, kind, queue: chat.queuedMessagesByTab[tab], socket: chat._ws })
  const forgetInitial = (item) => {
    if (initial.has(item)) clearTimer(initial.get(item).timer)
    initial.delete(item)
  }
  function markUnknown(operation) {
    if (!owns(operation)) return
    operation.item.queueActionUncertain = true
    uncertain.set(operation.item, operation)
  }

  async function run(kind, tab, item, content) {
    if (disposed) throw Error('Queue actions disposed')
    if (chat.wsStatus !== 'open' || chat._ws?.readyState !== BridgeWebSocket.OPEN)
      throw Error('Chat is disconnected; queued input may still execute on the server')
    const operation = capture(tab, item, kind)
    if (tab !== chat.activeTab || !operation.queue?.includes(item)) throw Error('Queued message ownership changed')
    if (!item.backendQueued) throw Error('Wait for the initial queue acknowledgement before changing input')
    if (pending.has(item) || item.cancelling) throw Error('Queued message action is pending')
    if (item.queueActionUncertain) throw Error('Queue outcome is unknown; check server state before retrying')
    const snapshot = { content: item.content, contentParts: item.contentParts, cancelling: item.cancelling }
    const ack = new Promise((resolve, reject) => {
      operation.resolve = resolve
      operation.reject = reject
    })
    pending.set(item, operation)
    const timer = setTimer(() => operation.reject(Error('Queue action timed out; outcome unknown')), timeoutMs)
    let sent
    try {
      sent = BridgeWebSocket.captureSend(
        () => {
          if (kind === 'edit') chat.editQueuedMessage(tab, item.eventId, content)
          else chat.cancelQueuedMessage(tab, item.eventId)
        },
        { requireConfirmation: true },
      )
      const transport = Promise.all([Promise.resolve(sent.value), sent.confirmation]).then(() => {
        if (sent.error) throw sent.error
        return ack
      })
      return await Promise.race([ack, transport])
    } catch (error) {
      if (operation.confirmed) return operation.status
      if (sent?.frame) {
        markUnknown({ ...operation, resolve: null, reject: null })
        if (owns(operation)) delete item.cancelling
        throw Error(`${error.message || error}. The action may have executed; check server state before retrying.`)
      }
      if (owns(operation)) {
        item.content = snapshot.content
        item.contentParts = snapshot.contentParts
        if (snapshot.cancelling === undefined) delete item.cancelling
        else item.cancelling = snapshot.cancelling
      }
      throw error
    } finally {
      clearTimer(timer)
      pending.delete(item)
    }
  }

  return {
    edit: (tab, item, content) => run('edit', tab, item, content),
    cancel: (tab, item) => run('cancel', tab, item),
    trackInput(tab, item) {
      if (disposed || !item || item.backendQueued || initial.has(item)) return
      const operation = capture(tab, item, 'input')
      const timer = setTimer(() => {
        forgetInitial(item)
        markUnknown(operation)
      }, timeoutMs)
      initial.set(item, { timer, operation })
    },
    observe(message, socket = chat._ws) {
      if (disposed || socket !== chat._ws || !['input_queued', 'input_edit_ack', 'input_cancel_ack'].includes(message?.type)) return
      const tab = message.source
      let item = chat.queuedMessagesByTab?.[tab]?.find((entry) => entry.eventId === message.event_id)
      if (!item && message.type === 'input_queued') {
        const messages = chat.messagesByTab?.[tab]
        const index = messages?.findIndex((entry) => entry.role === 'user' && entry.eventId === message.event_id) ?? -1
        if (index >= 0) {
          item = messages.splice(index, 1)[0]
          item.queued = true
          item.queuedTab = tab
          if (!chat.queuedMessagesByTab[tab]) chat.queuedMessagesByTab[tab] = []
          chat.queuedMessagesByTab[tab].push(item)
        }
      }
      if (!item) return
      if (message.type === 'input_queued') {
        const tracked = initial.get(item)?.operation || uncertain.get(item)
        if (tracked && !owns(tracked)) return
        item.backendQueued = true
        forgetInitial(item)
        if (uncertain.get(item)?.kind === 'input') {
          uncertain.delete(item)
          delete item.queueActionUncertain
        }
        return
      }
      const operation = pending.get(item) || uncertain.get(item)
      if (!operation || !owns(operation)) return
      const ackType = operation.kind === 'edit' ? 'input_edit_ack' : 'input_cancel_ack'
      if (
        message.type !== ackType ||
        (message.status !== 'already_sent' && message.status !== (operation.kind === 'edit' ? 'edited' : 'cancelled'))
      )
        return
      operation.confirmed = true
      operation.status = message.status
      uncertain.delete(item)
      delete item.queueActionUncertain
      operation.resolve?.(message.status)
      if (message.status === 'already_sent' && chat.activeTab === tab)
        showNotification({ type: 'warning', message: 'The message already entered processing; the requested change was not applied.' })
    },
    dispose() {
      disposed = true
      for (const operation of pending.values()) operation.reject(Error('Queue actions disposed'))
      for (const item of initial.keys()) forgetInitial(item)
    },
  }
}
