function ownershipKey(ownership) {
  if (ownership?.readyId == null || ownership?.runtimeId == null || ownership?.creatureId == null) return ''
  return JSON.stringify([ownership.readyId, ownership.runtimeId, ownership.creatureId])
}

export function isComposerSubmitDisabled(submitBusy, processing) {
  return submitBusy && !processing
}

export function createSubmitGate() {
  const active = new Map()
  let sequence = 0

  return {
    acquire(ownership) {
      const key = ownershipKey(ownership)
      if (!key || active.has(key)) return null
      const token = { key, sequence: ++sequence }
      active.set(key, token)
      return token
    },
    release(token) {
      if (active.get(token?.key) === token) active.delete(token.key)
    },
    busy(ownership) {
      return active.has(ownershipKey(ownership))
    },
  }
}
