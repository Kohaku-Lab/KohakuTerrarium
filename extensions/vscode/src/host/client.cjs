const { validateEndpoint } = require('./protocol.cjs')

function encode(value) {
  return encodeURIComponent(value)
}

// Fixed-route query builder: drops absent cursors and stringifies the
// remaining opaque values. Only Host-controlled option keys reach here.
function historyQuery(params) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params || {})) {
    if (value === undefined || value === null) continue
    search.set(key, String(value))
  }
  const query = search.toString()
  return query ? `?${query}` : ''
}

// Sub-agent query builder: the shared leaves omit an identifier rather than
// sending an empty one, so a blank string is dropped exactly like ``null`` and
// never reaches the wire as ``?name=``.
function subagentQuery(params) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params || {})) {
    if (value === undefined || value === null || value === '') continue
    search.set(key, String(value))
  }
  const query = search.toString()
  return query ? `?${query}` : ''
}

function titleFor(row, creatures) {
  const candidate = row.title || row.display_name || row.config_name || row.name || ''
  if (candidate && !/^graph_[A-Za-z0-9]+$/.test(candidate)) return String(candidate)
  if (creatures.length) {
    return creatures
      .map((creature) => creature.name)
      .slice()
      .sort((a, b) => a.localeCompare(b))
      .join(', ')
  }
  return 'Session'
}

function normalizeSession(row) {
  const live = row.is_live === true || row.isLive === true
  const creatures = Array.isArray(row.creatures)
    ? row.creatures.map((creature) => {
        const rawId = creature.creature_id ?? creature.id ?? null
        if (live && !rawId) throw Error('Live Creature has no stable identity')
        return {
          id: rawId == null ? null : String(rawId),
          name: String(creature.name ?? creature.creature_name ?? ''),
        }
      })
    : []

  return {
    conversationId: row.conversation_id == null ? null : String(row.conversation_id),
    runtimeId: row.runtime_id == null ? null : String(row.runtime_id),
    savedName: row.saved_name ?? row.savedName ?? null,
    title: titleFor(row, creatures),
    isLive: live,
    kind: row.type === 'terrarium' || row.kind === 'terrarium' ? 'terrarium' : 'creature',
    creatures,
  }
}

function createClient({ endpoint, token, fetchImpl = fetch }) {
  const base = validateEndpoint(endpoint)
  if (typeof token !== 'string') throw Error('Host token must be a string')

  async function request(path, options = {}) {
    const response = await fetchImpl(`${base}${path}`, {
      ...options,
      redirect: 'error',
      headers: {
        ...(options.headers || {}),
        ...(token ? { 'X-KT-Host-Token': token } : {}),
      },
    })
    if (!response.ok) {
      const error = Error(`KT request failed: ${response.status}`)
      error.status = response.status
      throw error
    }
    return response
  }

  return {
    async capabilities() {
      return (await request('/api/auth/capabilities')).json()
    },
    async diagnostics() {
      return (await request('/api/catalog/server-info/diagnostics')).json()
    },
    async listOpen(options = {}) {
      const body = await (await request('/api/sessions/open', options)).json()
      return (Array.isArray(body) ? body : body.sessions || []).map(normalizeSession)
    },
    async createCreature({ configPath, pwd, name }) {
      return (
        await request('/api/sessions/active/creature', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ config_path: configPath, pwd, name }),
        })
      ).json()
    },
    async active(session) {
      return (await request(`/api/sessions/active/${encode(session)}`)).json()
    },
    async resume(savedName) {
      return (await request(`/api/sessions/${encode(savedName)}/resume`, { method: 'POST' })).json()
    },
    async stop(session) {
      return (await request(`/api/sessions/active/${encode(session)}`, { method: 'DELETE' })).json()
    },
    async history(session, creature) {
      return (await request(`/api/sessions/${encode(session)}/creatures/${encode(creature)}/history`)).json()
    },
    async historyPage(session, creature, options = {}) {
      const params = { ...options }
      params.limit = params.limit == null ? 400 : Math.min(params.limit, 400)
      const query = historyQuery({ paged: true, ...params })
      return (await request(`/api/sessions/${encode(session)}/creatures/${encode(creature)}/history${query}`)).json()
    },
    async historyDetail(session, creature, params = {}) {
      const query = historyQuery(params)
      return (await request(`/api/sessions/${encode(session)}/creatures/${encode(creature)}/history/detail${query}`)).json()
    },
    // Live sub-agent inner conversation. Identified by the exact live ``job_id``,
    // the interactive ``name``, or the persisted ``name`` + ``run``; the fixed
    // route never carries a caller-chosen URL.
    async subagentConversation(session, creature, options = {}) {
      const query = subagentQuery({ job_id: options.jobId, name: options.name, run: options.run })
      return (await request(`/api/sessions/${encode(session)}/creatures/${encode(creature)}/subagents/conversation${query}`)).json()
    },
    // Send to a LIVE sub-agent. A non-live run answers 409, which the caller sees
    // as the exact status: a mutation is never retried or masked as a timeout.
    async sendSubagentMessage(session, creature, name, options = {}) {
      const body = { content: options.content }
      if (options.jobId) body.job_id = options.jobId
      return (
        await request(`/api/sessions/${encode(session)}/creatures/${encode(creature)}/subagents/${encode(name)}/send`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        })
      ).json()
    },
    // Persisted sub-agent runs for a session (read-only discovery).
    async listSubagents(session, options = {}) {
      const query = subagentQuery({ parent: options.parent, job_id: options.jobId, name: options.name })
      return (await request(`/api/sessions/${encode(session)}/subagents${query}`)).json()
    },
    // Persisted sub-agent conversation. There is deliberately no saved SEND:
    // a finished run is read-only, matching the shared leaf's own contract.
    async savedSubagentConversation(session, options = {}) {
      const query = subagentQuery({ parent: options.parent, job_id: options.jobId, name: options.name, run: options.run })
      return (await request(`/api/sessions/${encode(session)}/subagents/conversation${query}`)).json()
    },
    // Move a running job to the background. The exact backend status is forwarded
    // (this is a mutation), never reinterpreted or retried.
    async promote(session, creature, jobId) {
      return (
        await request(`/api/sessions/${encode(session)}/creatures/${encode(creature)}/promote/${encode(jobId)}`, { method: 'POST' })
      ).json()
    },
    async interrupt(session, creature) {
      return (
        await request(`/api/sessions/${encode(session)}/creatures/${encode(creature)}/interrupt`, {
          method: 'POST',
        })
      ).json()
    },
    async creatureCommand(session, creature, command, args, { signal } = {}) {
      return (
        await request(`/api/sessions/${encode(session)}/creatures/${encode(creature)}/command`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ command, args }),
          signal,
        })
      ).json()
    },
  }
}

function validateCapabilities(body) {
  if (
    body?.schema !== 1 ||
    typeof body?.auth?.host_token?.enabled !== 'boolean' ||
    typeof body?.auth?.admin_token?.enabled !== 'boolean' ||
    body?.auth?.multi_user?.enabled !== false
  ) {
    throw Error('Unsupported KT capabilities')
  }
  return body
}

module.exports = { createClient, normalizeSession, validateCapabilities }
