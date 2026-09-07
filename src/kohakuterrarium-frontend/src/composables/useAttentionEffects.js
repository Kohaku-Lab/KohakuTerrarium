import { onBeforeUnmount, onMounted, watch } from "vue"

import { subscribeAttention, subscribeAttentionEdges, totalAttention } from "@/stores/attention"
import { useAttentionPrefs } from "@/stores/attentionPrefs"
import { attentionTargetLabel, navigateToAttention } from "@/utils/attentionNavigation"
import { useI18n } from "@/utils/i18n"

const DESKTOP_PROTOCOL = 1
const AUDIO_UNLOCK_EVENT = "kt:attention-audio-unlock"

export function requestAttentionAudioUnlock() {
  document.dispatchEvent(new Event(AUDIO_UNLOCK_EVENT))
}

export async function requestNotificationPermission() {
  if (typeof Notification === "undefined" || !window.isSecureContext) return "unsupported"
  try {
    return await Notification.requestPermission()
  } catch {
    return "unsupported"
  }
}

export function useAttentionEffects() {
  const prefs = useAttentionPrefs()
  const { t } = useI18n()
  let unsubscribeEdges
  let unsubscribeAttention
  let desktopSurface = Boolean(window.pywebview?.api)
  let desktopCapabilities = null
  let audioContext = null
  let favicons = []
  let originalFavicons = []
  let pendingDesktopAttention = false

  async function detectDesktopSurface() {
    const api = window.pywebview?.api
    if (!api) return
    desktopSurface = true
    try {
      const capabilities = await api.get_desktop_capabilities?.()
      if (capabilities?.surface === "desktop" && capabilities.protocol === DESKTOP_PROTOCOL) {
        desktopCapabilities = capabilities
        if (pendingDesktopAttention) {
          pendingDesktopAttention = false
          requestDesktopAttention()
        }
      }
    } catch {
      desktopCapabilities = null
    }
  }

  function unlockAudio() {
    if (!prefs.state.attentionSound) return
    const AudioContext = window.AudioContext || window.webkitAudioContext
    if (!AudioContext) return
    try {
      audioContext ||= new AudioContext()
      if (audioContext.state === "suspended") void audioContext.resume?.().catch(() => {})
    } catch {
      audioContext = null
    }
  }

  function playSound(kind) {
    const enabled =
      prefs.state.attentionSound &&
      ((kind === "waiting-input" && prefs.state.soundWaiting) ||
        (kind === "completed" && prefs.state.soundCompletion))
    if (!enabled || !audioContext || audioContext.state === "suspended") return
    try {
      const oscillator = audioContext.createOscillator()
      const gain = audioContext.createGain()
      const start = audioContext.currentTime
      oscillator.frequency.value = kind === "waiting-input" ? 880 : 660
      gain.gain.setValueAtTime(0.0001, start)
      gain.gain.exponentialRampToValueAtTime(0.06, start + 0.01)
      gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.12)
      oscillator.connect(gain)
      gain.connect(audioContext.destination)
      oscillator.start(start)
      oscillator.stop(start + 0.13)
    } catch {
      // Audio is best-effort and must not affect other reminder channels.
    }
  }

  function notificationContent(event) {
    const waiting = event.kind === "waiting-input"
    const target = attentionTargetLabel(event)
    const title = waiting
      ? t("attention.notify.waitingTitle")
      : t("attention.notify.completedTitle")
    const summary = event.summary && prefs.state.notificationPreview ? event.summary : ""
    const body = waiting
      ? summary
        ? t("attention.notify.waitingBodySummary", { target, summary })
        : t("attention.notify.waitingBody", { target })
      : summary
        ? t("attention.notify.completedBodySummary", { target, summary })
        : t("attention.notify.completedBody", { target })
    return { title, body }
  }

  function notifyBrowser(event) {
    if (
      desktopSurface ||
      !prefs.state.systemNotifications ||
      !window.isSecureContext ||
      typeof Notification === "undefined" ||
      Notification.permission !== "granted"
    ) {
      return
    }
    const enabled =
      (event.kind === "waiting-input" && prefs.state.notifyWaiting) ||
      (event.kind === "completed" && prefs.state.notifyCompletion)
    if (!enabled) return

    const { title, body } = notificationContent(event)
    const notification = new Notification(title, { body })
    notification.onclick = () => {
      window.focus()
      navigateToAttention(event)
      notification.close()
    }
  }

  function requestDesktopAttention() {
    const api = window.pywebview?.api
    if (!desktopSurface || !prefs.state.desktopAttention || !api?.request_desktop_attention) return
    if (desktopCapabilities === null) {
      pendingDesktopAttention = true
      return
    }
    if (desktopCapabilities.nativeAttention !== true) return
    void api.request_desktop_attention().catch(() => {})
  }

  function onEdge(event) {
    const inactive = document.hidden || !document.hasFocus()
    if (inactive) {
      requestDesktopAttention()
      notifyBrowser(event)
    }
    playSound(event.kind)
  }

  function updateFavicon() {
    if (favicons.length === 0) return
    const summary = totalAttention()
    const showBadge = prefs.state.faviconBadge && (summary.pending > 0 || summary.completed > 0)
    const label =
      summary.pending > 0 ? "!" : summary.completed > 9 ? "9+" : String(summary.completed)
    const badgeUrl = showBadge
      ? faviconDataUrl(label, summary.pending > 0 ? "#dc2626" : "#2563eb")
      : null
    favicons.forEach((favicon, index) => {
      favicon.href = badgeUrl ?? originalFavicons[index]
    })
  }

  onMounted(() => {
    favicons = [...document.querySelectorAll('link[rel~="icon"]')]
    originalFavicons = favicons.map((favicon) => favicon.getAttribute("href"))
    unsubscribeEdges = subscribeAttentionEdges(onEdge)
    unsubscribeAttention = subscribeAttention(updateFavicon)
    document.addEventListener("pointerdown", unlockAudio, { passive: true })
    document.addEventListener("keydown", unlockAudio)
    document.addEventListener(AUDIO_UNLOCK_EVENT, unlockAudio)
    window.addEventListener("pywebviewready", detectDesktopSurface)
    void detectDesktopSurface()
    updateFavicon()
  })

  onBeforeUnmount(() => {
    unsubscribeEdges?.()
    unsubscribeAttention?.()
    document.removeEventListener("pointerdown", unlockAudio)
    document.removeEventListener("keydown", unlockAudio)
    document.removeEventListener(AUDIO_UNLOCK_EVENT, unlockAudio)
    window.removeEventListener("pywebviewready", detectDesktopSurface)
    favicons.forEach((favicon, index) => {
      const original = originalFavicons[index]
      if (original !== null) favicon.href = original
    })
    void audioContext?.close?.().catch(() => {})
  })

  watch(() => prefs.state.faviconBadge, updateFavicon)
}

function faviconDataUrl(label, color) {
  const escaped = label.replace(/[&<>"']/g, "")
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="8" fill="#1f2937"/><circle cx="23" cy="9" r="9" fill="${color}"/><text x="23" y="13" text-anchor="middle" font-family="sans-serif" font-size="11" font-weight="700" fill="white">${escaped}</text><text x="9" y="23" text-anchor="middle" font-family="sans-serif" font-size="18" fill="white">K</text></svg>`
  return `data:image/svg+xml,${encodeURIComponent(svg)}`
}
