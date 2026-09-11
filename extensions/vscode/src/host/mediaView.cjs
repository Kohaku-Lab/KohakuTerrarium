// Per-webview media bootstrap: owns a private spool root plus the ``vscode``
// adapters (open a non-text resource in an editor tab, save a copy through the
// Save dialog) and the tab-close reconciliation that drops an editor lease.
// Kept out of the extension entry so the entry stays under the 600-line limit.
const fs = require('node:fs')
const fsp = require('node:fs/promises')

const { MediaHost } = require('./mediaHost.cjs')
const { MediaSpool } = require('./mediaSpool.cjs')

// How often the View sweeps ``tabGroups.all`` for an editor tab that closed
// without delivering an ``onDidChangeTabs`` close event (a missed close).
const DEFAULT_RECONCILE_MS = 15_000

function createMediaView({
  vscode,
  webview,
  storageDir,
  reconcileIntervalMs = DEFAULT_RECONCILE_MS,
  timers = { setInterval, clearInterval },
}) {
  // A View without a storage location cannot scope a private spool root, so the
  // media surface is simply unavailable rather than writing to an arbitrary path.
  if (!storageDir) return null
  fs.mkdirSync(storageDir, { recursive: true })
  const spool = new MediaSpool({
    base: '',
    token: '',
    spoolBase: storageDir,
    asWebviewUri: (filePath) => String(webview.asWebviewUri(vscode.Uri.file(filePath))),
  })

  let reconcileTimer = null

  // Every file path that currently drives an open editor tab, so a stale editor
  // lease whose close event was missed can be told apart from a live one.
  const openTabPaths = () => {
    const paths = new Set()
    const groups = vscode.window?.tabGroups?.all || []
    for (const group of groups) {
      for (const tab of group?.tabs || []) {
        const fsPath = tab?.input?.uri?.fsPath
        if (fsPath) paths.add(fsPath)
      }
    }
    return paths
  }

  const stopReconcile = () => {
    if (reconcileTimer !== null) {
      timers.clearInterval(reconcileTimer)
      reconcileTimer = null
    }
  }

  const reconcileEditors = () => {
    const tracked = mediaHost.editorFilePaths()
    if (tracked.length === 0) {
      stopReconcile()
      return
    }
    const open = openTabPaths()
    for (const filePath of tracked) {
      if (!open.has(filePath)) mediaHost.closeEditor(filePath)
    }
  }

  // Lazily arm the sweep only while at least one editor lease exists, so an idle
  // View never holds a timer. A retained editor lease after view dispose keeps it
  // armed; the extension's ``dispose`` (deactivate) is what finally clears it.
  const ensureReconcile = () => {
    if (reconcileTimer !== null || mediaHost.editorFilePaths().length === 0) return
    reconcileTimer = timers.setInterval(reconcileEditors, reconcileIntervalMs)
    reconcileTimer?.unref?.()
  }

  const mediaHost = new MediaHost({
    spool,
    openEditor: async (resource) => {
      // The backend shim opens a non-text resource; it is not a TextDocument, so
      // leases are reconciled through the tab's own input URI.
      await vscode.commands.executeCommand('vscode.open', vscode.Uri.file(resource.filePath))
    },
    // Report the save outcome explicitly: a dismissed dialog resolves
    // ``{ saved: false }`` so the Host can answer ``cancelled`` instead of ``ok``.
    saveAs: async (resource) => {
      const target = await vscode.window.showSaveDialog({
        defaultUri: vscode.Uri.file(resource.name),
        saveLabel: 'Save media',
      })
      if (!target) return { saved: false }
      await fsp.copyFile(resource.filePath, target.fsPath)
      return { saved: true }
    },
    onEditorLeaseChange: () => {
      if (mediaHost.editorFilePaths().length > 0) ensureReconcile()
      else stopReconcile()
    },
  })

  let tabSubscription = null
  const tabGroups = vscode.window?.tabGroups
  if (typeof tabGroups?.onDidChangeTabs === 'function') {
    tabSubscription = tabGroups.onDidChangeTabs((event) => {
      for (const tab of event?.closed || []) {
        const fsPath = tab?.input?.uri?.fsPath
        if (fsPath) mediaHost.closeEditor(fsPath)
      }
      // A close we just handled may have emptied the lease set (disarming the
      // sweep); an ``opened`` change may have armed a fresh lease.
      ensureReconcile()
    })
  }
  return {
    mediaHost,
    resourceRoot: vscode.Uri.file(storageDir),
    start: () => spool.start(),
    setBackend: (base, token) => {
      const next = token || ''
      // The backend/credentials can be re-resolved while a runtime is reused. A change
      // must abort every in-flight prepare so a spooled body is never completed against
      // a stale endpoint or a stale Host token.
      if (base !== spool.base || next !== spool.token) mediaHost.abortAll()
      spool.base = base
      spool.token = next
    },
    releaseView: () => mediaHost.releaseView(),
    dispose: async () => {
      tabSubscription?.dispose()
      tabSubscription = null
      stopReconcile()
      await mediaHost.dispose()
    },
  }
}

module.exports = { createMediaView }
