async function verifyConnection(connection, verify, timeoutMs) {
  const controller = new AbortController()
  let timer
  const expired = new Promise((_, reject) => {
    timer = setTimeout(() => {
      reject(Error('Local KT connection verification timed out'))
      controller.abort()
    }, timeoutMs)
  })
  try {
    await Promise.race([verify(connection, { signal: controller.signal }), expired])
  } finally {
    clearTimeout(timer)
    controller.abort()
  }
}

async function resolveLocalConnection({ discover, getStoredToken, promptToken, storeToken = async () => {}, verify, timeoutMs = 5_000 }) {
  const local = await discover()
  if (!local.requiresToken) {
    const connection = { ...local, token: '' }
    await verifyConnection(connection, verify, timeoutMs)
    return connection
  }

  const storedToken = (await getStoredToken()) || ''
  if (storedToken) {
    const storedConnection = { ...local, token: storedToken }
    try {
      await verifyConnection(storedConnection, verify, timeoutMs)
      return storedConnection
    } catch (error) {
      if (error?.status !== 401) throw error
    }
  }

  const token = (await promptToken()) || ''
  if (!token) throw Error('Host token is required by the local service')
  const connection = { ...local, token }
  await verifyConnection(connection, verify, timeoutMs)
  await storeToken(token)
  return connection
}

module.exports = { resolveLocalConnection }
