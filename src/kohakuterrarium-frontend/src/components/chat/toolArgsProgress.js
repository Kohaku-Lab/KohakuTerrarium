/** Status-line text for a tool call whose arguments are still streaming. */

import { formatBytes } from "@/utils/chatAttachments"

/** ``"Writing app… 12.5 KB"`` for ``show_app``, ``"Writing <tool> arguments… …"`` otherwise; null without progress. */
export function toolArgsProgressLabel(progress, t) {
  if (!progress?.name) return null
  const size = formatBytes(progress.chars)
  return progress.name === "show_app"
    ? t("chat.writingApp", { size })
    : t("chat.writingTool", { tool: progress.name, size })
}
