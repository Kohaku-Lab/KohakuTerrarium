function validMessage(message) {
  return message && typeof message === 'object' && !Array.isArray(message)
}

export function settleRequestMessage(pending, message) {
  if (!validMessage(message) || !Number.isSafeInteger(message.requestId) || message.requestId < 1) return false
  if (Object.hasOwn(message, 'socketId') || Object.hasOwn(message, 'sendId') || Object.hasOwn(message, 'id')) return false
  const request = pending.get(message.requestId)
  if (!request) return false
  const expectedType = `${request.type}.result`
  if (message.type !== expectedType && message.type !== 'error') return false
  if (message.type === 'error' && typeof message.error !== 'string') return false
  if (message.type === expectedType && !Object.hasOwn(message, 'data')) return false
  pending.delete(message.requestId)
  clearTimeout(request.timer)
  if (message.type === 'error') request.reject(Error(message.error))
  else request.resolve(message.data)
  return true
}
