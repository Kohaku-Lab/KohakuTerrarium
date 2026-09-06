const { normalizeSession } = require('./client.cjs')
const { executeGoal } = require('./goalCommand.cjs')
const { allowedMessage, validateEndpoint } = require('./protocol.cjs')
const { ArtifactRegistry, artifactNamespaceOf, canonicalArtifactPath } = require('./artifactRegistry.cjs')
const { ArtifactReader, httpBaseFromWebSocket } = require('./artifactRead.cjs')

const contextCapabilities = new WeakMap()

function encode(value) {
  return encodeURIComponent(value)
}

function normalizeActive(active) {
  return normalizeSession({
    conversation_id: active.conversation_id ?? null,
    runtime_id: active.session_id ?? active.runtime_id,
    display_name: active.display_name ?? active.config_name ?? active.name,
    is_live: true,
    type: active.type,
    creatures: active.creatures,
  })
}

class RuntimeHost {
  constructor({
    client,
    state,
    sockets,
    post,
    getDefaultCreature,
    getWorkspacePath,
    socketFactory,
    webSocketBase,
    token,
    runtimeEpoch = null,
    topologyTimeoutMs = 30_000,
    fetchImpl = null,
    artifactReader = null,
    artifactRegistry = null,
    artifactTimeoutMs = 10_000,
    artifactMaxBytes = 8 * 1024 * 1024,
    artifactMaxConcurrent = 4,
  }) {
    this.client = client
    this.state = state
    this.sockets = sockets
    this.post = post
    this.getDefaultCreature = getDefaultCreature
    this.getWorkspacePath = getWorkspacePath
    this.socketFactory = socketFactory
    this.webSocketBase = webSocketBase
    this.token = token
    this.runtimeEpoch = runtimeEpoch
    this.topologyTimeoutMs = topologyTimeoutMs
    this.selectionOperationTail = Promise.resolve()
    this.selectionVersion = 0
    this.selectionIntentVersion = 0
    this.pendingSelectionMutations = 0
    this.topologyReconcileVersion = 0
    this.disposed = false
    this.topologyControllers = new Set()
    this.pendingGoals = new Set()
    this.goalTimeoutMs = 25_000
    this.artifacts = artifactRegistry || new ArtifactRegistry()
    // Artifact fetches use the loopback HTTP form of the validated ws base.
    const artifactBase = validateEndpoint(httpBaseFromWebSocket(webSocketBase))
    this.artifactReader =
      artifactReader ||
      new ArtifactReader({
        base: artifactBase,
        token,
        ...(fetchImpl ? { fetchImpl } : {}),
        limits: { timeoutMs: artifactTimeoutMs, maxBytes: artifactMaxBytes, maxConcurrent: artifactMaxConcurrent },
      })
    this.artifactTimeoutMs = artifactTimeoutMs
    this.artifactMaxConcurrent = artifactMaxConcurrent
    this.artifactControllers = new Set()
    const postToView = post
    // Register artifact refs from ws frames before the frame reaches the webview.
    this.post = (message) => {
      if (message?.type === 'ws.frame') this.artifacts.observeFrameText(message.data)
      return postToView(message)
    }
    this.generation = this.sockets.begin()
  }

  rotateGeneration() {
    this.generation = this.sockets.begin()
    this.cancelArtifactReads()
    this.artifacts.invalidate()
  }

  cancelArtifactReads() {
    for (const controller of this.artifactControllers) controller.abort()
    this.artifactControllers.clear()
  }

  requireSelection(message) {
    const selection = this.state.selection
    if (!selection || selection.session !== message.session || selection.creature !== message.creature) {
      throw Error('Selected Creature ownership changed')
    }
    return selection
  }

  enqueueSelectionOperation(operation) {
    const result = this.selectionOperationTail.then(operation)
    this.selectionOperationTail = result.catch(() => {})
    return result
  }

  enqueueSelectionMutation(operation) {
    // Explicit intent supersedes in-flight artifact reads and previously admitted refs.
    this.selectionIntentVersion++
    this.pendingSelectionMutations++
    this.cancelArtifactReads()
    this.artifacts.invalidate()
    return this.enqueueSelectionOperation(operation).finally(() => this.pendingSelectionMutations--)
  }

  clearSelection() {
    return this.enqueueSelectionMutation(() => this.clearSelectionOwned())
  }

  async clearSelectionOwned() {
    if (!this.state.selection) {
      return { selection: null, changed: false, selectionVersion: this.selectionVersion }
    }
    await this.state.updateSelection(null)
    this.rotateGeneration()
    this.selectionVersion++
    return { selection: null, changed: true, selectionVersion: this.selectionVersion }
  }

  reconcileSelection() {
    return this.enqueueSelectionMutation(() => this.reconcileSelectionOwned())
  }

  async reconcileTopologySelection() {
    if (this.disposed) return this.supersededTopologySelection()
    const topologyVersion = ++this.topologyReconcileVersion
    const current = this.state.selection
    const selectionVersion = this.selectionVersion
    const selectionIntentVersion = this.selectionIntentVersion
    if (!current?.targetCreatureId) {
      return { selection: null, changed: false, selectionVersion }
    }
    let timeout
    const controller = new AbortController()
    this.topologyControllers.add(controller)
    const expired = new Promise((_, reject) => {
      timeout = setTimeout(() => {
        controller.abort()
        reject(Error('Topology reconciliation timed out'))
      }, this.topologyTimeoutMs)
    })
    let sessions
    try {
      sessions = await Promise.race([this.client.listOpen({ signal: controller.signal }), expired])
    } finally {
      clearTimeout(timeout)
      this.topologyControllers.delete(controller)
    }
    if (!this.ownsTopologySelection(topologyVersion, selectionIntentVersion, selectionVersion, current)) {
      return this.supersededTopologySelection()
    }
    return this.applyTopologySelection(topologyVersion, selectionIntentVersion, selectionVersion, current, sessions)
  }

  ownsTopologySelection(topologyVersion, selectionIntentVersion, selectionVersion, current) {
    return (
      topologyVersion === this.topologyReconcileVersion &&
      selectionIntentVersion === this.selectionIntentVersion &&
      selectionVersion === this.selectionVersion &&
      this.pendingSelectionMutations === 0 &&
      !this.disposed &&
      this.state.selection === current
    )
  }

  supersededTopologySelection() {
    return {
      selection: this.state.selection,
      changed: false,
      selectionVersion: this.selectionVersion,
      superseded: true,
    }
  }

  async applyTopologySelection(topologyVersion, selectionIntentVersion, selectionVersion, current, sessions) {
    const result = this.reconciledSelection(current, sessions)
    if (!result.changed) {
      if (!this.ownsTopologySelection(topologyVersion, selectionIntentVersion, selectionVersion, current)) {
        return this.supersededTopologySelection()
      }
      this.selectionVersion++
      return { ...result, selectionVersion: this.selectionVersion }
    }
    const applied = await this.state.updateSelectionIf(result.selection, () =>
      this.ownsTopologySelection(topologyVersion, selectionIntentVersion, selectionVersion, current),
    )
    if (!applied) return this.supersededTopologySelection()
    this.rotateGeneration()
    this.selectionVersion++
    return { ...result, selectionVersion: this.selectionVersion }
  }

  async reconcileSelectionOwned() {
    const current = this.state.selection
    if (!current?.targetCreatureId) {
      return { selection: null, changed: false, selectionVersion: this.selectionVersion }
    }
    const sessions = await this.client.listOpen()
    return this.applyReconciledSelection(current, sessions)
  }

  reconciledSelection(current, sessions) {
    const session = sessions.find(
      (candidate) => candidate.isLive && candidate.creatures.some((creature) => creature.id === current.targetCreatureId),
    )
    const creature = session?.creatures.find((candidate) => candidate.id === current.targetCreatureId)
    const selection =
      session && creature
        ? {
            session: session.runtimeId,
            graph: session.runtimeId,
            creature: creature.name,
            targetCreatureId: current.targetCreatureId,
          }
        : null
    const changed = !selection || selection.session !== current.session || selection.creature !== current.creature
    if (!changed) {
      return { selection: current, changed: false, selectionVersion: this.selectionVersion }
    }
    return { selection, changed: true, selectionVersion: this.selectionVersion }
  }

  async applyReconciledSelection(current, sessions) {
    const result = this.reconciledSelection(current, sessions)
    if (!result.changed) return result
    await this.state.updateSelection(result.selection)
    this.rotateGeneration()
    this.selectionVersion++
    return { ...result, selectionVersion: this.selectionVersion }
  }

  async selectOwned(message) {
    const active = await this.client.active(message.session)
    const selected = active.creatures?.find((creature) => String(creature.creature_id ?? creature.id) === message.creatureId)
    if (!selected?.name) throw Error('Selected Creature is not in the active Session')
    const selection = {
      session: active.session_id ?? message.session,
      graph: active.session_id ?? message.session,
      creature: selected.name,
      targetCreatureId: message.creatureId,
    }
    const changed =
      !this.state.selection ||
      this.state.selection.session !== selection.session ||
      this.state.selection.creature !== selection.creature ||
      this.state.selection.targetCreatureId !== selection.targetCreatureId
    if (changed) {
      await this.state.updateSelection(selection)
      this.rotateGeneration()
      this.selectionVersion++
    }
    return { selection, changed, selectionVersion: this.selectionVersion }
  }

  acquireContextCommand() {
    const selected = this.state.selection
    if (!selected) return null
    const capability = Object.freeze({})
    contextCapabilities.set(capability, {
      runtime: this,
      runtimeEpoch: this.runtimeEpoch,
      selected,
      selectionVersion: this.selectionVersion,
    })
    return capability
  }

  ownsContextCommand(capability) {
    const owned = contextCapabilities.get(capability)
    return (
      !this.disposed &&
      owned?.runtime === this &&
      owned.runtimeEpoch === this.runtimeEpoch &&
      owned.selected === this.state.selection &&
      owned.selectionVersion === this.selectionVersion
    )
  }

  async contextCommandOwned(message, capability) {
    if (!this.ownsContextCommand(capability)) throw Error('Selected Creature ownership changed')
    const { selected } = contextCapabilities.get(capability)
    const command = message.type === 'context.compact' ? 'compact' : 'clear'
    const args = command === 'clear' ? '--force' : ''
    const data = await this.client.creatureCommand(selected.session, selected.creature, command, args)
    if (!this.ownsContextCommand(capability)) throw Error('Selected Creature ownership changed')
    return data
  }

  async stopOwned(message) {
    const selected = this.state.selection
    if (!selected || selected.session !== message.session || selected.targetCreatureId !== message.creatureId) {
      throw Error('Session ownership changed')
    }
    await this.client.stop(selected.session)
    return this.clearSelectionOwned()
  }

  async handle(message) {
    switch (message.type) {
      case 'session.clearSelection': {
        const result = await this.clearSelection()
        this.post({
          type: 'session.clearSelection.result',
          requestId: message.requestId,
          data: { ok: true, selectionVersion: result.selectionVersion, readyId: this.runtimeEpoch },
        })
        return
      }
      case 'session.reconcile': {
        const data = await this.reconcileSelection()
        this.post({ type: 'session.reconcile.result', requestId: message.requestId, data: { ...data, readyId: this.runtimeEpoch } })
        return
      }
      case 'session.list': {
        this.post({ type: 'session.list.result', requestId: message.requestId, data: await this.client.listOpen() })
        return
      }
      case 'session.create': {
        const configPath = this.getDefaultCreature()
        const pwd = this.getWorkspacePath()
        if (!configPath) throw Error('Configure kohakuterrarium.defaultCreature first')
        if (!pwd) throw Error('Open a workspace folder before creating a Session')
        const created = await this.client.createCreature({
          configPath,
          pwd,
          name: 'VS Code Session',
        })
        const data = normalizeActive(created)
        this.post({ type: 'session.create.result', requestId: message.requestId, data })
        return
      }
      case 'session.resume': {
        const open = await this.client.listOpen()
        if (!open.some((session) => !session.isLive && session.savedName === message.savedName)) {
          throw Error('Saved session is not an open dormant Session')
        }
        const resumed = await this.client.resume(message.savedName)
        const data = normalizeActive({
          ...resumed.session,
          session_id: resumed.instance_id ?? resumed.session?.session_id,
          type: resumed.type,
          config_name: resumed.session_name,
        })
        data.savedName = resumed.session_name ?? message.savedName
        this.post({ type: 'session.resume.result', requestId: message.requestId, data })
        return
      }
      case 'session.select': {
        const result = await this.enqueueSelectionMutation(() => this.selectOwned(message))
        this.post({
          type: 'session.select.result',
          requestId: message.requestId,
          data: { ...result.selection, selectionVersion: result.selectionVersion, readyId: this.runtimeEpoch },
        })
        return
      }
      case 'session.stop': {
        const result = await this.enqueueSelectionMutation(() => this.stopOwned(message))
        this.post({
          type: 'session.stop.result',
          requestId: message.requestId,
          data: { ok: true, selectionVersion: result.selectionVersion, readyId: this.runtimeEpoch },
        })
        return
      }
      case 'http.history': {
        const selected = this.requireSelection(message)
        // Capture the intent version before the async read: an explicit selection intent
        // during the fetch supersedes admission even if the selection pointer is unchanged.
        const selectionIntentVersion = this.selectionIntentVersion
        const data = await this.client.history(selected.session, selected.creature)
        // Admit refs only while the fetching selection still owns the runtime.
        if (!this.disposed && this.state.selection === selected && selectionIntentVersion === this.selectionIntentVersion) {
          this.artifacts.observe(data)
        }
        this.post({
          type: 'http.history.result',
          requestId: message.requestId,
          data,
        })
        return
      }
      case 'http.interrupt': {
        const selected = this.requireSelection(message)
        this.post({
          type: 'http.interrupt.result',
          requestId: message.requestId,
          data: await this.client.interrupt(selected.session, selected.creature),
        })
        return
      }
      case 'goal.execute': {
        const data = await executeGoal(this, message)
        this.post({ type: 'goal.execute.result', requestId: message.requestId, data })
        return
      }
      case 'artifact.read': {
        const data = await this.readArtifactOwned(message)
        this.post({ type: 'artifact.read.result', requestId: message.requestId, data })
        return
      }
      case 'context.compact':
      case 'context.clear': {
        const capability = message.contextCapability || this.acquireContextCommand()
        if (!capability) throw Error('Select a Creature before managing context')
        const data = await this.enqueueSelectionOperation(() => this.contextCommandOwned(message, capability))
        this.post({ type: `${message.type}.result`, requestId: message.requestId, data })
        return
      }
      case 'ws.open': {
        const selected = this.state.selection
        if (!selected) throw Error('Select a Creature before opening chat')
        const route = `/ws/sessions/${encode(selected.session)}/creatures/${encode(selected.creature)}/chat`
        this.sockets.open(
          this.generation,
          message.socketId,
          () => this.socketFactory(`${this.webSocketBase}${route}`, this.token ? [`kt-token.${this.token}`] : []),
          { postMessage: this.post },
        )
        return
      }
      case 'ws.send':
        if (!(await this.sockets.send(this.generation, message.socketId, message.data))) {
          throw Error('Chat socket is not open')
        }
        this.post({
          type: 'ws.send.result',
          socketId: message.socketId,
          sendId: message.sendId,
          readyId: this.runtimeEpoch,
        })
        return
      case 'ws.close':
        this.sockets.closeSocket(this.generation, message.socketId, { postMessage: this.post })
        return
      default:
        throw Error(`Unsupported message: ${message.type}`)
    }
  }

  ownsArtifactRead(selected, message) {
    return (
      !this.disposed &&
      !!selected &&
      selected === this.state.selection &&
      message.readyId === this.runtimeEpoch &&
      message.selectionVersion === this.selectionVersion &&
      this.artifacts.allowed(message.path)
    )
  }

  async readArtifactOwned(message) {
    if (!allowedMessage(message)) throw Error('Invalid artifact request')
    const selected = this.state.selection
    if (!selected) throw Error('Select a Creature before reading artifacts')
    if (!this.ownsArtifactRead(selected, message)) throw Error('Selected Creature ownership changed')
    // A queued explicit selection intent supersedes new reads before any HTTP happens.
    if (this.pendingSelectionMutations > 0) throw Error('Selected Creature ownership changed')
    const canonical = canonicalArtifactPath(message.path)
    if (!canonical || !this.artifacts.allowsCanonical(canonical)) throw Error('Unknown artifact reference')
    // The cap covers the whole operation: namespace listing plus artifact read.
    if (this.artifactControllers.size >= this.artifactMaxConcurrent) throw Error('Artifact read limit reached')
    const controller = new AbortController()
    this.artifactControllers.add(controller)
    const selectionIntentVersion = this.selectionIntentVersion
    const owned = () => selectionIntentVersion === this.selectionIntentVersion && this.ownsArtifactRead(selected, message)
    let timedOut = false
    const timer = setTimeout(() => {
      timedOut = true
      controller.abort()
    }, this.artifactTimeoutMs)
    const aborted = new Promise((_, reject) => {
      controller.signal.addEventListener('abort', () => reject(Error(timedOut ? 'Artifact read timed out' : 'Artifact read aborted')), {
        once: true,
      })
    })
    aborted.catch(() => {})
    const guard = (promise) => Promise.race([promise, aborted])
    try {
      // The trusted namespace comes from a fresh listing per read; it is never cached.
      const sessions = await guard(this.client.listOpen({ signal: controller.signal }))
      if (!owned()) throw Error('Selected Creature ownership changed')
      const row = sessions.find((candidate) => candidate.isLive && candidate.runtimeId === selected.session)
      const savedName = row?.savedName
      if (typeof savedName !== 'string' || savedName.length === 0 || artifactNamespaceOf(canonical) !== savedName) {
        throw Error('Unknown artifact reference')
      }
      const data = await this.artifactReader.read(canonical, { signal: controller.signal })
      // Re-check ownership at delivery so a mid-read change never yields bytes.
      if (!owned()) throw Error('Selected Creature ownership changed')
      return { dataUrl: data }
    } catch (error) {
      if (timedOut) throw Error('Artifact read timed out')
      if (!owned()) throw Error('Selected Creature ownership changed')
      const messageText = error instanceof Error ? error.message : String(error)
      if (messageText === 'Unknown artifact reference' || messageText.startsWith('Artifact ')) throw error
      throw Error('Artifact request failed')
    } finally {
      clearTimeout(timer)
      // Abort the op controller on every exit: it disposes the listing request and response body.
      controller.abort()
      this.artifactControllers.delete(controller)
    }
  }

  dispose() {
    this.disposed = true
    this.selectionIntentVersion++
    this.topologyReconcileVersion++
    for (const controller of this.topologyControllers) controller.abort()
    this.topologyControllers.clear()
    for (const cancel of this.pendingGoals) cancel(Error('Goal runtime disposed; execution outcome may be unknown'))
    this.pendingGoals.clear()
    this.cancelArtifactReads()
    this.artifacts.invalidate()
    this.sockets.closeGeneration(this.generation)
  }
}

module.exports = { RuntimeHost, normalizeActive }
