/**
 * The document a frame contribution runs in.
 *
 * The package's own HTML is parsed (not executed) and given, ahead of its
 * content, a ``<base>`` for its asset folder, a content security policy that
 * lets it load scripts, styles and images only from that folder's origin and
 * forbids network access of its own, and the bridge client script.
 */

import { FRAME_CLIENT_SOURCE } from "@/ext/sdk/frameBridge"

/** Content security policy of a frame; ``origin`` is where its asset files are served from. */
export function frameCsp(origin) {
  return [
    "default-src 'none'",
    `script-src 'unsafe-inline' ${origin}`,
    `style-src 'unsafe-inline' ${origin}`,
    `img-src ${origin} data: blob:`,
    `font-src ${origin} data:`,
    `media-src ${origin} data: blob:`,
    "connect-src 'none'",
    "frame-src 'none'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
  ].join("; ")
}

/** The sandbox attribute of a frame: scripts run, but with an opaque origin and no other power. */
export const FRAME_SANDBOX = "allow-scripts"

/**
 * ``srcdoc`` for a frame: ``html`` is the package's page, ``base`` the URL of
 * the folder its relative files load from, ``origin`` that URL's origin.
 */
export function buildFrameDocument(html, { base, origin }) {
  const doc = new DOMParser().parseFromString(String(html || ""), "text/html")
  for (const node of doc.querySelectorAll("base, meta[http-equiv]")) node.remove()
  const head = doc.head
  const baseEl = doc.createElement("base")
  baseEl.setAttribute("href", base)
  const csp = doc.createElement("meta")
  csp.setAttribute("http-equiv", "Content-Security-Policy")
  csp.setAttribute("content", frameCsp(origin))
  const client = doc.createElement("script")
  client.textContent = FRAME_CLIENT_SOURCE
  head.prepend(baseEl, csp, client)
  if (!doc.querySelector("meta[charset]")) {
    const charset = doc.createElement("meta")
    charset.setAttribute("charset", "utf-8")
    head.prepend(charset)
  }
  return `<!doctype html>${doc.documentElement.outerHTML}`
}
