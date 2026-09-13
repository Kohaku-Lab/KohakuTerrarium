class ConnectionStateWriter {
  constructor(workspaceState, key) {
    this.workspaceState = workspaceState
    this.key = key
    this.queue = Promise.resolve()
  }

  read() {
    return this.workspaceState.get(this.key) || {}
  }

  update(mutator) {
    return this.enqueue(async () => {
      const current = this.read()
      const next = typeof mutator === 'function' ? mutator(current) : mutator
      if (next === undefined) return { applied: false, value: current }
      await this.workspaceState.update(this.key, next)
      return { applied: true, value: next }
    })
  }

  updateIf(predicate, mutator) {
    return this.enqueue(async () => {
      const current = this.read()
      if (!predicate(current)) return { applied: false, value: current }
      const next = typeof mutator === 'function' ? mutator(current) : mutator
      if (next === undefined) return { applied: false, value: current }
      await this.workspaceState.update(this.key, next)
      return { applied: true, value: next }
    })
  }

  enqueue(operation) {
    const result = this.queue.then(operation)
    this.queue = result.catch(() => {})
    return result
  }
}

module.exports = { ConnectionStateWriter }
