function validMessage(message) {
  return message && typeof message === 'object' && !Array.isArray(message)
}

export function settleRequestMessage(pending, message, clearTimer = clearTimeout) {
  if (!validMessage(message) || !Number.isSafeInteger(message.requestId) || message.requestId < 1) return false
  if (Object.hasOwn(message, 'socketId') || Object.hasOwn(message, 'sendId') || Object.hasOwn(message, 'id')) return false
  const request = pending.get(message.requestId)
  if (!request) return false
  const expectedType = `${request.type}.result`
  if (message.type !== expectedType && message.type !== 'error') return false
  if (message.type === 'error' && typeof message.error !== 'string') return false
  if (message.type === expectedType && !Object.hasOwn(message, 'data')) return false
  pending.delete(message.requestId)
  clearTimer(request.timer)
  if (message.type === 'error') {
    const error = Error(message.error)
    if (Number.isSafeInteger(message.status)) error.status = message.status
    // The branch transport phase (a fixed boolean plus the supersession marker)
    // is the only extra field the shared store guard reads; nothing else crosses.
    if (typeof message.mayHaveRun === 'boolean') error.mayHaveRun = message.mayHaveRun
    if (message.superseded === true) error.superseded = true
    request.reject(error)
  } else request.resolve(message.data)
  return true
}
