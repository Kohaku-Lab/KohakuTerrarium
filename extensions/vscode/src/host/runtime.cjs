const { normalizeSession } = require('./client.cjs')
const { executeGoal } = require('./goalCommand.cjs')
const { beginReady, reconcileReady } = require('./readyRuntime.cjs')
const { allowedMessage } = require('./protocol.cjs')
const { MEDIA_TYPES, dispatchMedia } = require('./mediaHost.cjs')
const { MODEL_TYPES, dispatchModel } = require('./modelHost.cjs')
const { BRANCH_TYPES, dispatchBranch } = require('./branchHost.cjs')
const { openPlatformLink } = require('./openLink.cjs')

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
    mediaHost = null,
    backendBase = null,
    openExternal = null,
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
    this.readyControllers = new Set()
    // Long branch POSTs own an abortable wait so dispose/ready can release it
    // without sending a backend interrupt (cancel wait != cancel turn).
    this.branchControllers = new Set()
    this.goalTimeoutMs = 25_000
    // The extension injects a per-view media coordinator; the runtime owns its fence.
    this.mediaHost = mediaHost
    // The platform link opener resolves a user-clicked reference against the live
    // backend base (never the token) and calls the injected host ``openExternal``.
    this.backendBase = backendBase
    this.openExternal = openExternal
    this.post = post
    this.generation = this.sockets.begin()
  }

  beginReady(readyId) {
    return beginReady(this, readyId)
  }

  reconcileReady(readyId) {
    return reconcileReady(this, readyId)
  }

  rotateGeneration() {
    this.generation = this.sockets.begin()
  }

  requireSelection(message) {
    const selection = this.state.selection
    if (!selection || selection.session !== message.session || selection.creature !== message.creature) {
      throw Error('Selected Creature ownership changed')
    }
    return selection
  }

  // Saved sub-agent routes address a session, not a creature. The stable target
  // identity is the selected session; the exact selected object still fences the
  // read so an explicit reselect or a ready reset invalidates it.
  requireSelectedSession(message) {
    const selection = this.state.selection
    if (!selection || selection.session !== message.session) throw Error('Selected Creature ownership changed')
    return selection
  }

  ownsHistoryRead(selected, readyId, intent) {
    return !this.disposed && readyId === this.runtimeEpoch && this.state.selection === selected && intent === this.selectionIntentVersion
  }

  // The sub-agent read/mutation fence. Admission is the selected object identity,
  // the ready epoch and the explicit-intent version; a notification-ordering
  // ``selectionVersion`` advance from an unchanged-target topology refresh must
  // not reject a read captured before the refresh, while a real reselect/switch
  // (new selection object + intent bump) or a ready reset still invalidates it.
  ownsSelectedRead(selected, readyId, intent) {
    return this.ownsHistoryRead(selected, readyId, intent)
  }

  enqueueSelectionOperation(operation) {
    const readyId = this.runtimeEpoch
    const result = this.selectionOperationTail.then(() => {
      if (this.disposed || readyId !== this.runtimeEpoch) throw Error('Runtime ownership changed')
      return operation()
    })
    this.selectionOperationTail = result.catch(() => {})
    return result
  }

  enqueueSelectionMutation(operation) {
    // Explicit intent supersedes in-flight media reads.
    this.selectionIntentVersion++
    this.pendingSelectionMutations++
    this.mediaHost?.abortAll()
    return this.enqueueSelectionOperation(operation).finally(() => this.pendingSelectionMutations--)
  }

  clearSelection() {
    return this.enqueueSelectionMutation(() => this.clearSelectionOwned())
  }

  async clearSelectionOwned() {
    if (!this.state.selection) {
      return { selection: null, changed: false, selectionVersion: this.selectionVersion }
    }
    const readyId = this.runtimeEpoch
    await this.state.updateSelection(null)
    if (readyId !== this.runtimeEpoch || this.disposed) throw Error('Runtime ownership changed')
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
    const readyId = this.runtimeEpoch
    const sessions = await this.client.listOpen()
    if (readyId !== this.runtimeEpoch || this.disposed) throw Error('Runtime ownership changed')
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
    const readyId = this.runtimeEpoch
    await this.state.updateSelection(result.selection)
    if (readyId !== this.runtimeEpoch || this.disposed) throw Error('Runtime ownership changed')
    this.rotateGeneration()
    this.selectionVersion++
    return { ...result, selectionVersion: this.selectionVersion }
  }

  async selectOwned(message) {
    const readyId = this.runtimeEpoch
    const active = await this.client.active(message.session)
    if (readyId !== this.runtimeEpoch || this.disposed) throw Error('Runtime ownership changed')
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
      if (readyId !== this.runtimeEpoch || this.disposed) throw Error('Runtime ownership changed')
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
      selectionIntentVersion: this.selectionIntentVersion,
    })
    return capability
  }

  ownsContextCommand(capability) {
    // Admission is the stable target identity + ready epoch + explicit-intent fence, not
    // the notification-ordering selectionVersion: an unchanged-target topology refresh
    // advances selectionVersion yet must not reject a command captured before the clear
    // confirmation or while compacting. Explicit reselect/switch and ready resets still
    // bump the intent/epoch and are rejected.
    const owned = contextCapabilities.get(capability)
    return (
      !this.disposed &&
      owned?.runtime === this &&
      owned.runtimeEpoch === this.runtimeEpoch &&
      owned.selected === this.state.selection &&
      owned.selectionIntentVersion === this.selectionIntentVersion &&
      this.pendingSelectionMutations === 0
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
    const readyId = this.runtimeEpoch
    await this.client.stop(selected.session)
    if (readyId !== this.runtimeEpoch || this.disposed) throw Error('Runtime ownership changed; stop outcome may be unknown')
    return this.clearSelectionOwned()
  }

  async handle(message) {
    const readyId = this.runtimeEpoch
    if (this.disposed) throw Error('Runtime disposed')
    if (message.type.startsWith('ws.') && this.runtimeEpoch != null && message.readyId !== this.runtimeEpoch)
      throw Error('Socket ready ownership changed')
    // Every media request is a single fixed dispatch (see mediaHost.dispatchMedia);
    // it is handled before the lifecycle switch so this class stays host-shaped.
    if (MEDIA_TYPES.has(message.type)) return dispatchMedia(this, message)
    // Model/slash + instance-metadata ops are a single fixed dispatch too (see
    // modelHost.dispatchModel), keeping this switch about session lifecycle only.
    if (MODEL_TYPES.has(message.type)) return dispatchModel(this, message)
    // Branch mutations (regenerate/editMessage) are their own fixed dispatch (see
    // branchHost.dispatchBranch): the long POST is admitted with selection/ready
    // but never holds the selection queue for the whole turn.
    if (BRANCH_TYPES.has(message.type)) return dispatchBranch(this, message)
    switch (message.type) {
      case 'session.clearSelection': {
        const result = await this.clearSelection()
        this.post({
          type: 'session.clearSelection.result',
          requestId: message.requestId,
          data: { ok: true, selectionVersion: result.selectionVersion, readyId },
        })
        return
      }
      case 'session.reconcile': {
        const data = await this.reconcileSelection()
        this.post({ type: 'session.reconcile.result', requestId: message.requestId, data: { ...data, readyId } })
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
        if (readyId !== this.runtimeEpoch || this.disposed) throw Error('Runtime ownership changed')
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
          data: { ...result.selection, selectionVersion: result.selectionVersion, readyId },
        })
        return
      }
      case 'session.stop': {
        const result = await this.enqueueSelectionMutation(() => this.stopOwned(message))
        this.post({
          type: 'session.stop.result',
          requestId: message.requestId,
          data: { ok: true, selectionVersion: result.selectionVersion, readyId },
        })
        return
      }
      case 'http.history': {
        const selected = this.requireSelection(message)
        const data = await this.client.history(selected.session, selected.creature)
        this.post({
          type: 'http.history.result',
          requestId: message.requestId,
          data,
        })
        return
      }
      case 'http.historyPage':
      case 'http.historyDetail': {
        if (!allowedMessage(message))
          throw Error(message.type === 'http.historyPage' ? 'Invalid history page request' : 'Invalid history detail request')
        const selected = this.requireSelection(message)
        const intent = this.selectionIntentVersion
        const data =
          message.type === 'http.historyPage'
            ? await this.client.historyPage(selected.session, selected.creature, message.options || {})
            : await this.client.historyDetail(selected.session, selected.creature, message.params || {})
        if (!this.ownsHistoryRead(selected, readyId, intent)) throw Error('Selected Creature ownership changed')
        this.post({ type: `${message.type}.result`, requestId: message.requestId, data })
        return
      }
      case 'http.subagentConversation': {
        if (!allowedMessage(message)) throw Error('Invalid subagent conversation request')
        const selected = this.requireSelection(message)
        const intent = this.selectionIntentVersion
        const data = await this.client.subagentConversation(selected.session, selected.creature, message.options || {})
        if (!this.ownsSelectedRead(selected, readyId, intent)) throw Error('Selected Creature ownership changed')
        this.post({ type: 'http.subagentConversation.result', requestId: message.requestId, data })
        return
      }
      case 'http.subagentList':
      case 'http.subagentSavedConversation': {
        if (!allowedMessage(message))
          throw Error(
            message.type === 'http.subagentList' ? 'Invalid subagent runs request' : 'Invalid saved subagent conversation request',
          )
        const selected = this.requireSelectedSession(message)
        const intent = this.selectionIntentVersion
        const data =
          message.type === 'http.subagentList'
            ? await this.client.listSubagents(selected.session, message.options || {})
            : await this.client.savedSubagentConversation(selected.session, message.options || {})
        if (!this.ownsSelectedRead(selected, readyId, intent)) throw Error('Selected Creature ownership changed')
        this.post({ type: `${message.type}.result`, requestId: message.requestId, data })
        return
      }
      case 'http.subagentSend': {
        if (!allowedMessage(message)) throw Error('Invalid subagent send request')
        const selected = this.requireSelection(message)
        const intent = this.selectionIntentVersion
        // A send is a mutation: serialized with selection changes, admitted before
        // and after the await, and never wrapped in a retry/timeout that would
        // disguise whether the backend actually received it.
        const data = await this.enqueueSelectionOperation(async () => {
          if (this.disposed || this.state.selection !== selected || intent !== this.selectionIntentVersion)
            throw Error('Selected Creature ownership changed')
          return this.client.sendSubagentMessage(selected.session, selected.creature, message.name, {
            content: message.content,
            jobId: message.jobId,
          })
        })
        if (!this.ownsSelectedRead(selected, readyId, intent)) throw Error('Selected Creature ownership changed')
        this.post({ type: 'http.subagentSend.result', requestId: message.requestId, data })
        return
      }
      case 'http.promote': {
        if (!allowedMessage(message)) throw Error('Invalid promote request')
        const selected = this.requireSelection(message)
        const intent = this.selectionIntentVersion
        const data = await this.enqueueSelectionOperation(async () => {
          if (this.disposed || this.state.selection !== selected || intent !== this.selectionIntentVersion)
            throw Error('Selected Creature ownership changed')
          return this.client.promote(selected.session, selected.creature, message.jobId)
        })
        if (!this.ownsSelectedRead(selected, readyId, intent)) throw Error('Selected Creature ownership changed')
        this.post({ type: 'http.promote.result', requestId: message.requestId, data })
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
          readyId,
        })
        return
      case 'ws.close':
        this.sockets.closeSocket(this.generation, message.socketId, { postMessage: this.post })
        return
      case 'platform.openLink': {
        const data = await openPlatformLink(this, message)
        // A result suppressed after the await (stale ready/selection) is
        // intentionally not posted; the open, if it happened, cannot be undone.
        if (data.suppressed) return
        this.post({ type: 'platform.openLink.result', requestId: message.requestId, data })
        return
      }
      default:
        throw Error(`Unsupported message: ${message.type}`)
    }
  }

  ownsArtifactRead(selected, message) {
    // Ownership is the stable target identity and ready epoch; selectionVersion is
    // ordering-only for the Webview, so an unchanged-target topology refresh must not
    // reject a media read captured under the previous ordering.
    return !this.disposed && !!selected && selected === this.state.selection && message.readyId === this.runtimeEpoch
  }

  dispose() {
    this.disposed = true
    this.selectionIntentVersion++
    this.topologyReconcileVersion++
    for (const controller of this.topologyControllers) controller.abort()
    this.topologyControllers.clear()
    for (const controller of this.readyControllers) controller.abort()
    this.readyControllers.clear()
    // Release the local branch waits; never send a backend interrupt from dispose.
    for (const controller of this.branchControllers) controller.abort()
    this.branchControllers.clear()
    for (const cancel of this.pendingGoals) cancel(Error('Goal runtime disposed; execution outcome may be unknown'))
    this.pendingGoals.clear()
    this.mediaHost?.abortAll()
    this.sockets.closeGeneration(this.generation)
  }
}

module.exports = { RuntimeHost, normalizeActive }
