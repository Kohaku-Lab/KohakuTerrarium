const SUPERSEDED = Symbol('conversation-superseded')

export function isConversationSuperseded(error) {
  return error?.code === SUPERSEDED
}

function supersededError() {
  const error = new Error('Conversation operation was superseded')
  error.code = SUPERSEDED
  error.silent = true
  return error
}

function sameOwnership(left, right) {
  return left?.readyId === right?.readyId && left?.runtimeId === right?.runtimeId && left?.creatureId === right?.creatureId
}

function ownershipKey(owned) {
  if (owned?.runtimeId == null || owned?.creatureId == null) return ''
  return JSON.stringify([owned.runtimeId, owned.creatureId])
}

function createConversationBuckets(current, empty, { maxBuckets = 32 } = {}) {
  if (!Number.isSafeInteger(maxBuckets) || maxBuckets < 1) throw Error('Invalid conversation bucket limit')
  const buckets = new Map()
  let revision = 0
  let disposed = false
  const keyFor = (owned = current()) => ownershipKey(owned)

  return {
    capture() {
      const owned = { ...current() }
      return { ...owned, bucketRevision: buckets.get(keyFor(owned))?.revision }
    },
    get(owned) {
      const key = keyFor(owned)
      const entry = buckets.get(key)
      if (!entry) return empty
      buckets.delete(key)
      buckets.set(key, entry)
      return entry.value
    },
    set(value, owned) {
      const key = keyFor(owned)
      if (!key || disposed) return
      buckets.delete(key)
      if (!value.length) return
      buckets.set(key, { value, revision: ++revision })
      while (buckets.size > maxBuckets) buckets.delete(buckets.keys().next().value)
    },
    filter(predicate, owned) {
      const key = keyFor(owned)
      const entry = buckets.get(key)
      if (!entry) return
      const value = entry.value.filter(predicate)
      if (!value.length) buckets.delete(key)
      else buckets.set(key, { value, revision: ++revision })
    },
    clear(owned) {
      buckets.delete(keyFor(owned))
    },
    clearSubmitted(value, owned) {
      const key = keyFor(owned)
      const entry = buckets.get(key)
      if (!entry || entry.value !== value || entry.revision !== owned?.bucketRevision) return false
      buckets.delete(key)
      return true
    },
    clearAll() {
      buckets.clear()
    },
    dispose() {
      disposed = true
      buckets.clear()
    },
  }
}

export function createConversationAttachments(current, options) {
  const buckets = createConversationBuckets(current, [], options)
  return {
    ...buckets,
    removeSubmitted(submitted, owned) {
      const remaining = new Map()
      for (const attachment of submitted) remaining.set(attachment, (remaining.get(attachment) || 0) + 1)
      buckets.filter((attachment) => {
        const count = remaining.get(attachment) || 0
        if (!count) return true
        if (count === 1) remaining.delete(attachment)
        else remaining.set(attachment, count - 1)
        return false
      }, owned)
    },
  }
}

export function createConversationDrafts(current, options) {
  return createConversationBuckets(current, '', options)
}

export function createConversationOwnership(current) {
  let disposed = false
  const capture = () => ({ ...current() })
  const assertion = (owned) => () => {
    if (disposed || !sameOwnership(owned, current())) throw supersededError()
  }

  return {
    isCurrent(owned) {
      return !disposed && sameOwnership(owned, current())
    },
    dispose() {
      disposed = true
    },
    transform(transformer) {
      return async (...args) => {
        const owned = capture()
        const assertCurrent = assertion(owned)
        assertCurrent()
        const result = await transformer(...args)
        assertCurrent()
        return result
      }
    },
    async run(operation) {
      const owned = capture()
      const assertCurrent = assertion(owned)
      assertCurrent()
      const result = await operation(assertCurrent)
      assertCurrent()
      return result
    },
    async dispatch(operation) {
      const owned = capture()
      const assertCurrent = assertion(owned)
      assertCurrent()
      return operation(assertCurrent)
    },
  }
}
