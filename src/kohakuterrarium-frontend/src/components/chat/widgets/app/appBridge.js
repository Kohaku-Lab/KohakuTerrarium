/**
 * Host side of the ``kt.app`` bridge: JSON-RPC messages from one app frame.
 *
 * Only messages whose ``source`` is this frame's window are handled. An app
 * may send a message to the agent only shortly after the user interacted with
 * the frame, as the host sees it (focus moving into the frame, or the pointer
 * over it while it has focus).
 */

export const INTERACTION_WINDOW_MS = 5000
export const APP_ERROR = "__error__"

export function createAppBridge({
  getFrame,
  getWidget,
  host,
  getInit,
  onSent,
  onError,
  onResize,
  openLink,
  now = () => Date.now(),
}) {
  let interactionAt = -Infinity
  let hovering = false
  const empty = !host

  function frameWindow() {
    return getFrame()?.contentWindow || null
  }

  // A JSON copy: reactive proxies (widget state, data) cannot be cloned into the frame.
  function post(message) {
    frameWindow()?.postMessage(JSON.parse(JSON.stringify(message)), "*")
  }

  function reply(id, result, error) {
    if (id === undefined || id === null) return
    post(error ? { jsonrpc: "2.0", id, error: { message: error } } : { jsonrpc: "2.0", id, result })
  }

  function recentlyUsed() {
    const frame = getFrame()
    const focused = frame && globalThis.document?.activeElement === frame
    return now() - interactionAt <= INTERACTION_WINDOW_MS || (focused && hovering)
  }

  function noteInteraction() {
    interactionAt = now()
  }

  function onWindowBlur() {
    setTimeout(() => {
      if (globalThis.document?.activeElement === getFrame()) noteInteraction()
    }, 0)
  }

  function setHovering(value) {
    hovering = value
    if (value && globalThis.document?.activeElement === getFrame()) noteInteraction()
  }

  function handleMessage(event) {
    const win = frameWindow()
    if (!win || event.source !== win) return false
    const msg = event.data
    if (!msg || msg.jsonrpc !== "2.0" || typeof msg.method !== "string") return false
    const params = msg.params && typeof msg.params === "object" ? msg.params : {}
    switch (msg.method) {
      case "ui/initialize":
        reply(msg.id, getInit())
        break
      case "kt/send": {
        if (empty) {
          reply(msg.id, null, "this frame has no agent to send to")
          break
        }
        const name = typeof params.name === "string" ? params.name : ""
        if (!/^[A-Za-z0-9_.:-]{1,64}$/.test(name) || name === APP_ERROR) {
          reply(msg.id, null, "invalid message name")
          break
        }
        if (!recentlyUsed()) {
          reply(msg.id, null, "kt.send needs a recent click in the app")
          break
        }
        const data =
          params.data && typeof params.data === "object" ? params.data : { value: params.data }
        host.send(name, data)
        onSent?.(name)
        reply(msg.id, { ok: true })
        break
      }
      case "kt/state.set":
        if (!empty && params.patch && typeof params.patch === "object") host.setState(params.patch)
        break
      case "kt/openLink":
        if (typeof params.url === "string" && /^https?:\/\//i.test(params.url))
          openLink?.(params.url)
        break
      case "kt/resize":
        if (Number.isFinite(params.height)) onResize?.(params.height)
        break
      case "kt/error": {
        const message = String(params.message || "script error").slice(0, 500)
        onError?.(message)
        if (!empty && getWidget()?.awaiting)
          host.send(APP_ERROR, { message, line: params.line || 0 })
        break
      }
      default:
        reply(msg.id, null, `unknown method ${msg.method}`)
    }
    return true
  }

  function notify(method, params) {
    post({ jsonrpc: "2.0", method, params })
  }

  return { handleMessage, notify, noteInteraction, onWindowBlur, setHovering, recentlyUsed }
}
