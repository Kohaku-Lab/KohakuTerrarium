import { computed, onScopeDispose, watch } from "vue"
import { useChatStore } from "@/stores/chat"
import { useHostsStore } from "@/stores/hosts"
import { relayWorkContext } from "@/utils/hostBridge"
import { useWorkContext } from "./workContext"

export function workHostKey(hosts = useHostsStore()) {
  return `${hosts.activeHostId || ""}|${hosts.activeHost?.currentUser?.id ?? ""}`
}

export function usePopoutWorkFocus(sessionId, label) {
  const hosts = useHostsStore()
  const chat = useChatStore(sessionId)
  const work = useWorkContext({
    target: computed(() => ({
      sessionId,
      label: label.value,
      creatureId: chat.terrariumTarget || "",
    })),
    includeSpeech: true,
  })
  let disposed = false,
    pending = false,
    queued = null
  const send = (selected = false, closed = false) => {
    const packet = JSON.parse(
      JSON.stringify({ host: workHostKey(hosts), selected, closed, work: work.value }),
    )
    // Streaming replaces the pending observation; never queue one IPC per text token.
    queued = { ...packet, selected: packet.selected || queued?.selected || false }
    if (!pending) void flush()
  }
  async function flush() {
    pending = true
    while (queued) {
      const packet = queued
      queued = null
      if (!disposed || packet.closed) await relayWorkContext(packet)
    }
    pending = false
  }
  const focus = () => send(true)
  window.addEventListener("focus", focus)
  const stopSelection = watch(
    () => chat.terrariumTarget,
    () => send(document.hasFocus()),
    { flush: "post" },
  )
  const stopWork = watch(work, () => send(document.hasFocus()), { immediate: true, flush: "post" })
  onScopeDispose(() => {
    disposed = true
    stopSelection()
    stopWork()
    window.removeEventListener("focus", focus)
    send(false, true)
  })
}
