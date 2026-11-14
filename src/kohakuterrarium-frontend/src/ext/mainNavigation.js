import { openChat, openTab } from "./sdk"

export function startMainNavigation() {
  const receive = ({ detail }) => {
    if (detail?.kind === "attach") void openChat(detail).catch(() => {})
    else if (detail?.kind === "settings") openTab(detail)
    else if (detail?.kind === "extension" && typeof detail.id === "string") openTab(detail.id)
  }
  window.addEventListener("kt:navigate-main", receive)
  return () => window.removeEventListener("kt:navigate-main", receive)
}
