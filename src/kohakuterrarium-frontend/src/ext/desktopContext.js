import { computed, onUnmounted, readonly, shallowRef, watch } from "vue"

import { useAuthStore } from "@/stores/auth"
import { useHostsStore } from "@/stores/hosts"
import { getRuntimeScope } from "@/stores/runtimeScope"
import { hostKind } from "@/utils/hostBridge"

export const desktopWork = shallowRef(null)
export const desktopContextConnected = shallowRef(false)
const CHANNEL = "kt:desktop-context:v2"
const MAX_AGE = 10000
let connection = null

export function contextRealm() {
  const hosts = useHostsStore()
  const user = hosts.activeHostId ? hosts.activeUser : useAuthStore().sameOriginUser
  return JSON.stringify([
    hosts.activeHostId,
    hosts.activeHost?.url || "",
    user?.id || user?.username || "",
  ])
}

function bindHost(message) {
  const hosts = useHostsStore()
  hosts.reloadFromStorage()
  if (message.hostId !== hosts.activeHostId) hosts.activeHostId = message.hostId
  if (!message.hostId) useAuthStore().sameOriginUser = message.user || null
}

/** Same-origin, memory-only snapshots; only the main window selects and rebroadcasts a source. */
export function createContextRelay({
  mode,
  port,
  realm,
  host,
  bind = () => {},
  receive = () => {},
  now = Date.now,
  id = crypto.randomUUID(),
}) {
  let sequence = 0
  let version = 0
  let current = null
  let selected = id
  let selectedSeen = now()
  let claimedAt = 0
  let authority = null
  let seen = 0
  let epoch = 0
  let connected = mode === "main"
  let closed = false
  const orders = new Map()
  function expireSource() {
    if (selected !== id && current && now() - selectedSeen > MAX_AGE) {
      current = null
      version++
    }
  }
  function broadcast(probe = false) {
    port.postMessage({
      probe,
      type: "context",
      source: id,
      epoch,
      sequence: ++sequence,
      realm: realm(),
      host: host(),
      selected,
      work: current,
      version,
      sent: now(),
    })
  }
  function message({ data: m }) {
    if (closed || !m || typeof m !== "object") return
    if (mode === "main") {
      expireSource()
      if (m.type === "request") {
        broadcast(true)
        return
      }
      if (
        m.type !== "work" ||
        m.realm !== realm() ||
        m.authority !== id ||
        m.epoch !== epoch ||
        typeof m.source !== "string" ||
        !Number.isSafeInteger(m.sequence)
      )
        return
      if (m.sequence <= (orders.get(m.source) || 0)) return
      orders.set(m.source, m.sequence)
      if (now() - m.sent > MAX_AGE || m.sent > now() + MAX_AGE) return
      if (m.claim && m.sent >= claimedAt) {
        if (selected !== m.source) version++
        selected = m.source
        claimedAt = m.sent
      }
      if (selected !== m.source) return
      selectedSeen = now()
      if (current?.sessionId !== m.work?.sessionId) version++
      current = m.work
      broadcast()
    } else if (
      m.type === "context" &&
      Number.isSafeInteger(m.sequence) &&
      typeof m.realm === "string"
    ) {
      if (authority === m.source && m.sequence <= seen) return
      if (now() - m.sent > MAX_AGE || m.sent > now() + MAX_AGE) return
      if (authority && authority !== m.source && m.epoch < epoch) return
      const before = realm()
      const epochChanged = epoch !== m.epoch
      const identityChanged = authority !== m.source || epochChanged || m.realm !== before
      const baseline = !connected || now() - selectedSeen > MAX_AGE || identityChanged
      if (bind(m.host, { identityChanged }) === false) return
      authority = m.source
      epoch = m.epoch
      seen = m.sequence
      selectedSeen = now()
      connected = true
      const rebound = epochChanged || before !== realm() || m.realm !== before
      if (rebound && mode === "popout")
        window.dispatchEvent(new Event("kt:desktop-context-rebound"))
      receive(m.realm === realm() ? m.work : null, {
        baseline: baseline || rebound,
        version: `${authority}:${m.version}`,
      })
      if (m.probe && mode === "popout") window.dispatchEvent(new Event("kt:desktop-context-probe"))
    }
  }
  port.addEventListener("message", message)
  epoch = now()
  const request = () => port.postMessage({ type: "request" })
  if (mode !== "main") request()
  return {
    publish(work, claim = false) {
      if (closed || mode === "overlay") return
      if (mode === "main") {
        if (claim) {
          if (selected !== id) version++
          selected = id
          selectedSeen = now()
          claimedAt = now()
        }
        if (selected !== id) return
        if (current?.sessionId !== work?.sessionId) version++
        current = work
        broadcast()
      } else
        port.postMessage({
          type: "work",
          source: id,
          authority,
          epoch,
          sequence: ++sequence,
          realm: realm(),
          work,
          claim,
          sent: now(),
        })
    },
    reset() {
      epoch = Math.max(now(), epoch + 1)
      selected = id
      orders.clear()
      current = null
      version++
      if (mode === "main") broadcast()
    },
    tick() {
      if (closed) return
      if (mode === "main") {
        expireSource()
        broadcast()
      } else {
        if (connected && now() - selectedSeen > MAX_AGE) {
          connected = false
          receive(null, { baseline: true, connected: false })
        }
        request()
      }
    },
    close() {
      closed = true
      port.removeEventListener("message", message)
      port.close()
    },
  }
}

/** Start before extension loading so a secondary renderer adopts the main host/user identity. */
export async function startDesktopContext(mode, invalidate = () => {}) {
  if (hostKind() !== "tauri" || typeof BroadcastChannel !== "function" || connection) return
  let resolve
  let readyTimer
  const ready = new Promise((done) => {
    resolve = done
    readyTimer = setTimeout(done, MAX_AGE)
  })
  const port = new BroadcastChannel(CHANNEL)
  let baseline = 0
  let bootstrapped = false
  connection = createContextRelay({
    mode,
    port,
    realm: contextRealm,
    host: () => {
      const user = useAuthStore().sameOriginUser
      return {
        hostId: useHostsStore().activeHostId,
        user: user ? { id: user.id, username: user.username, role: user.role } : null,
      }
    },
    bind: (message, info) => {
      if (mode === "popout" && bootstrapped) {
        if (info.identityChanged) {
          connection?.close()
          invalidate()
          return false
        }
        return
      }
      bindHost(message)
    },
    receive: (work, info) => {
      desktopContextConnected.value = info.connected !== false
      if (info.baseline) baseline++
      desktopWork.value = work
        ? Object.freeze({ ...work, baseline: `${info.version}:${baseline}` })
        : null
      resolve()
      clearTimeout(readyTimer)
    },
  })
  const timer = setInterval(() => connection?.tick(), 2000)
  const stopRealm =
    mode === "main"
      ? watch([contextRealm, getRuntimeScope], () => connection?.reset(), { flush: "sync" })
      : () => {}
  window.addEventListener(
    "pagehide",
    () => {
      clearInterval(timer)
      clearTimeout(readyTimer)
      stopRealm()
      connection?.close()
      connection = null
    },
    { once: true },
  )
  if (mode !== "main") await ready
  else clearTimeout(readyTimer)
  bootstrapped = true
}

export function useDesktopPublisher(getWork) {
  if (!connection) return
  const work = getWork()
  const publish = () => connection?.publish(work.value)
  const focus = () => connection?.publish(work.value, true)
  const stop = watch(work, publish, { immediate: true, deep: true })
  const rebound = () => {
    if (document.hasFocus()) focus()
  }
  window.addEventListener("focus", focus)
  window.addEventListener("kt:desktop-context-rebound", rebound)
  window.addEventListener("kt:desktop-context-probe", publish)
  if (document.hasFocus()) focus()
  const heartbeat = setInterval(publish, 2000)
  onUnmounted(() => {
    stop()
    clearInterval(heartbeat)
    window.removeEventListener("focus", focus)
    window.removeEventListener("kt:desktop-context-rebound", rebound)
    window.removeEventListener("kt:desktop-context-probe", publish)
  })
}

export const relayedWork = computed(() => (desktopWork.value ? readonly(desktopWork.value) : null))
