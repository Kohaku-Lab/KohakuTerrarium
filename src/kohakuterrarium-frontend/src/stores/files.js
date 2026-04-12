/**
 * Files store — derives the touched-files set from the chat event
 * stream. A file is "touched" by the agent when a `read`, `write`,
 * `edit`, or `bash` tool completes and its args reference a path.
 *
 * Phase 6 scope: read the data the chat store already collects; expose
 * read/wrote/errored counts and a recency-sorted list. No WS of our
 * own, no server polling.
 */

import { defineStore } from "pinia"
import { computed } from "vue"

import { useChatStore } from "@/stores/chat"

const READ_TOOLS = new Set(["read"])
const WRITE_TOOLS = new Set(["write", "edit"])
const EXEC_TOOLS = new Set(["bash"])

/**
 * Best-effort path extraction from a tool call's args. Supports the
 * common shapes the builtin tools emit.
 */
function _extractPath(tc) {
  const args = tc?.args || tc?.arguments || {}
  if (typeof args === "string") {
    try {
      const parsed = JSON.parse(args)
      return parsed.file_path || parsed.path || parsed.filename || ""
    } catch {
      return ""
    }
  }
  return args.file_path || args.path || args.filename || ""
}

export const useFilesStore = defineStore("files", () => {
  const chat = useChatStore()

  /**
   * Flatten every tool_call across every tab into a touched list.
   * Each entry: `{path, action, tool, status, ts, round}`.
   */
  const touched = computed(() => {
    const entries = []
    const tabs = chat.messagesByTab || {}
    for (const [_tabKey, msgs] of Object.entries(tabs)) {
      for (const msg of msgs) {
        const toolCalls = msg.tool_calls
        if (!toolCalls) continue
        for (const tc of toolCalls) {
          if (!tc?.name) continue
          const path = _extractPath(tc)
          if (!path) continue
          let action = null
          if (READ_TOOLS.has(tc.name)) action = "read"
          else if (WRITE_TOOLS.has(tc.name)) action = "wrote"
          else if (EXEC_TOOLS.has(tc.name) && String(path).includes("/")) action = "exec"
          if (!action) continue
          if (tc.status === "error" || tc.error) action = "errored"
          entries.push({
            path,
            action,
            tool: tc.name,
            status: tc.status || "unknown",
            ts: msg.timestamp || "",
            round: msg.round || 0,
            turn: msg.id || "",
          })
        }
      }
    }
    return entries
  })

  /** Unique path → latest action map (for tree badges). */
  const latestActionByPath = computed(() => {
    const out = {}
    for (const e of touched.value) {
      out[e.path] = e.action
    }
    return out
  })

  /** Grouped by action, sorted by recency (newest first). */
  const grouped = computed(() => {
    const groups = { wrote: [], read: [], exec: [], errored: [] }
    for (const e of touched.value) {
      if (groups[e.action]) groups[e.action].push(e)
    }
    for (const k of Object.keys(groups)) {
      groups[k].sort((a, b) => String(b.ts).localeCompare(String(a.ts)))
    }
    return groups
  })

  return {
    touched,
    latestActionByPath,
    grouped,
  }
})
