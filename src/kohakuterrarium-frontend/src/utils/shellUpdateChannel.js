import { appUpdateAPI } from "@/utils/appUpdateApi"

/** The shell channel that goes with a runtime channel: nightly follows nightly, everything else is stable. */
export function shellChannelFor(runtimeChannel) {
  return runtimeChannel === "nightly" ? "nightly" : "stable"
}

/**
 * Make the shell's update channel match the runtime's, so one choice in Settings > Updates decides
 * both. `shell` is the shell-settings handle (`{ settings, patch }`); failures leave it as it was.
 */
export async function followRuntimeChannel(shell) {
  try {
    const wanted = shellChannelFor((await appUpdateAPI.getSettings())?.channel)
    if (shell.settings.value?.updateChannel !== wanted) await shell.patch({ updateChannel: wanted })
  } catch {
    // The runtime channel is unknown (not a launcher install): keep the shell's own.
  }
}
