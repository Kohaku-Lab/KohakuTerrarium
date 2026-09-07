function normalizeDaemonEndpoint(value) {
  let url
  try {
    url = new URL(value)
  } catch {
    throw Error('Invalid daemon endpoint')
  }
  if (url.protocol !== 'http:' || !url.port || url.pathname !== '/' || url.search || url.hash) {
    throw Error('Daemon endpoint must be explicit-port HTTP')
  }
  if (url.hostname === '0.0.0.0') return `http://127.0.0.1:${url.port}`
  if (url.hostname === '[::]') return `http://[::1]:${url.port}`
  if (!['127.0.0.1', '[::1]', 'localhost'].includes(url.hostname)) {
    throw Error('Daemon endpoint is not loopback')
  }
  return url.hostname === 'localhost' ? `http://127.0.0.1:${url.port}` : url.origin
}

function supported(capabilities) {
  const auth = capabilities?.auth
  return (
    capabilities?.schema === 1 &&
    typeof auth?.host_token?.enabled === 'boolean' &&
    typeof auth?.admin_token?.enabled === 'boolean' &&
    auth?.multi_user?.enabled === false
  )
}

function result(endpoint, capabilities, source) {
  const hostToken = capabilities.auth?.host_token || {}
  return {
    endpoint,
    capabilities,
    source,
    requiresToken: hostToken.enabled === true && hostToken.loopback_bypass !== true,
  }
}

async function supportedCandidates(endpoints, probe) {
  const results = await Promise.all(
    endpoints.map(async (endpoint) => {
      try {
        const capabilities = await probe(endpoint)
        return supported(capabilities) ? { endpoint, capabilities } : null
      } catch {
        return null
      }
    }),
  )
  return results.filter(Boolean)
}

async function discoverLocalKt({
  readState,
  isPidAlive = () => true,
  probe,
  verifyProbe = async () => true,
  selectStrictCandidate = async (candidates) => candidates[0],
  ports = Array.from({ length: 50 }, (_, index) => 8001 + index),
  concurrency = 8,
}) {
  let state = null
  try {
    state = await readState()
  } catch {}
  if (state?.bound === true && state.url && isPidAlive(Number(state.pid || 0))) {
    try {
      const endpoint = normalizeDaemonEndpoint(state.url)
      const capabilities = await probe(endpoint)
      if (supported(capabilities)) return result(endpoint, capabilities, 'daemon')
    } catch {}
  }

  const strictCandidates = []
  for (let index = 0; index < ports.length; index += concurrency) {
    const endpoints = ports.slice(index, index + concurrency).map((port) => `http://127.0.0.1:${port}`)
    const found = await supportedCandidates(endpoints, probe)
    for (const match of found) {
      const candidate = result(match.endpoint, match.capabilities, 'probe')
      if (candidate.requiresToken) {
        strictCandidates.push(candidate)
        continue
      }
      try {
        if (await verifyProbe(candidate.endpoint)) return candidate
      } catch {}
    }
  }
  if (strictCandidates.length) {
    const selected = await selectStrictCandidate(strictCandidates)
    if (strictCandidates.includes(selected)) return selected
    const error = Error('Local KT discovery selection cancelled')
    error.code = 'KT_DISCOVERY_CANCELLED'
    throw error
  }
  throw Error('No supported local KohakuTerrarium service was found')
}

module.exports = { discoverLocalKt, normalizeDaemonEndpoint }
