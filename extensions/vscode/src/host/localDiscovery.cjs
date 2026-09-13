const fs = require('node:fs/promises')
const os = require('node:os')
const path = require('node:path')

const { discoverLocalKt } = require('./discovery.cjs')

function daemonStatePath() {
  return path.join(os.homedir(), '.kohakuterrarium', 'run', 'web.json')
}

async function readDaemonState() {
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      return JSON.parse(await fs.readFile(daemonStatePath(), 'utf8'))
    } catch {
      if (attempt < 2) await new Promise((resolve) => setTimeout(resolve, 25))
    }
  }
  return null
}

function isPidAlive(pid) {
  if (!Number.isSafeInteger(pid) || pid < 1) return false
  try {
    process.kill(pid, 0)
    return true
  } catch {
    return false
  }
}

async function probeCapabilities(endpoint, timeoutMs = 500) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(`${endpoint}/api/auth/capabilities`, {
      signal: controller.signal,
      redirect: 'error',
    })
    if (!response.ok) throw Error(`KT discovery failed: ${response.status}`)
    return await response.json()
  } finally {
    clearTimeout(timer)
    controller.abort()
  }
}

async function verifyKtProbe(endpoint, timeoutMs = 500, token = '') {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  try {
    const response = await fetch(`${endpoint}/api/catalog/server-info/diagnostics`, {
      signal: controller.signal,
      redirect: 'error',
      headers: token ? { 'X-KT-Host-Token': token } : {},
    })
    if (!response.ok) {
      const error = Error(`KT identity verification failed: ${response.status}`)
      error.status = response.status
      throw error
    }
    const body = await response.json()
    return typeof body?.version === 'string' && Number.isSafeInteger(body?.daemon?.pid) && typeof body?.daemon?.mode === 'string'
  } finally {
    clearTimeout(timer)
    controller.abort()
  }
}

function discoverInstalledKt({ selectStrictCandidate } = {}) {
  return discoverLocalKt({
    readState: readDaemonState,
    isPidAlive,
    probe: probeCapabilities,
    verifyProbe: verifyKtProbe,
    selectStrictCandidate,
  })
}

module.exports = {
  daemonStatePath,
  discoverInstalledKt,
  isPidAlive,
  probeCapabilities,
  readDaemonState,
  verifyKtProbe,
}
