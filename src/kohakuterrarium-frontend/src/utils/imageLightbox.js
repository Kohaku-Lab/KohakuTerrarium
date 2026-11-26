import { useLocaleStore } from "@/stores/locale"
import { translate } from "@/utils/i18n"

const LIGHTBOX_ATTR = "data-kt-image-lightbox"

function label(key) {
  let locale = "en"
  try {
    locale = useLocaleStore().locale || "en"
  } catch {
    locale = "en"
  }
  return translate(locale, key)
}

function fileName(url) {
  try {
    const path = new URL(url, window.location.href).pathname
    return decodeURIComponent(path.split("/").pop() || "") || "image"
  } catch {
    return "image"
  }
}

function button(text, style) {
  const el = document.createElement("button")
  el.type = "button"
  el.textContent = text
  el.style.cssText =
    "padding:6px 14px;border-radius:6px;border:1px solid rgba(255,255,255,.35);" +
    "background:rgba(0,0,0,.55);color:#fff;font:inherit;font-size:13px;cursor:pointer;" +
    (style || "")
  return el
}

/** Download `url` through the page itself, with `<a download>` semantics. */
export function downloadUrl(url, name = fileName(url)) {
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = name
  anchor.rel = "noopener"
  anchor.style.display = "none"
  document.body.appendChild(anchor)
  anchor.click()
  anchor.remove()
}

/** Close the open image lightbox, if any. */
export function closeImageLightbox() {
  for (const el of document.querySelectorAll(`[${LIGHTBOX_ATTR}]`)) el.__ktClose?.()
}

/**
 * Show the image at `url` over the app in a full-window viewer with Save and
 * Close; Escape or a click on the backdrop closes it. Returns the close function.
 */
export function openImageLightbox(url) {
  closeImageLightbox()
  const overlay = document.createElement("div")
  overlay.setAttribute(LIGHTBOX_ATTR, "")
  overlay.setAttribute("role", "dialog")
  overlay.setAttribute("aria-modal", "true")
  overlay.style.cssText =
    "position:fixed;inset:0;z-index:10000;display:flex;flex-direction:column;" +
    "align-items:center;justify-content:center;gap:12px;padding:16px;" +
    "background:rgba(0,0,0,.82);box-sizing:border-box;"

  const image = document.createElement("img")
  image.src = url
  image.alt = fileName(url)
  image.style.cssText =
    "max-width:100%;max-height:calc(100% - 56px);object-fit:contain;border-radius:4px;"

  const bar = document.createElement("div")
  bar.style.cssText = "display:flex;gap:8px;"
  const save = button(label("chat.media.save"))
  const close = button(label("common.close"))
  bar.append(save, close)
  overlay.append(image, bar)

  const onKey = (event) => {
    if (event.key === "Escape") {
      event.preventDefault()
      dismiss()
    }
  }
  function dismiss() {
    document.removeEventListener("keydown", onKey, true)
    overlay.remove()
  }
  overlay.__ktClose = dismiss
  overlay.addEventListener("click", (event) => {
    if (event.target === overlay) dismiss()
  })
  save.addEventListener("click", () => downloadUrl(url))
  close.addEventListener("click", dismiss)
  document.addEventListener("keydown", onKey, true)
  document.body.appendChild(overlay)
  close.focus()
  return dismiss
}
