import { onBeforeUnmount, onMounted, ref, watch } from "vue"

import { useShellSettings } from "@/composables/useShellSettings"
import { attentionOverview, subscribeAttention } from "@/stores/attention"
import { hostKind, hostSupports, setKeepAwake } from "@/utils/hostBridge"

/** How long the computer stays awake after the last turn ended. */
export const HOLD_OFF_MS = 20000

/** Whether the shell is keeping the computer awake right now. */
export const keepingAwake = ref(false)

/**
 * Whether work should hold the computer awake: `mode` is the user's choice, `running` the number
 * of turns in progress and `charging` whether the computer is on mains power (``null`` when unknown).
 */
export function shouldStayAwake(mode, running, charging) {
  if (mode === "never" || running <= 0) return false
  return mode !== "running-on-ac" || charging !== false
}

/** Keep the computer awake while agents work, as the keep-awake setting allows. */
export function useKeepAwake() {
  const shell = useShellSettings()
  let battery = null
  let release = null
  let stopAttention = () => {}

  const mode = () => shell.settings.value?.keepAwake ?? "running"
  const wanted = () =>
    shouldStayAwake(mode(), attentionOverview().running, battery ? battery.charging : null)

  async function hold() {
    keepingAwake.value = (await setKeepAwake(true)) === true
  }

  async function drop() {
    await setKeepAwake(false)
    keepingAwake.value = false
  }

  function apply() {
    if (wanted()) {
      clearTimeout(release)
      release = null
      if (!keepingAwake.value) void hold()
    } else if (keepingAwake.value && release === null) {
      release = setTimeout(() => {
        release = null
        if (!wanted()) void drop()
      }, HOLD_OFF_MS)
    }
  }

  onMounted(async () => {
    if (hostKind() !== "tauri" || !hostSupports("set_keep_awake")) return
    battery = (await navigator.getBattery?.().catch(() => null)) ?? null
    battery?.addEventListener("chargingchange", apply)
    stopAttention = subscribeAttention(apply)
    apply()
  })
  watch(() => shell.settings.value?.keepAwake, apply)
  onBeforeUnmount(() => {
    stopAttention()
    battery?.removeEventListener("chargingchange", apply)
    clearTimeout(release)
    if (keepingAwake.value) void drop()
  })
}
