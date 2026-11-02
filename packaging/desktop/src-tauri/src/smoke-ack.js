(async () => {
  const invoke = window.__TAURI_INTERNALS__?.invoke
  if (!invoke || !document.querySelector("#app[data-v-app] > *")) return
  const authenticationProbe = "/api/settings/ui-prefs"
  try {
    const response = await fetch(authenticationProbe, { credentials: "same-origin", cache: "no-store" })
    if (response.ok) await invoke("smoke_ack")
  } catch {}
})()
