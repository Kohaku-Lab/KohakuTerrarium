function messageText(value) {
  if (typeof value === 'string') return value
  if (value && typeof value.message === 'string') return value.message
  return ''
}

/** Interpret command HTTP payloads without depending on either host UI. */
export function parseContextCommandResult(response) {
  if (!response || response.cancelled) return { error: '', status: '' }

  const error = messageText(response.error)
  if (error) return { error, status: '' }

  const dataMessage = messageText(response.data)
  const notification = messageText(response.notify)
  const output = typeof response.output === 'string' ? response.output : ''
  return { error: '', status: dataMessage || notification || output }
}

/** Apply an outcome only while its captured conversation ownership is current. */
export function applyContextCommandOutcome(response, isCurrent, show) {
  if (!isCurrent) return false
  const outcome = parseContextCommandResult(response)
  if (outcome.error) show('error', outcome.error)
  else if (outcome.status) show('status', outcome.status)
  return true
}
