let installed = null

export function createNotificationSurface({ document, maxVisible = 5, setTimer = setTimeout, clearTimer = clearTimeout }) {
  const root = document.createElement('section')
  root.className = 'kt-notifications'
  root.setAttribute('aria-label', 'Notifications')
  document.body.append(root)
  const entries = new Set()
  let disposed = false
  const clear = () => {
    for (const entry of [...entries]) entry.close()
  }

  function show(value) {
    if (disposed) return { close() {} }
    const options = typeof value === 'string' ? { message: value } : value || {}
    const type = ['info', 'success', 'warning', 'error'].includes(options.type) ? options.type : 'info'
    const duration = Number.isFinite(options.duration) && options.duration >= 0 ? Math.min(options.duration, 60_000) : 4000
    const node = document.createElement('div')
    node.className = `kt-notification kt-notification-${type}`
    node.setAttribute('role', ['warning', 'error'].includes(type) ? 'alert' : 'status')
    node.setAttribute('aria-live', ['warning', 'error'].includes(type) ? 'assertive' : 'polite')
    node.setAttribute('aria-atomic', 'true')
    const text = document.createElement('span')
    text.textContent = `${type[0].toUpperCase()}${type.slice(1)}: ${String(options.message ?? '')}`
    const button = document.createElement('button')
    button.type = 'button'
    button.setAttribute('aria-label', 'Dismiss notification')
    button.textContent = '×'
    node.append(text, button)
    let timer = null
    let hovering = false
    const previousFocus = document.activeElement
    const pause = () => {
      if (timer !== null) clearTimer(timer)
      timer = null
    }
    const entry = {
      close() {
        if (!entries.delete(entry)) return
        const restoreFocus = node.contains(document.activeElement)
        pause()
        node.remove()
        if (restoreFocus && previousFocus?.isConnected) previousFocus.focus()
      },
    }
    const resume = () => {
      pause()
      if (duration > 0 && !hovering && !node.contains(document.activeElement) && entries.has(entry)) timer = setTimer(entry.close, duration)
    }
    button.addEventListener('click', entry.close)
    node.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        entry.close()
      }
    })
    node.addEventListener('mouseenter', () => {
      hovering = true
      pause()
    })
    node.addEventListener('mouseleave', () => {
      hovering = false
      resume()
    })
    node.addEventListener('focusin', pause)
    node.addEventListener('focusout', () => queueMicrotask(resume))
    entries.add(entry)
    root.append(node)
    while (entries.size > maxVisible) entries.values().next().value.close()
    resume()
    return entry
  }

  return {
    show,
    clear,
    dispose() {
      disposed = true
      clear()
      root.remove()
    },
  }
}

export function installNotificationSurface(document) {
  installed?.dispose()
  const surface = createNotificationSurface({ document })
  installed = surface
  return {
    clear: surface.clear,
    dispose() {
      surface.dispose()
      if (installed === surface) installed = null
    },
  }
}

export function showNotification(options) {
  return installed?.show(options) || { close() {} }
}
