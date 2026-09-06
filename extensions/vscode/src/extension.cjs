const crypto = require('node:crypto')
const vscode = require('vscode')
const WebSocket = require('ws')

const { createClient, validateCapabilities } = require('./host/client.cjs')
const { resolveLocalConnection } = require('./host/connection.cjs')
const { createConnectionAttemptOwner } = require('./host/connectionAttempt.cjs')
const { discoverInstalledKt, probeCapabilities, verifyKtProbe } = require('./host/localDiscovery.cjs')
const { publicError } = require('./host/errors.cjs')
const { allowedMessage, validateEndpoint } = require('./host/protocol.cjs')
const { RuntimeHost } = require('./host/runtime.cjs')
const { SocketOwners } = require('./host/sockets.cjs')
const { ConnectionStateWriter } = require('./host/state.cjs')
const { TopologyWatcher } = require('./host/topology.cjs')
const { renderWebviewHtml } = require('./host/webview.cjs')

const CONFIG_KEY = 'kohakuterrarium.connection'
const TOKEN_KEY = 'kohakuterrarium.hostToken'

function tokenRequired(capabilities) {
  const policy = capabilities.auth?.host_token || {}
  return policy.enabled === true && policy.loopback_bypass !== true
}

async function resolveConnection(context, stored) {
  let discovery
  if (stored.manual === true && stored.endpoint) {
    const endpoint = validateEndpoint(stored.endpoint)
    const capabilities = validateCapabilities(await probeCapabilities(endpoint))
    discovery = {
      endpoint,
      capabilities,
      source: 'manual',
      requiresToken: tokenRequired(capabilities),
    }
  } else {
    try {
      discovery = await discoverInstalledKt({
        selectStrictCandidate: async (candidates) => {
          const selected = await vscode.window.showQuickPick(
            candidates.map((candidate) => ({ label: candidate.endpoint, candidate })),
            {
              placeHolder: 'Select a trusted local endpoint to send your host token; service identity is not yet verified',
              ignoreFocusOut: true,
            },
          )
          return selected?.candidate
        },
      })
    } catch (error) {
      if (!stored.endpoint || error?.code === 'KT_DISCOVERY_CANCELLED') throw error
      const endpoint = validateEndpoint(stored.endpoint)
      const capabilities = validateCapabilities(await probeCapabilities(endpoint))
      discovery = {
        endpoint,
        capabilities,
        source: 'legacy',
        requiresToken: tokenRequired(capabilities),
      }
    }
  }

  return resolveLocalConnection({
    discover: async () => discovery,
    getStoredToken: () => context.secrets.get(TOKEN_KEY),
    promptToken: () =>
      vscode.window.showInputBox({
        prompt: `The local KT service at ${discovery.endpoint} requires its host token`,
        password: true,
        ignoreFocusOut: true,
      }),
    storeToken: (token) => context.secrets.store(TOKEN_KEY, token),
    verify: async ({ endpoint, token, source, requiresToken }, { signal }) => {
      if (source === 'probe' && requiresToken && !(await verifyKtProbe(endpoint, 500, token))) {
        throw Error('KT identity verification failed')
      }
      await createClient({ endpoint, token }).listOpen({ signal })
    },
  })
}

async function configure(context, stateWriter, onConfigured = () => {}) {
  const previous = stateWriter.read()
  const rawEndpoint = await vscode.window.showInputBox({
    prompt: 'Advanced local KT endpoint override',
    value: previous.endpoint || 'http://127.0.0.1:8001',
    ignoreFocusOut: true,
  })
  if (!rawEndpoint) return
  const endpoint = validateEndpoint(rawEndpoint)
  const capabilities = validateCapabilities(await probeCapabilities(endpoint))
  let token = ''
  if (tokenRequired(capabilities)) {
    token =
      (await vscode.window.showInputBox({
        prompt: 'The overridden local KT service requires its host token',
        password: true,
        ignoreFocusOut: true,
      })) || ''
    if (!token) throw Error('Host token is required by the local service')
  }
  await createClient({ endpoint, token }).listOpen()
  if (token) await context.secrets.store(TOKEN_KEY, token)
  await stateWriter.update(() => ({
    endpoint,
    manual: true,
    source: 'manual',
    selection: previous.endpoint === endpoint ? previous.selection || null : null,
  }))
  await onConfigured()
  await vscode.window.showInformationMessage('KohakuTerrarium local override configured')
  await vscode.commands.executeCommand('kohakuterrarium.chat.focus')
}

function webSocketBase(endpoint) {
  return endpoint.replace(/^http:/, 'ws:')
}

async function confirmContextClear() {
  return (
    (await vscode.window.showWarningMessage(
      'Clear the active Creature context? Session history remains available.',
      { modal: true },
      'Clear Context',
    )) === 'Clear Context'
  )
}

async function dispatchContextCommand({ message, getRuntime, isCurrent, confirmClear, post }) {
  const ownedRuntime = await getRuntime()
  const contextCapability = ownedRuntime.acquireContextCommand()
  if (!contextCapability) throw Error('Select a Creature before managing context')
  if (message.type === 'context.clear' && !(await confirmClear())) {
    await post({ type: 'context.clear.result', requestId: message.requestId, data: { cancelled: true } })
    return
  }
  if (!isCurrent(ownedRuntime) || !ownedRuntime.ownsContextCommand(contextCapability)) {
    await post({
      type: `${message.type}.result`,
      requestId: message.requestId,
      data: { cancelled: true, superseded: true },
    })
    return
  }
  await ownedRuntime.handle({ ...message, contextCapability })
}

function activate(context) {
  const liveViews = new Set()
  const stateWriter = new ConnectionStateWriter(context.workspaceState, CONFIG_KEY)
  async function rediscoverViews() {
    for (const entry of liveViews) {
      entry.disposeRuntime()
      await entry.webview.postMessage({ type: 'configuration.changed' })
    }
  }

  context.subscriptions.push(
    vscode.commands.registerCommand('kohakuterrarium.configure', () =>
      configure(context, stateWriter, rediscoverViews).catch(() =>
        vscode.window.showErrorMessage('Could not configure local KohakuTerrarium'),
      ),
    ),
    vscode.commands.registerCommand('kohakuterrarium.useAutomaticDiscovery', async () => {
      await stateWriter.update((current) => ({
        manual: false,
        selection: current.selection || null,
      }))
      await rediscoverViews()
      await vscode.window.showInformationMessage('KohakuTerrarium will use automatic local discovery')
    }),
  )

  const provider = {
    resolveWebviewView(view) {
      const webview = view.webview
      webview.options = {
        enableScripts: true,
        localResourceRoots: [vscode.Uri.joinPath(context.extensionUri, 'dist'), vscode.Uri.joinPath(context.extensionUri, 'media')],
      }
      webview.html = renderWebviewHtml({
        cspSource: webview.cspSource,
        scriptUri: String(webview.asWebviewUri(vscode.Uri.joinPath(context.extensionUri, 'dist', 'webview.js'))),
        styleUri: String(webview.asWebviewUri(vscode.Uri.joinPath(context.extensionUri, 'dist', 'webview.css'))),
        brandUri: String(webview.asWebviewUri(vscode.Uri.joinPath(context.extensionUri, 'media', 'kohaku-icon.png'))),
        nonce: crypto.randomBytes(16).toString('base64'),
      })

      let runtime = null
      let runtimePromise = null
      let topology = null
      let activeConnection = null
      let composerConnection = { endpoint: null, id: null }
      let epoch = 0
      const connectionAttempts = createConnectionAttemptOwner()
      const entry = {
        webview,
        disposeRuntime() {
          connectionAttempts.invalidate()
          epoch++
          topology?.close()
          topology = null
          runtime?.dispose()
          runtime = null
          runtimePromise = null
          activeConnection = null
        },
      }
      liveViews.add(entry)

      const sendError = (message, error) => {
        console.error(`KohakuTerrarium ${message.type} failed`, error)
        const safe = publicError(message.type)
        const webSocketType = message.type === 'ws.send' ? 'ws.send.error' : message.type.startsWith('ws.') ? 'ws.error' : 'error'
        return webview.postMessage({
          type: webSocketType,
          ...(message.type.startsWith('ws.') ? { socketId: message.socketId } : { requestId: message.requestId }),
          ...(message.type === 'ws.send' ? { sendId: message.sendId } : {}),
          error: safe.message,
          code: safe.code,
        })
      }

      async function buildRuntime(readyId) {
        const runtimeEpoch = epoch
        const initial = stateWriter.read()
        const connection = await resolveConnection(context, initial)
        if (runtimeEpoch !== epoch) throw Error('Runtime ownership changed')
        const stored = await stateWriter.update((current) => {
          if (runtimeEpoch !== epoch) return undefined
          return {
            endpoint: connection.endpoint,
            manual: current.manual === true,
            source: connection.source,
            selection: current.selection || null,
          }
        })
        if (runtimeEpoch !== epoch || !stored.applied) throw Error('Runtime ownership changed')
        activeConnection = connection
        if (composerConnection.endpoint !== connection.endpoint) {
          composerConnection = { endpoint: connection.endpoint, id: crypto.randomUUID() }
        }
        const state = {
          selection: stored.value.selection || null,
          async updateSelection(selection) {
            if (runtimeEpoch !== epoch) throw Error('Runtime ownership changed')
            const result = await stateWriter.update((current) => {
              if (runtimeEpoch !== epoch || current.endpoint !== connection.endpoint) return undefined
              return { ...current, selection }
            })
            if (runtimeEpoch !== epoch || !result.applied) throw Error('Runtime ownership changed')
            this.selection = selection
          },
          async updateSelectionIf(selection, owns) {
            const result = await stateWriter.updateIf(
              (current) => runtimeEpoch === epoch && current.endpoint === connection.endpoint && owns(),
              (current) => ({ ...current, selection }),
            )
            if (!result.applied || runtimeEpoch !== epoch) return false
            this.selection = selection
            return true
          },
        }
        const createdRuntime = new RuntimeHost({
          client: createClient(connection),
          state,
          sockets: new SocketOwners(),
          post: (response) => {
            if (runtimeEpoch !== epoch || runtime !== createdRuntime) return false
            return webview.postMessage(response)
          },
          getDefaultCreature: () => vscode.workspace.getConfiguration('kohakuterrarium').get('defaultCreature', ''),
          getWorkspacePath: () => vscode.workspace.workspaceFolders?.[0]?.uri.fsPath || null,
          socketFactory: (url, protocols) => new WebSocket(url, protocols),
          webSocketBase: webSocketBase(connection.endpoint),
          token: connection.token,
          runtimeEpoch: readyId,
        })
        topology = new TopologyWatcher({
          socketFactory: (url, protocols) => new WebSocket(url, protocols),
          endpoint: connection.endpoint,
          token: connection.token,
          onInvalidate: async () => {
            if (runtimeEpoch !== epoch || runtime !== createdRuntime) return
            const result = await createdRuntime.reconcileTopologySelection()
            if (runtimeEpoch !== epoch || runtime !== createdRuntime || result.superseded) return
            await webview.postMessage({
              type: 'selection.changed',
              readyId: createdRuntime.runtimeEpoch,
              connectionId: composerConnection.id,
              data: result,
            })
          },
        })
        if (runtimeEpoch !== epoch) {
          createdRuntime.dispose()
          throw Error('Runtime ownership changed')
        }
        runtime = createdRuntime
        topology.start()
        return createdRuntime
      }

      function ensureRuntime(readyId = null) {
        if (runtime) return Promise.resolve(runtime)
        if (!runtimePromise) {
          const ownedPromise = buildRuntime(readyId).finally(() => {
            if (runtimePromise === ownedPromise) runtimePromise = null
          })
          runtimePromise = ownedPromise
        }
        return runtimePromise
      }

      const disposable = webview.onDidReceiveMessage(async (message) => {
        if (!allowedMessage(message)) return
        try {
          if (message.type === 'ready') {
            if (runtime) entry.disposeRuntime()
            const attempt = connectionAttempts.begin()
            try {
              const current = await ensureRuntime(message.requestId)
              if (!attempt.isCurrent()) {
                webview.postMessage({ type: 'ready.result', requestId: message.requestId, data: { superseded: true } })
                return
              }
              const reconciled = await current.reconcileSelection()
              if (!attempt.isCurrent() || reconciled.superseded) {
                webview.postMessage({ type: 'ready.result', requestId: message.requestId, data: { superseded: true } })
                return
              }
              webview.postMessage({
                type: 'ready.result',
                requestId: message.requestId,
                data: {
                  available: true,
                  automatic: activeConnection.source !== 'manual',
                  connectionId: composerConnection.id,
                  selection: reconciled.selection,
                  selectionVersion: reconciled.selectionVersion,
                  readyId: current.runtimeEpoch,
                },
              })
            } catch (error) {
              if (attempt.isCurrent()) {
                entry.disposeRuntime()
                sendError(message, error)
              } else {
                webview.postMessage({ type: 'ready.result', requestId: message.requestId, data: { superseded: true } })
              }
            }
            return
          }
          if (message.type === 'context.clear' || message.type === 'context.compact') {
            await dispatchContextCommand({
              message,
              getRuntime: ensureRuntime,
              isCurrent: (candidate) => runtime === candidate,
              confirmClear: confirmContextClear,
              post: (response) => webview.postMessage(response),
            })
            return
          }
          await (await ensureRuntime()).handle(message)
          if (message.type === 'session.reconcile') topology?.start()
        } catch (error) {
          sendError(message, error)
        }
      })

      view.onDidDispose(() => {
        disposable.dispose()
        entry.disposeRuntime()
        liveViews.delete(entry)
      })
    },
  }

  context.subscriptions.push(vscode.window.registerWebviewViewProvider('kohakuterrarium.chat', provider))
}

function deactivate() {}

module.exports = {
  activate,
  configure,
  confirmContextClear,
  dispatchContextCommand,
  deactivate,
  resolveConnection,
  tokenRequired,
  webSocketBase,
}
