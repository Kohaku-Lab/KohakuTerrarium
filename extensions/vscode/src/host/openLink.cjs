const { allowedMessage } = require('./protocol.cjs')

// Host-side platform link opener. The Webview hands over one model-authored
// reference on a USER CLICK; the Host is the only side that turns it into a
// concrete URL and calls the injected ``vscode.env.openExternal``. It is
// deliberately narrow:
//
//   - only an absolute ``http(s)`` URL or a path relative to the live backend
//     base is opened;
//   - ``javascript:``/``data:``/``command:``/``file:``/``blob:`` and every other
//     scheme, a protocol-relative ``//host`` reference, any backslash, and a URL
//     carrying embedded credentials are refused;
//   - ``#hash`` and the OS-owned ``mailto:``/``tel:`` schemes keep their native
//     handling in the shared leaves, so if one arrives here it is refused too;
//   - the host token is never part of the opened URL and no request is made
//     through any generic proxy: resolution is pure string work against the
//     explicit backend origin the View already resolved.
const SCHEME = /^[a-z][a-z0-9+.-]*:/i
const NAVIGABLE_SCHEME = /^https?:$/i

function credentialFree(url) {
  if (url.username || url.password) return null
  return url.href
}

// Resolve one raw reference to a URL to hand to ``openExternal``, or ``null``
// when it is not a supported/ safe target. Pure and side-effect free so it can
// be reasoned about (and tested) on its own.
function resolveOpenTarget(target, backendBase) {
  if (typeof target !== 'string') return null
  const raw = target.trim()
  if (!raw || raw.startsWith('#')) return null
  // A backslash is not a URL separator on the supported hosts and can smuggle a
  // differently-parsed path, so the reference is refused rather than normalized.
  if (raw.includes('\\')) return null

  if (SCHEME.test(raw)) {
    if (!NAVIGABLE_SCHEME.test(raw.split(':', 1)[0] + ':')) return null
    try {
      return credentialFree(new URL(raw))
    } catch {
      return null
    }
  }

  // Protocol-relative ``//host`` names its own scheme/host; the backend base does
  // not supply one for the model's target, so it is refused instead of guessed.
  if (raw.startsWith('//')) return null
  if (typeof backendBase !== 'string' || !backendBase) return null
  try {
    const url = new URL(raw, `${backendBase.replace(/\/+$/, '')}/`)
    if (!NAVIGABLE_SCHEME.test(url.protocol)) return null
    return credentialFree(url)
  } catch {
    return null
  }
}

async function openPlatformLink(host, message) {
  if (!allowedMessage(message)) throw Error('Invalid platform link envelope')
  // Admission is the ready epoch plus the explicit-intent fence and no in-flight
  // selection mutation: a click captured under one connection/selection must not
  // open against another.
  const intent = host.selectionIntentVersion
  const owns = () =>
    !host.disposed &&
    message.readyId === host.runtimeEpoch &&
    intent === host.selectionIntentVersion &&
    host.pendingSelectionMutations === 0
  if (!owns()) throw Error('Selected Creature ownership changed')

  const url = resolveOpenTarget(message.target, host.backendBase)
  if (!url) throw Error('Unsupported link target')
  if (typeof host.openExternal !== 'function') throw Error('Host cannot open external links')

  // The click is the user gesture; opening the browser is the only side effect.
  const opened = await host.openExternal(url)

  // Re-check AFTER the await: the ready epoch or selection intent can move on
  // while the browser opens. The open may already have happened, so it cannot be
  // undone — but a stale document must never receive the result.
  if (!owns()) return { suppressed: true }
  if (opened === false) throw Error('Could not open link')
  return { opened: true }
}

module.exports = { resolveOpenTarget, openPlatformLink }
