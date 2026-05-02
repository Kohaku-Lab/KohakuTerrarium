import { ElMessage } from "element-plus"

import { terrariumAPI, agentAPI } from "@/utils/api"
import { createVisibilityInterval } from "@/composables/useVisibilityInterval"
import { useMessagesStore } from "@/stores/messages"
import { useInstancesStore } from "@/stores/instances"
import { useStatusStore } from "@/stores/status"
import { getHybridPrefSync, setHybridPref } from "@/utils/uiPrefs"
import { wsUrl } from "@/utils/wsUrl"

const BRANCH_RESYNC_DELAY_MS = 350

function normalizeContentParts(content) {
  if (!Array.isArray(content)) return null
  return content.filter(
    (part) =>
      part &&
      typeof part === "object" &&
      typeof part.type === "string" &&
      ["text", "image_url", "file"].includes(part.type),
  )
}

function extractTextContent(content) {
  if (typeof content === "string") return content
  if (!Array.isArray(content)) return ""
  return content
    .filter((part) => part?.type === "text")
    .map((part) => part.text || "")
    .join("\n")
}

function normalizeMessageContent(content) {
  const contentParts = normalizeContentParts(content)
  return {
    content: typeof content === "string" ? content : extractTextContent(content),
    contentParts,
  }
}

function contentSignature(content) {
  if (typeof content === "string") return `text:${content}`
  const normalized = normalizeContentParts(content)
  if (!normalized) return ""
  return JSON.stringify(normalized)
}

function toolResultPayload(result, data = {}) {
  return {
    result,
    resultParts: normalizeContentParts(result),
    resultMeta: data.result_meta || data.output_meta || data.metadata || null,
  }
}

/**
 * Convert OpenAI-format conversation history to frontend messages.
 */
export function _convertHistory(messages) {
  const result = []
  const toolResults = {}
  for (const msg of messages) {
    if (msg.role === "tool") toolResults[msg.tool_call_id] = msg.content
  }
  for (const msg of messages) {
    if (msg.role === "system" || msg.role === "tool") continue
    if (msg.role === "user") {
      const normalized = normalizeMessageContent(msg.content)
      result.push({
        id: "h_" + result.length,
        role: "user",
        content: normalized.content,
        contentParts: normalized.contentParts,
        timestamp: "",
      })
    } else if (msg.role === "assistant") {
      const tcs = (msg.tool_calls || []).map((tc) => ({
        id: tc.id,
        name: tc.function?.name || "unknown",
        kind: (tc.function?.name || "").startsWith("agent_") ? "subagent" : "tool",
        args: _parseArgs(tc.function?.arguments),
        status: "done",
        result: toolResults[tc.id] || "",
      }))
      const normalized = normalizeMessageContent(msg.content)
      result.push({
        id: "h_" + result.length,
        role: "assistant",
        content: normalized.content,
        contentParts: normalized.contentParts,
        timestamp: "",
        tool_calls: tcs.length ? tcs : undefined,
      })
    }
  }
  return result
}

/**
 * Replay ordered event list to reconstruct chat view.
 *
 * Returns { messages, pendingJobs } where pendingJobs is a map of
 * jobId -> { name, type, startedAt } for tools/sub-agents that started
 * but never received a done/error event.
 */
/**
 * Build per-turn branch metadata from the event stream.
 *
 * Returns { byTurn: Map(turn_index -> { branches: number[], latestBranch: number,
 * eventIdsByBranch: Map(branch_id -> number[]) }), liveIds: Set<number>,
 * branchSelection: Map(turn_index -> selected_branch_id) }.
 *
 * branchSelection defaults to the latest branch per turn; callers can
 * override it (e.g. when the user clicks <1/N>) and re-run the replay.
 */
/**
 * Path-aware branch metadata.
 *
 * Each event optionally carries ``parent_branch_path`` — a list of
 * ``[turn_index, branch_id]`` pairs naming the live branch of every
 * prior turn at the moment the event was recorded. The frontend uses
 * this to correctly hide follow-up turns when the user switches an
 * earlier turn to a sibling branch whose subtree never produced them.
 *
 * Events without an explicit path (legacy / migrated streams) get one
 * derived from their position: snapshot of the highest branch_id seen
 * for every prior turn before this event in event order.
 */
function _coercePath(raw) {
  if (!raw || !Array.isArray(raw)) return []
  const out = []
  for (const item of raw) {
    if (Array.isArray(item) && item.length === 2) {
      const [t, b] = item
      if (typeof t === "number" && typeof b === "number") out.push([t, b])
    }
  }
  return out
}

function _indexParentPaths(events) {
  const paths = new Map()
  const latestByTurn = new Map()
  for (const evt of events) {
    const ti = evt?.turn_index
    const bi = evt?.branch_id
    const eid = evt?.event_id
    const explicit = _coercePath(evt?.parent_branch_path)
    if (typeof eid === "number") {
      if (explicit.length) {
        paths.set(eid, explicit)
      } else if (typeof ti === "number") {
        const snap = []
        for (const [t, b] of latestByTurn) {
          if (t < ti) snap.push([t, b])
        }
        snap.sort((a, b) => a[0] - b[0])
        paths.set(eid, snap)
      }
    }
    if (typeof ti === "number" && typeof bi === "number") {
      const prev = latestByTurn.get(ti) || 0
      if (bi > prev) latestByTurn.set(ti, bi)
    }
  }
  return paths
}

function _pathMatches(path, selected) {
  for (const [t, b] of path) {
    if (selected.has(t) && selected.get(t) !== b) return false
  }
  return true
}

function _resolveSelectedBranches(events, parentPaths, branchView) {
  const branchesByTurn = new Map()
  for (const evt of events) {
    const ti = evt?.turn_index
    const bi = evt?.branch_id
    const eid = evt?.event_id
    if (typeof ti !== "number" || typeof bi !== "number") continue
    const path = typeof eid === "number" ? parentPaths.get(eid) || [] : []
    let bucket = branchesByTurn.get(ti)
    if (!bucket) {
      bucket = []
      branchesByTurn.set(ti, bucket)
    }
    if (!bucket.some((entry) => entry.branch === bi)) {
      bucket.push({ path, branch: bi })
    }
  }
  const selected = new Map()
  const turns = [...branchesByTurn.keys()].sort((a, b) => a - b)
  for (const ti of turns) {
    const candidates = branchesByTurn.get(ti).filter((entry) => _pathMatches(entry.path, selected))
    if (!candidates.length) continue
    if (branchView && Object.prototype.hasOwnProperty.call(branchView, ti)) {
      const requested = branchView[ti]
      const match = candidates.find((entry) => entry.branch === requested)
      if (match) {
        selected.set(ti, match.branch)
        continue
      }
    }
    selected.set(ti, Math.max(...candidates.map((entry) => entry.branch)))
  }
  return selected
}

export function _collectBranchMetadata(events, branchView = null) {
  const parentPaths = _indexParentPaths(events)
  const branchSelection = _resolveSelectedBranches(events, parentPaths, branchView)

  // Per-turn metadata, filtered to branches whose parent path is
  // consistent with the current selection of prior turns. The
  // navigator's <x/N> count reflects only siblings inside the live
  // subtree, not branches living under a different prior selection.
  const byTurn = new Map()
  for (const evt of events) {
    const ti = evt?.turn_index
    const bi = evt?.branch_id
    const eid = evt?.event_id
    if (typeof ti !== "number" || typeof bi !== "number") continue
    const path = typeof eid === "number" ? parentPaths.get(eid) || [] : []
    const priorSelected = new Map()
    for (const [t, b] of branchSelection) if (t < ti) priorSelected.set(t, b)
    if (!_pathMatches(path, priorSelected)) continue
    let bucket = byTurn.get(ti)
    if (!bucket) {
      bucket = { branches: [], latestBranch: 0, eventIdsByBranch: new Map() }
      byTurn.set(ti, bucket)
    }
    if (!bucket.eventIdsByBranch.has(bi)) {
      bucket.eventIdsByBranch.set(bi, [])
      bucket.branches.push(bi)
    }
    if (typeof eid === "number") bucket.eventIdsByBranch.get(bi).push(eid)
    if (bi > bucket.latestBranch) bucket.latestBranch = bi
  }
  for (const bucket of byTurn.values()) bucket.branches.sort((a, b) => a - b)

  const liveIds = new Set()
  for (const evt of events) {
    const eid = evt?.event_id
    const ti = evt?.turn_index
    const bi = evt?.branch_id
    if (typeof eid !== "number") continue
    if (typeof ti !== "number" || typeof bi !== "number") {
      liveIds.add(eid)
      continue
    }
    if (branchSelection.get(ti) !== bi) continue
    const path = parentPaths.get(eid) || []
    const priorSelected = new Map()
    for (const [t, b] of branchSelection) if (t < ti) priorSelected.set(t, b)
    if (!_pathMatches(path, priorSelected)) continue
    liveIds.add(eid)
  }
  return { byTurn, liveIds, branchSelection }
}

function _stableStringify(value) {
  if (Array.isArray(value)) return `[${value.map((item) => _stableStringify(item)).join(",")}]`
  if (value && typeof value === "object") {
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${_stableStringify(value[key])}`)
      .join(",")}}`
  }
  return JSON.stringify(value)
}

function _dedupeAdjacentDuplicateEvents(events) {
  const out = []
  let previous = null
  for (const evt of events || []) {
    const clone = { ...(evt || {}) }
    delete clone.event_id
    delete clone.ts
    const signature = _stableStringify(clone)
    if (signature === previous) continue
    out.push(evt)
    previous = signature
  }
  return out
}

export function _replayEvents(messages, events, branchView = null) {
  if (!events?.length) return { messages: _convertHistory(messages), pendingJobs: {} }

  events = _dedupeAdjacentDuplicateEvents(events)
  const { byTurn, liveIds, branchSelection } = _collectBranchMetadata(events, branchView)

  // Pre-pass: compact_replace ranges hide every event whose event_id
  // falls inside the replaced range. Mirrors Python replay_conversation
  // so resume + live show the same compact-summary bubble.
  const replacedIds = new Set()
  for (const evt of events) {
    if (evt?.type === "compact_replace") {
      const frm = evt.replaced_from_event_id
      const to = evt.replaced_to_event_id
      if (typeof frm === "number" && typeof to === "number") {
        for (let i = frm; i <= to; i++) replacedIds.add(i)
      }
    }
  }

  const result = []
  let cur = null
  let _n = 0
  // Dedupe user-role renders across user_input + user_message duplicates
  // for the same (turn, branch).
  const _seenUserRender = new Set()
  // Track job lifecycle: started jobs and completed jobs
  const startedJobs = {} // jobId -> tool part reference
  const completedJobs = new Set() // jobIds that received done/error

  function ensureCur() {
    if (!cur) {
      cur = {
        id: "h_" + result.length,
        role: "assistant",
        parts: [],
        timestamp: "",
      }
      result.push(cur)
    }
    return cur
  }

  function appendText(content) {
    const c = ensureCur()
    const tail = c.parts.length ? c.parts[c.parts.length - 1] : null
    if (tail && tail.type === "text") {
      tail.content += content
    } else {
      c.parts.push({ type: "text", content })
    }
  }

  function addTool(name, kind, args, jobId) {
    const c = ensureCur()
    const tail = c.parts.length ? c.parts[c.parts.length - 1] : null
    if (tail && tail.type === "text") tail._streaming = false
    const tool = {
      type: "tool",
      id: `tool_${_n++}`,
      jobId: jobId || "",
      name,
      kind,
      args: args || {},
      status: "done",
      result: "",
      tools_used: [],
      children: [],
    }
    c.parts.push(tool)
    if (jobId) startedJobs[jobId] = tool
    return tool
  }

  // Search ALL messages for a sub-agent part.
  // Same matching strategy as live _findSubagentPart.
  function findSubagent(saName, jobId) {
    // Pass 1: match by job_id (most reliable)
    if (jobId) {
      for (let i = result.length - 1; i >= 0; i--) {
        const msg = result[i]
        if (!msg.parts) continue
        for (let j = msg.parts.length - 1; j >= 0; j--) {
          const p = msg.parts[j]
          if (p.type === "tool" && p.kind === "subagent" && p.jobId === jobId) return p
        }
      }
    }
    // Pass 2: match by name (exact, startsWith, or includes)
    if (saName) {
      for (let i = result.length - 1; i >= 0; i--) {
        const msg = result[i]
        if (!msg.parts) continue
        for (let j = msg.parts.length - 1; j >= 0; j--) {
          const p = msg.parts[j]
          if (p.type !== "tool" || p.kind !== "subagent") continue
          if (p.name === saName || p.name.includes(saName) || saName.includes(p.name)) return p
        }
      }
    }
    // Pass 3: any sub-agent (last resort)
    for (let i = result.length - 1; i >= 0; i--) {
      const msg = result[i]
      if (!msg.parts) continue
      for (let j = msg.parts.length - 1; j >= 0; j--) {
        const p = msg.parts[j]
        if (p.type === "tool" && p.kind === "subagent") return p
      }
    }
    return null
  }

  function addSubagentTool(name, args, saName, saJobId) {
    const sa = findSubagent(saName, saJobId)
    if (sa) {
      const tool = {
        type: "tool",
        id: `tool_${_n++}`,
        name,
        kind: "tool",
        args: args || {},
        status: "done",
        result: "",
        tools_used: [],
      }
      if (!sa.children) sa.children = []
      sa.children.push(tool)
      return tool
    }
    return addTool(name, "tool", args)
  }

  function updateSubagentTool(name, result, opts, saName, saJobId) {
    const sa = findSubagent(saName, saJobId)
    if (sa?.children?.length) {
      const tc = [...sa.children].reverse().find((p) => p.name === name)
      if (tc) {
        const payload = toolResultPayload(result || "", opts || {})
        tc.result = payload.result
        tc.resultParts = payload.resultParts
        tc.resultMeta = payload.resultMeta
        if (opts?.error) tc.status = "error"
        return
      }
    }
    updateTool(name, result, opts)
  }

  function findToolByJobId(jobId) {
    for (let i = result.length - 1; i >= 0; i--) {
      const msg = result[i]
      if (!msg.parts) continue
      const tc = [...msg.parts].reverse().find((p) => p.type === "tool" && p.jobId === jobId)
      if (tc) return tc
    }
    return null
  }

  function updateTool(name, result, opts, jobId) {
    let tc = null
    if (jobId) {
      tc = findToolByJobId(jobId)
    }
    if (!tc && cur) {
      tc = [...cur.parts].reverse().find((p) => p.type === "tool" && p.name === name)
    }
    if (!tc) {
      // Search all messages as final fallback (name match, or partial match for sub-agents)
      for (let i = result.length - 1; i >= 0 && !tc; i--) {
        const msg = result[i]
        if (!msg.parts) continue
        tc = [...msg.parts]
          .reverse()
          .find(
            (p) =>
              p.type === "tool" &&
              (p.name === name || p.name.startsWith(name) || name.startsWith(p.name)) &&
              !p.result,
          )
      }
    }
    if (tc) {
      const payload = toolResultPayload(result || "", opts || {})
      tc.result = payload.result
      tc.resultParts = payload.resultParts
      tc.resultMeta = payload.resultMeta
      if (opts?.interrupted || opts?.finalState === "interrupted") tc.status = "interrupted"
      else if (opts?.error) tc.status = "error"
      if (opts?.tools_used) tc.tools_used = opts.tools_used
      if (opts?.turns != null) tc.turns = opts.turns
      if (opts?.duration != null) tc.duration = opts.duration
      if (opts?.total_tokens != null) tc.total_tokens = opts.total_tokens
      if (opts?.prompt_tokens != null) tc.prompt_tokens = opts.prompt_tokens
      if (opts?.completion_tokens != null) tc.completion_tokens = opts.completion_tokens
      // Track completion for pending-job detection
      if (tc.jobId) completedJobs.add(tc.jobId)
      if (jobId) completedJobs.add(jobId)
    }
  }

  function findCompactMessage(round, preferRunning = false) {
    for (let i = result.length - 1; i >= 0; i--) {
      const msg = result[i]
      if (msg.role !== "compact") continue
      if (round && msg.round === round) {
        if (!preferRunning || msg.status === "running") return msg
      }
    }
    if (!preferRunning) return null
    for (let i = result.length - 1; i >= 0; i--) {
      const msg = result[i]
      if (msg.role === "compact" && msg.status === "running") return msg
    }
    return null
  }

  function upsertCompactMessage(round, summary, status, messagesCompacted) {
    const existing =
      findCompactMessage(round, status === "done") || findCompactMessage(round, false)
    if (existing) {
      existing.round = round
      existing.summary = summary
      existing.status = status
      existing.messagesCompacted = messagesCompacted
      return existing
    }
    const compact = {
      id: "compact_" + result.length,
      role: "compact",
      round,
      summary,
      status,
      messagesCompacted,
      timestamp: "",
    }
    result.push(compact)
    return compact
  }

  for (const evt of events) {
    const t = evt.type

    // Skip events on a non-selected branch of their turn (siblings of
    // regen / edit+rerun stay on disk for the <1/N> navigator but
    // don't render in the default view).
    if (typeof evt.event_id === "number" && !liveIds.has(evt.event_id)) {
      continue
    }

    // Skip events covered by a compact_replace range (the summary
    // bubble emitted below replaces them).
    if (
      typeof evt.event_id === "number" &&
      replacedIds.has(evt.event_id) &&
      t !== "compact_replace"
    ) {
      continue
    }

    // ── Common types (both formats) ──
    if (t === "user_input" || t === "user_message") {
      // Live-agent flows emit BOTH user_input + user_message for every
      // user turn (the first carries the trigger, the second is the
      // state-bearing replay event). The migration-from-snapshot path
      // emits ONLY user_message. Render the first one we see for each
      // (turn, branch) pair; skip the second.
      const ti = evt?.turn_index
      const bi = evt?.branch_id
      const key = typeof ti === "number" && typeof bi === "number" ? `${ti}/${bi}` : null
      if (key && _seenUserRender.has(key)) continue
      if (key) _seenUserRender.add(key)
      cur = null
      const normalized = normalizeMessageContent(evt.content)
      result.push({
        id: "h_" + result.length,
        role: "user",
        content: normalized.content,
        contentParts: normalized.contentParts,
        timestamp: "",
      })
    } else if (t === "processing_start") {
      cur = {
        id: "h_" + result.length,
        role: "assistant",
        parts: [],
        timestamp: "",
      }
      result.push(cur)
    } else if (t === "text" || t === "text_chunk") {
      // text_chunk is the Wave C per-chunk streaming format; replay
      // collapses consecutive chunks into one assistant text part.
      appendText(evt.content || "")
    } else if (t === "processing_end" || t === "idle") {
      // Do NOT clear cur if sub-agents might still be adding tools to this message
      // But mark text as done
      if (cur) {
        for (const p of cur.parts) {
          if (p.type === "text") p._streaming = false
        }
      }
      cur = null

      // ── StreamOutput format (live WS): type="activity" wrapper ──
    } else if (t === "activity") {
      const at = evt.activity_type
      if (at === "trigger_fired") {
        cur = null
        const ch = evt.channel || ""
        const sender = evt.sender || ""
        result.push({
          id: "h_" + result.length,
          role: "trigger",
          content: ch ? `channel: ${ch}${sender ? ` from ${sender}` : ""}` : evt.name,
          triggerContent: evt.content || "",
          channel: ch,
          sender,
          timestamp: "",
        })
      } else if (at === "token_usage" || at === "processing_complete") {
        // skip
      } else if (at === "context_cleared") {
        cur = null
        result.push({
          id: "clear_" + result.length,
          role: "clear",
          messagesCleared: evt.messages_cleared || 0,
          timestamp: "",
        })
      } else if (at === "processing_error") {
        cur = null
        result.push({
          id: "err_" + result.length,
          role: "error",
          errorType: evt.error_type || "Error",
          content: evt.error || evt.detail || "Unknown error",
          timestamp: "",
        })
      } else if (at === "subagent_start") {
        addTool(evt.name, "subagent", evt.args || { info: evt.detail }, evt.job_id)
      } else if (at === "subagent_done") {
        updateTool(
          evt.name,
          evt.result || evt.detail,
          {
            tools_used: evt.tools_used,
            turns: evt.turns,
            duration: evt.duration,
            total_tokens: evt.total_tokens,
            prompt_tokens: evt.prompt_tokens,
            completion_tokens: evt.completion_tokens,
          },
          evt.job_id,
        )
      } else if (at === "subagent_error") {
        updateTool(
          evt.name,
          evt.result || evt.error || evt.detail,
          {
            error: true,
            interrupted: !!evt.interrupted,
            finalState: evt.final_state,
            tools_used: evt.tools_used,
            turns: evt.turns,
            duration: evt.duration,
            total_tokens: evt.total_tokens,
            prompt_tokens: evt.prompt_tokens,
            completion_tokens: evt.completion_tokens,
          },
          evt.job_id,
        )
      } else if (at === "tool_start") {
        addTool(evt.name, "tool", evt.args || { info: evt.detail }, evt.job_id)
      } else if (at === "tool_done") {
        updateTool(
          evt.name,
          evt.result || evt.output || evt.detail,
          { tools_used: evt.tools_used },
          evt.job_id,
        )
      } else if (at === "tool_error") {
        updateTool(
          evt.name,
          evt.result || evt.error || evt.detail,
          {
            error: true,
            interrupted: !!evt.interrupted,
            finalState: evt.final_state,
          },
          evt.job_id,
        )
      } else if (at?.startsWith("subagent_tool_")) {
        const subAct = at.replace("subagent_", "")
        const toolName = evt.tool || evt.name || ""
        const saName = evt.subagent || ""
        const saJobId = evt.job_id || ""
        if (subAct === "tool_start") {
          addSubagentTool(toolName, { info: evt.detail || "" }, saName, saJobId)
        } else if (subAct === "tool_done") {
          updateSubagentTool(toolName, evt.detail || "", null, saName, saJobId)
        } else if (subAct === "tool_error") {
          updateSubagentTool(toolName, evt.detail || "", { error: true }, saName, saJobId)
        }
      }

      // ── SessionStore format (persistent): direct type names ──
    } else if (t === "trigger_fired") {
      cur = null
      const ch = evt.channel || ""
      const sender = evt.sender || ""
      result.push({
        id: "h_" + result.length,
        role: "trigger",
        content: ch ? `channel: ${ch}${sender ? ` from ${sender}` : ""}` : "",
        triggerContent: evt.content || "",
        channel: ch,
        sender,
        timestamp: "",
      })
    } else if (t === "tool_call") {
      addTool(evt.name, "tool", evt.args || {}, evt.call_id || evt.job_id)
    } else if (t === "tool_result") {
      updateTool(
        evt.name,
        evt.output || evt.error || "",
        {
          error: evt.error ? true : false,
          interrupted: !!evt.interrupted,
          finalState: evt.final_state,
          output_meta: evt.output_meta,
          result_meta: evt.result_meta,
          metadata: evt.metadata,
        },
        evt.call_id || evt.job_id,
      )
    } else if (t === "subagent_call") {
      addTool(evt.name, "subagent", { task: evt.task || "" }, evt.job_id)
    } else if (t === "subagent_result") {
      updateTool(
        evt.name,
        evt.output || evt.error || "",
        {
          error: evt.error ? true : false,
          interrupted: !!evt.interrupted,
          finalState: evt.final_state,
          tools_used: evt.tools_used,
          turns: evt.turns,
          duration: evt.duration,
          total_tokens: evt.total_tokens,
          prompt_tokens: evt.prompt_tokens,
          completion_tokens: evt.completion_tokens,
        },
        evt.job_id,
      )
    } else if (t === "subagent_tool") {
      const toolName = evt.tool_name || ""
      const saName = evt.subagent || ""
      const saJobId = evt.job_id || ""
      if (evt.activity === "tool_start") {
        addSubagentTool(toolName, { info: evt.detail || "" }, saName, saJobId)
      } else if (evt.activity === "tool_done") {
        updateSubagentTool(toolName, evt.detail || "", null, saName, saJobId)
      } else if (evt.activity === "tool_error") {
        updateSubagentTool(toolName, evt.detail || "", { error: true }, saName, saJobId)
      }
    } else if (t === "channel_message") {
      const normalized = normalizeMessageContent(evt.content)
      result.push({
        id: "ch_" + result.length,
        role: "channel",
        sender: evt.sender || "",
        content: normalized.content,
        contentParts: normalized.contentParts,
        timestamp: "",
      })
    } else if (t === "compact_summary" || t === "compact_complete") {
      cur = null
      upsertCompactMessage(
        evt.compact_round || evt.round || 0,
        evt.summary || "",
        "done",
        evt.messages_compacted || 0,
      )
    } else if (t === "compact_replace") {
      // Wave C state-bearing event. Used by replay_conversation and
      // by v1→v2 migration to mark the boundary where pre-compact
      // history was replaced with a summary. Render as a compact
      // bubble — NOT as a plain assistant message.
      cur = null
      upsertCompactMessage(
        evt.round || 0,
        evt.summary_text || evt.summary || "",
        "done",
        evt.messages_compacted || 0,
      )
    } else if (t === "compact_start") {
      cur = null
      upsertCompactMessage(evt.compact_round || evt.round || 0, "", "running", 0)
    } else if (t === "processing_error") {
      cur = null
      result.push({
        id: "err_" + result.length,
        role: "error",
        errorType: evt.error_type || "Error",
        content: evt.error || "",
        timestamp: "",
      })
    } else if (t === "context_cleared") {
      cur = null
      result.push({
        id: "clear_" + result.length,
        role: "clear",
        messagesCleared: evt.messages_cleared || 0,
        timestamp: "",
      })
    } else if (t === "assistant_image") {
      // Replay the image into the current assistant message so resumed
      // sessions (and plain history reloads) show it in place. Mirrors
      // the live `_handleAssistantImage` path so shape + meta match.
      const c = ensureCur()
      for (const p of c.parts || []) {
        if (p.type === "text") p._streaming = false
      }
      c.parts.push({
        type: "image_url",
        image_url: {
          url: evt.url,
          detail: evt.detail || "auto",
        },
        meta: {
          source_type: evt.source_type,
          source_name: evt.source_name,
          revised_prompt: evt.revised_prompt,
        },
      })
    } else if (t === "token_usage" || t === "processing_complete") {
      // skip
    }
  }

  // Determine which jobs are still pending (started but no done/error).
  // Mark them as "running" so live WS events can update them.
  const pendingJobs = {}
  for (const [jobId, toolPart] of Object.entries(startedJobs)) {
    if (!completedJobs.has(jobId)) {
      toolPart.status = "running"
      toolPart.startedAt = Date.now() // approximate
      pendingJobs[jobId] = {
        name: toolPart.name,
        type: toolPart.kind === "subagent" ? "subagent" : "tool",
        startedAt: Date.now(),
      }
      if (toolPart.children) {
        for (const child of toolPart.children) {
          if (child.status === "done" && !child.result) {
            child.status = "running"
          }
        }
      }
    }
  }

  // Only mark sub-agents as interrupted if they have NO job_id tracking
  // (legacy events without job_id) AND have no result
  for (const msg of result) {
    for (const part of msg.parts || []) {
      if (
        part.type === "tool" &&
        part.kind === "subagent" &&
        part.status === "done" &&
        !part.result &&
        !part.jobId
      ) {
        part.status = "interrupted"
      }
    }
  }

  // Clean up empty parts
  for (const msg of result) {
    if (msg.parts?.length === 0) delete msg.parts
  }

  // Branch navigator placement is determined per turn by *content*
  // grouping of the available branches:
  //
  //   - All branches share the SAME user_message content
  //     → regen-style branching. Navigator on the assistant bubble.
  //   - Branches have DIFFERENT user_message content
  //     → edit-style branching. Navigator on the user bubble.
  //   - Mixed (some same, some different)
  //     → both navigators surface independently: a user-level one
  //       between user contents, plus an assistant-level one between
  //       regens of the currently-selected user content.
  //
  // This mirrors the user's mental model: regen produces an assistant
  // alternative, edit produces a user alternative; placing the
  // chevrons anywhere else creates phantom navigators.
  const userContentByBranch = new Map() // ti -> Map(branch_id -> content)
  for (const evt of events) {
    if (evt?.type !== "user_message" && evt?.type !== "user_input") continue
    const ti = evt.turn_index
    const bi = evt.branch_id
    if (typeof ti !== "number" || typeof bi !== "number") continue
    let perTurn = userContentByBranch.get(ti)
    if (!perTurn) {
      perTurn = new Map()
      userContentByBranch.set(ti, perTurn)
    }
    if (!perTurn.has(bi)) {
      const c = evt.content
      perTurn.set(bi, typeof c === "string" ? c : JSON.stringify(c ?? ""))
    }
  }

  function _userGroupsForTurn(ti) {
    const info = byTurn.get(ti)
    if (!info) return null
    const contents = userContentByBranch.get(ti) || new Map()
    const groups = []
    for (const branch of info.branches) {
      const content = contents.get(branch) ?? ""
      const existing = groups.find((g) => g.content === content)
      if (existing) existing.branches.push(branch)
      else groups.push({ content, branches: [branch] })
    }
    return groups
  }

  // Walk events grouped to result messages by turn so each rendered
  // user / assistant bubble can be tagged with the right turn_index.
  // Dedup user_input + user_message — the renderer only emits one
  // role=user message per (turn, branch).
  const userTurnsForResult = []
  const assistantTurnsForResult = []
  const seenUserKey = new Set()
  for (const evt of events) {
    const eid = evt?.event_id
    if (typeof eid === "number" && !liveIds.has(eid)) continue
    const ti = evt?.turn_index
    const bi = evt?.branch_id
    if (typeof ti !== "number") continue
    if (evt.type === "user_input" || evt.type === "user_message") {
      const key = `${ti}/${bi}`
      if (seenUserKey.has(key)) continue
      seenUserKey.add(key)
      userTurnsForResult.push(ti)
    } else if (evt.type === "processing_end") {
      assistantTurnsForResult.push(ti)
    }
  }

  function _attachUserNav(msg, ti) {
    const groups = _userGroupsForTurn(ti)
    if (!groups || groups.length <= 1) return
    const sel = branchSelection.get(ti)
    let groupIdx = groups.findIndex((g) => g.branches.includes(sel))
    if (groupIdx < 0) groupIdx = 0
    msg.turnIndex = ti
    msg.branchAnchor = "user"
    msg.userGroupCount = groups.length
    msg.currentUserGroupIdx = groupIdx
    // Branch ids representing each group (we pick the highest in each
    // group as the "default" target when chevroning across groups so
    // switching lands on the latest regen of the destination edit).
    msg.userGroupBranches = groups.map((g) => Math.max(...g.branches))
    msg.branches = [...(byTurn.get(ti)?.branches || [])]
    msg.currentBranch = sel
    msg.latestBranch = byTurn.get(ti)?.latestBranch
  }

  function _attachAssistantNav(msg, ti) {
    const groups = _userGroupsForTurn(ti)
    if (!groups) return
    const sel = branchSelection.get(ti)
    const group = groups.find((g) => g.branches.includes(sel)) || groups[0]
    if (!group || group.branches.length <= 1) return
    msg.turnIndex = ti
    msg.branchAnchor = "assistant"
    msg.assistantBranchCount = group.branches.length
    msg.currentAssistantIdx = group.branches.indexOf(sel)
    msg.assistantBranches = [...group.branches]
    msg.branches = [...group.branches]
    msg.currentBranch = sel
    msg.latestBranch = Math.max(...group.branches)
  }

  let userMsgIdx = 0
  let assistantMsgIdx = 0
  for (const msg of result) {
    if (msg.role === "user") {
      const ti = userTurnsForResult[userMsgIdx]
      userMsgIdx += 1
      if (typeof ti === "number") _attachUserNav(msg, ti)
    } else if (msg.role === "assistant") {
      const ti = assistantTurnsForResult[assistantMsgIdx]
      assistantMsgIdx += 1
      if (typeof ti === "number") _attachAssistantNav(msg, ti)
    }
  }

  return { messages: result, pendingJobs, branchMeta: { byTurn, branchSelection } }
}

function _parseArgs(args) {
  if (!args) return {}
  if (typeof args === "string") {
    try {
      return JSON.parse(args)
    } catch {
      return { raw: args }
    }
  }
  return args
}

export const useChatStore = defineStore("chat", {
  state: () => ({
    /** @type {Object<string, import('@/utils/api').ChatMessage[]>} */
    messagesByTab: {},
    /** @type {string | null} */
    activeTab: null,
    /** @type {string[]} */
    tabs: [],
    /**
     * Per-tab processing flag. ``processingByTab[tab]`` is true while
     * that specific creature/root is mid-stream. Replaces the previous
     * global ``processing`` boolean — which made tab A's interrupt
     * button appear on tab B and dropped the indicator on every tab
     * switch until the next chunk arrived. UI components should read
     * the ``processing`` getter (active-tab short-cut) or probe
     * ``processingByTab[tab]`` directly when they care about a
     * specific creature.
     * @type {Object<string, boolean>}
     */
    processingByTab: {},
    /** @type {Object<string, {prompt: number, completion: number, total: number, cached: number}>} Per-source token usage */
    tokenUsage: {},
    /** @type {Object<string, {name: string, type: string, startedAt: number}>} Running background jobs */
    runningJobs: {},
    /** @type {Object<string, number>} Unread message counts per tab */
    unreadCounts: {},
    /**
     * Per-tab raw event log cached from the last ``getHistory`` so
     * branch navigation can re-replay without a network round-trip.
     * @type {Object<string, any[]>}
     */
    eventsByTab: {},
    /**
     * Per-tab branch selection: ``{turnIndex: branchId}``. Empty
     * means "use latest branch for every turn" (the default).
     * @type {Object<string, Object<number, number>>}
     */
    branchViewByTab: {},
    /** @type {{sessionId: string, model: string, llmName: string, agentName: string, compactThreshold: number}} Session metadata */
    sessionInfo: {
      sessionId: "",
      model: "",
      llmName: "",
      agentName: "",
      compactThreshold: 0,
    },
    /** Reactive tick counter - incremented every second when jobs are running */
    _jobTick: 0,
    /** @type {number | null} */
    _jobTimer: null,
    /** @type {string | null} */
    _instanceId: null,
    /** @type {string | null} */
    _instanceType: null,
    /** @type {WebSocket | null} Single WS for the instance */
    _ws: null,
    /** @type {number | null} Pending reconnect timer id */
    _reconnectTimer: null,
    /** @type {number} Current reconnect delay (exponential backoff) */
    _reconnectDelay: 500,
    /** Connection status for the single instance WS. Used by the UI to
     *  show "reconnecting" banners. "open" | "reconnecting" | "closed" */
    wsStatus: "closed",
    /** @type {Array<{id: string, content: string, timestamp: string}>} Messages queued while agent is processing */
    queuedMessages: [],
    /** @type {number} Monotonic token to ignore stale history/WS callbacks after instance switches */
    _instanceGeneration: 0,
    /** @type {Record<string, number>} Recent user message signatures for cross-tab dedupe */
    _recentUserInputs: {},
    /** @type {Record<string, {active: boolean, expectedBranchByTurn?: Record<string, number>}>} Tabs needing canonical replay after regen/edit streaming finishes */
    _branchResyncPendingByTab: {},
    /** @type {Record<string, number>} Debounce timers for post-branch history resync */
    _branchResyncTimers: {},
  }),

  getters: {
    currentMessages: (state) => {
      if (!state.activeTab) return []
      return state.messagesByTab[state.activeTab] || []
    },
    hasRunningJobs: (state) => Object.keys(state.runningJobs).length > 0,
    /**
     * Back-compat shim — true when the active tab is processing. Most
     * UI code that used to read ``chat.processing`` actually wanted
     * "is the tab I'm looking at streaming?", which is exactly this.
     * Escape key, interrupt-button visibility, processing banner all
     * pull from here. Components that need a specific tab's state
     * (e.g. unread-badge logic) should read
     * ``chat.processingByTab[tab]`` directly.
     */
    processing: (state) => (state.activeTab ? !!state.processingByTab[state.activeTab] : false),
    /** True when any tab is currently streaming. */
    anyProcessing: (state) => Object.values(state.processingByTab).some(Boolean),
    /**
     * Canonical display form of the active model, preferring the
     * ``provider/name[@variations]`` identifier so every display surface
     * shows the same string the user types into ``/model``. Falls back
     * to the raw API model id when ``llm_name`` hasn't been populated
     * yet (very first moments before the session_info event arrives).
     */
    modelDisplay: (state) => state.sessionInfo.llmName || state.sessionInfo.model || "",
    terrariumTarget: (state) => {
      if (state._instanceType !== "terrarium") return null
      const tab = state.activeTab
      if (!tab || tab.startsWith("ch:")) return null
      return tab
    },
  },

  actions: {
    /**
     * Public resync — re-fetch and rebuild the active tab's history.
     * Idempotent and cheap; safe to call from focus-change listeners,
     * chat panel re-activations, or programmatically. Soft-fails on
     * network errors so the UI doesn't flap on transient blips.
     */
    async refreshHistory(tab = this.activeTab) {
      if (!this._instanceId || !tab) return false
      try {
        return await this._resyncHistory(tab)
      } catch {
        return false
      }
    },

    initForInstance(instance) {
      if (this._instanceId === instance.id && this._ws) {
        // Same instance, WS already up. Nothing to do — the live WS
        // is already streaming any new events into ``messagesByTab``,
        // so re-fetching ``/history`` here would be wasted work and
        // (worse) would cause AttachTab's 5 s ``loadInstance`` poll
        // to rebuild the message list every five seconds, tearing
        // down + re-mounting every panel watching it.
        return
      }
      this._cleanup()
      const generation = ++this._instanceGeneration
      this._instanceId = instance.id
      this._instanceType = instance.type
      this.tabs = []
      this.messagesByTab = {}
      this.tokenUsage = {}
      this.runningJobs = {}
      this.unreadCounts = {}
      this.queuedMessages = []
      this.processingByTab = {}
      this._recentUserInputs = {}
      this._branchResyncPendingByTab = {}
      this._clearBranchResyncTimers()
      this.sessionInfo = {
        sessionId: "",
        model: "",
        llmName: "",
        agentName: "",
        compactThreshold: 0,
        maxContext: 0,
      }

      // Reset status store too
      const statusStore = useStatusStore()
      statusStore.reset()

      if (instance.type === "terrarium") {
        if (instance.has_root) {
          this._addTab("root")
        } else {
          this._addTab("ch:tasks")
        }
        this._connectTerrarium(instance.id, generation)
      } else {
        const name = instance.creatures[0]?.name || instance.config_name
        this._addTab(name)
        this._connectCreature(instance.id, generation)
      }

      // Restore saved tabs/active tab for this instance
      this._restoreTabs()
      if (!this.activeTab) this.activeTab = this.tabs[0] || null
    },

    openTab(tabKey) {
      this._addTab(tabKey)
      this.activeTab = tabKey
      this._saveTabs()

      // Load history for creature/root tabs
      if (this._instanceType === "terrarium") {
        this._loadHistory(tabKey)
      }
    },

    _addTab(key) {
      if (!this.tabs.includes(key)) {
        this.tabs.push(key)
        this.messagesByTab[key] = []
      }
    },

    closeTab(tab) {
      const idx = this.tabs.indexOf(tab)
      if (idx === -1) return
      this.tabs = this.tabs.filter((_, i) => i !== idx)
      if (this.activeTab === tab) {
        this.setActiveTab(this.tabs[Math.min(idx, this.tabs.length - 1)] || null)
      }
      this._saveTabs()
    },

    setActiveTab(tab) {
      this.activeTab = tab
      if (tab) delete this.unreadCounts[tab]
      this._saveTabs()
      if (tab && this._instanceType === "terrarium") {
        const msgs = this.messagesByTab[tab]
        if (msgs && msgs.length === 0) {
          this._loadHistory(tab, this._instanceGeneration)
        }
      }
    },

    /** Interrupt the active tab's agent. Also stops its streaming flag. */
    async interrupt(tab) {
      if (!this._instanceId) return
      const target = tab || this.activeTab
      if (!target || target.startsWith("ch:")) return

      try {
        // Interrupt the main agent processing only.
        // Background jobs (sub-agents, background tools) are NOT cancelled —
        // they have their own lifecycle and must be stopped individually
        // via stopTask() from the running jobs panel.
        if (this.processingByTab[target]) {
          if (this._instanceType === "terrarium") {
            await terrariumAPI.interruptCreature(this._instanceId, target)
          } else {
            await agentAPI.interrupt(this._instanceId)
          }
          this.processingByTab[target] = false
        }
        // Do NOT mark running parts as interrupted or remove running jobs.
        // The backend will send proper done/error events when jobs complete.
      } catch (err) {
        console.error("Interrupt failed:", err)
      }
    },

    async send(text) {
      if (!this.activeTab || !this._ws) return
      if (typeof text === "string" ? !text.trim() : !text.length) return

      const tab = this.activeTab
      const now = Date.now()
      const contentParts = typeof text === "string" ? [{ type: "text", text }] : text
      const normalized = normalizeMessageContent(contentParts)
      const signature = contentSignature(contentParts)
      const msg = {
        id: "u_" + now,
        role: "user",
        content: normalized.content,
        contentParts: normalized.contentParts,
        timestamp: new Date(now).toISOString(),
      }

      this._recentUserInputs[`${tab}:${signature}`] = now
      if (this.processingByTab[tab]) {
        // Don't put in main chat — hold in queue, shown above input box
        msg.queued = true
        this.queuedMessages.push(msg)
      } else {
        this._addMsg(tab, msg)
      }

      if (tab.startsWith("ch:")) {
        const chName = tab.slice(3)
        try {
          await terrariumAPI.sendToChannel(this._instanceId, chName, contentParts, "human")
        } catch (err) {
          console.error("Channel send failed:", err)
        }
      } else {
        const target = tab
        if (this._ws.readyState === WebSocket.OPEN) {
          this._ws.send(JSON.stringify({ type: "input", target, content: contentParts }))
          // Flip processing optimistically — the backend's
          // processing_start event will confirm it; this ensures the
          // indicator and interrupt button appear immediately on the
          // correct tab even before the first chunk arrives.
          this.processingByTab[target] = true
        }
      }
    },

    async _loadHistory(target, generation = this._instanceGeneration) {
      try {
        const data = await terrariumAPI.getHistory(this._instanceId, target)
        if (generation !== this._instanceGeneration) return
        const { messages, events, is_processing: isProcessing } = data || {}
        if (events?.length) {
          const normalizedEvents = _dedupeAdjacentDuplicateEvents(events)
          // Cache raw events so the branch navigator can re-replay
          // without a network round-trip after the user clicks <prev/next>.
          this.eventsByTab[target] = normalizedEvents
          const view = this.branchViewByTab[target] || null
          const { messages: msgs, pendingJobs } = _replayEvents(messages, normalizedEvents, view)
          this.messagesByTab[target] = msgs
          this._restoreTokenUsage(target, normalizedEvents)
          this._restoreRunningState(target, pendingJobs, isProcessing)
        } else if (messages?.length) {
          this.messagesByTab[target] = _convertHistory(messages)
          // No event stream but the agent might still be mid-turn —
          // honour the backend's processing flag so the UI shows the
          // running indicator after a refresh.
          if (isProcessing) this.processingByTab[target] = true
        } else if (isProcessing) {
          this.processingByTab[target] = true
        }
      } catch (err) {
        // 404 = session has no prior history, which is fine. Anything
        // else is a real error and should be surfaced.
        if (err?.response?.status !== 404) {
          console.error("Failed to load history for", target, err)
        }
      }
    },

    /** Connect single WS for terrarium.
     *
     * Phase 3 collapsed the legacy ``/ws/terrariums/{id}`` route into
     * the per-creature ``/ws/sessions/{sid}/creatures/{cid}/chat`` URL.
     * The frontend wires onto the terrarium's root creature; per-tab
     * channel views read history through the legacy
     * ``terrariumAPI.getHistory`` path until the multi-tab session
     * shell is ported (Stage 3 work).
     */
    _connectTerrarium(terrariumId, generation) {
      const target = this.tabs[0] || "root"
      this._openWs({
        generation,
        url: wsUrl(`/ws/sessions/${terrariumId}/creatures/${target}/chat`),
        onOpen: () => {
          // Load all tab histories, then flush WS buffer
          const loads = []
          if (this.tabs[0]) {
            loads.push(this._loadHistory(this.tabs[0], generation))
          }
          for (const tab of this.tabs) {
            if (tab.startsWith("ch:")) {
              loads.push(this._loadHistory(tab, generation))
            }
          }
          Promise.all(loads)
            .catch((err) => console.error("Terrarium history load failed:", err))
            .finally(() => this._flushWsBuffer(generation))
        },
        reconnect: () => this._connectTerrarium(terrariumId, generation),
      })
    },

    /** Connect single WS for standalone creature */
    _connectCreature(agentId, generation) {
      this._openWs({
        generation,
        url: wsUrl(`/ws/sessions/_/creatures/${agentId}/chat`),
        onOpen: () => {
          const tabKey = this.tabs[0]
          if (tabKey) {
            this._loadAgentHistory(agentId, tabKey, generation)
          } else {
            this._flushWsBuffer(generation)
          }
        },
        reconnect: () => this._connectCreature(agentId, generation),
      })
    },

    /** Shared WS bootstrap: wires onmessage/onclose, handles generation
     *  checks, and schedules exponential-backoff reconnects. */
    _openWs({ generation, url, onOpen, reconnect }) {
      // Always replace the buffer so history events are re-accumulated
      // on reconnect — the backend re-replays state on open.
      this._historyLoaded = false
      this._wsBuffer = []
      if (this._reconnectTimer) {
        clearTimeout(this._reconnectTimer)
        this._reconnectTimer = null
      }

      const ws = new WebSocket(url)
      this._ws = ws
      this.wsStatus = "reconnecting"

      ws.onopen = () => {
        if (generation !== this._instanceGeneration || ws !== this._ws) return
        this.wsStatus = "open"
        this._reconnectDelay = 500
        onOpen?.()
      }
      ws.onmessage = (event) => {
        if (generation !== this._instanceGeneration || ws !== this._ws) return
        let data
        try {
          data = JSON.parse(event.data)
        } catch (err) {
          console.warn("Failed to parse WS message:", err)
          return
        }
        if (this._historyLoaded) {
          this._onMessage(data)
        } else {
          this._wsBuffer.push(data)
        }
      }
      ws.onclose = () => {
        if (generation !== this._instanceGeneration || ws !== this._ws) return
        this.wsStatus = "reconnecting"
        // Exponential backoff, capped at 10s.
        const delay = this._reconnectDelay
        this._reconnectDelay = Math.min(delay * 2, 10000)
        this._reconnectTimer = setTimeout(() => {
          this._reconnectTimer = null
          if (generation !== this._instanceGeneration) return
          reconnect()
        }, delay)
      }
      ws.onerror = () => {
        // onclose fires after this; reconnect is scheduled there.
      }
    },

    /** Flush any events that arrived while history was still loading. */
    _flushWsBuffer(generation) {
      if (generation !== this._instanceGeneration) return
      this._historyLoaded = true
      if (this._wsBuffer) {
        for (const data of this._wsBuffer) {
          this._onMessage(data)
        }
        this._wsBuffer = []
      }
    },

    async _loadAgentHistory(agentId, tabKey, generation = this._instanceGeneration) {
      try {
        const data = await agentAPI.getHistory(agentId)
        if (generation !== this._instanceGeneration) return
        const { messages, events, is_processing: isProcessing } = data || {}
        if (events?.length) {
          const normalizedEvents = _dedupeAdjacentDuplicateEvents(events)
          // Cache raw events so branch navigation works after resume
          // without an extra network round-trip.
          this.eventsByTab[tabKey] = normalizedEvents
          const view = this.branchViewByTab[tabKey] || null
          const { messages: msgs, pendingJobs } = _replayEvents(messages, normalizedEvents, view)
          this.messagesByTab[tabKey] = msgs
          this._restoreTokenUsage(tabKey, normalizedEvents)
          this._restoreRunningState(tabKey, pendingJobs, isProcessing)
        } else if (messages?.length) {
          this.messagesByTab[tabKey] = _convertHistory(messages)
          if (isProcessing) this.processingByTab[tabKey] = true
        } else if (isProcessing) {
          this.processingByTab[tabKey] = true
        }
      } catch (err) {
        if (err?.response?.status !== 404) {
          console.error("Failed to load agent history:", err)
        }
      }
      this._flushWsBuffer(generation)
    },

    /** Restore running jobs from replay result. */
    _restoreRunningState(tabKey, pendingJobs, isProcessing = false) {
      for (const [jobId, job] of Object.entries(pendingJobs)) {
        this.runningJobs[jobId] = job
      }
      if (tabKey) {
        this.processingByTab[tabKey] = !!isProcessing
        this._rehydrateRunningParts(tabKey, pendingJobs)
      }
      if (Object.keys(pendingJobs).length > 0) {
        this._ensureJobTimer()
      }
    },

    _rehydrateRunningParts(tabKey, pendingJobs) {
      const msgs = this.messagesByTab[tabKey]
      if (!msgs) return
      const pendingIds = new Set(Object.keys(pendingJobs))
      for (const msg of msgs) {
        for (const part of msg.parts || []) {
          if (part.type !== "tool") continue
          const partJobId = part.jobId || part.id
          if (!pendingIds.has(partJobId)) continue
          part.status = "running"
          if (!part.startedAt) part.startedAt = pendingJobs[partJobId]?.startedAt || Date.now()
        }
      }
    },

    /** Restore token usage from event log (for page refresh) */
    _restoreTokenUsage(source, events) {
      for (const evt of _dedupeAdjacentDuplicateEvents(events)) {
        const isTokenEvt =
          (evt.type === "activity" && evt.activity_type === "token_usage") ||
          evt.type === "token_usage"
        if (isTokenEvt) {
          const prev = this.tokenUsage[source] || {
            prompt: 0,
            completion: 0,
            total: 0,
            cached: 0,
            lastPrompt: 0,
          }
          this.tokenUsage[source] = {
            prompt: prev.prompt + (evt.prompt_tokens || 0),
            completion: prev.completion + (evt.completion_tokens || 0),
            total: prev.total + (evt.total_tokens || 0),
            cached: prev.cached + (evt.cached_tokens || 0),
            lastPrompt: evt.prompt_tokens || prev.lastPrompt,
          }
        }
      }
    },

    /** Handle ALL incoming WS messages */
    _onMessage(data) {
      const source = data.source || ""

      if (data.type === "user_input") {
        this._handleUserInput(source, data)
      } else if (data.type === "text") {
        // If we get text chunks but the tab isn't marked processing
        // (e.g. reconnect mid-stream), flip it so the UI shows the
        // streaming indicator on the correct tab.
        if (source && !this.processingByTab[source]) {
          this.processingByTab[source] = true
        }
        this._appendStreamChunk(source, data.content)
      } else if (data.type === "processing_start") {
        if (source) this.processingByTab[source] = true
        // Promote queued user messages (agent is now processing them)
        this._promoteQueuedMessages(source)
      } else if (data.type === "processing_end") {
        this._finishStream(source)
        this._scheduleBranchResync(source)
      } else if (data.type === "idle") {
        if (source) this.processingByTab[source] = false
        this._finishStream(source)
        this._scheduleBranchResync(source)
      } else if (data.type === "activity") {
        this._handleActivity(source, data)
      } else if (data.type === "image") {
        this._handleAssistantImage(source, data)
      } else if (data.type === "channel_message") {
        this._handleChannelMessage(data)
      } else if (
        data.type === "ask_text" ||
        data.type === "confirm" ||
        data.type === "selection" ||
        data.type === "card" ||
        data.type === "notification" ||
        data.type === "progress"
      ) {
        this._handleUIEvent(source, data)
      } else if (data.type === "ui_supersede") {
        this._handleUISupersede(source, data)
      } else if (data.type === "ui_reply_ack") {
        this._handleUIReplyAck(source, data)
      } else if (data.type === "error") {
        this._addMsg(source, {
          id: "err_" + Date.now(),
          role: "system",
          content: "Error: " + (data.content || ""),
          timestamp: new Date().toISOString(),
        })
        if (source) this.processingByTab[source] = false
      }
    },

    /**
     * Phase B: handle a typed OutputEvent from the bus.
     *
     * For ``progress`` events with ``update_target`` we mutate the
     * existing message in place; for everything else we append a new
     * ``role: "ui_event"`` message. Notifications can either inline
     * (chat surface) or pop as ElMessage toasts (toast surface).
     */
    _handleUIEvent(source, data) {
      const tab = source || ""
      const eventType = data.type
      const eventId = data.event_id || `ev_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`
      const payload = data.payload || {}

      // Toast notifications skip the chat list — fire ElMessage and
      // return. Inline notifications fall through to the message path.
      if (eventType === "notification" && data.surface === "toast") {
        const level = payload.level || "info"
        const elType = ["info", "success", "warning", "error"].includes(level) ? level : "info"
        ElMessage({
          type: elType,
          message: payload.text || "",
          duration: payload.duration_ms || 4000,
        })
        return
      }

      // Progress events with update_target mutate the existing one.
      if (eventType === "progress" && data.update_target) {
        const list = this.messagesByTab[tab] || []
        const existing = list.find((m) => m.role === "ui_event" && m.eventId === data.update_target)
        if (existing) {
          existing.payload = { ...existing.payload, ...payload }
          return
        }
        // Fall through — first emit was missed; treat as new.
      }

      const msg = {
        id: `ui_${eventId}`,
        role: "ui_event",
        uiEventType: eventType,
        eventId,
        payload,
        interactive: !!data.interactive,
        surface: data.surface || "chat",
        tab,
        timestamp: new Date().toISOString(),
        replied: false,
        superseded: false,
        timedOut: false,
        repliedActionId: "",
        repliedValues: null,
      }
      this._addMsg(tab, msg)
    },

    _handleUISupersede(source, data) {
      const tab = source || ""
      const list = this.messagesByTab[tab] || []
      const target = list.find((m) => m.role === "ui_event" && m.eventId === data.event_id)
      if (target && !target.replied) {
        target.superseded = true
      }
    },

    _handleUIReplyAck(source, data) {
      const tab = source || ""
      const list = this.messagesByTab[tab] || []
      const target = list.find((m) => m.role === "ui_event" && m.eventId === data.event_id)
      if (!target) return
      if (data.status === "accepted") {
        // Confirms our optimistic update; idempotent.
        target.replied = true
        return
      }
      if (data.status === "superseded") {
        // Another renderer beat us. Mark superseded only if we
        // haven't already optimistically claimed replied — otherwise
        // this is the post-success ack of a previous race that's no
        // longer relevant.
        if (!target.replied) {
          target.superseded = true
        }
        return
      }
      // status === "unknown": agent already moved on (timeout,
      // resume, etc.). Don't flag superseded — the user's reply was
      // just too late, surface as timed out.
      if (data.status === "unknown" && !target.replied) {
        target.timedOut = true
      }
    },

    /**
     * Phase B: send a UIReply over the chat WS.
     *
     * Optimistically marks the message replied so the UI clears
     * immediately. The ``ui_reply_ack`` from the server confirms
     * (status: accepted / superseded / unknown).
     */
    submitUIReply(tab, eventId, actionId, values) {
      if (!this._ws || this._ws.readyState !== WebSocket.OPEN) return
      const list = this.messagesByTab[tab] || []
      const target = list.find((m) => m.role === "ui_event" && m.eventId === eventId)
      if (target) {
        target.replied = true
        target.repliedActionId = actionId
        target.repliedValues = values || null
      }
      try {
        this._ws.send(
          JSON.stringify({
            type: "ui_reply",
            event_id: eventId,
            action_id: actionId,
            values: values || {},
            ts: Date.now() / 1000,
          }),
        )
      } catch (err) {
        console.error("submitUIReply failed:", err)
      }
    },

    _handleActivity(source, data) {
      const at = data.activity_type
      const name = data.name || "unknown"

      // Forward ALL activities to status store for dashboard
      const statusStore = useStatusStore()
      statusStore.handleActivity(data)

      if (at === "session_info") {
        // Merge — update fields present in the event, keep existing for absent ones
        if (data.session_id) this.sessionInfo.sessionId = data.session_id
        if (data.model) this.sessionInfo.model = data.model
        if (data.llm_name) this.sessionInfo.llmName = data.llm_name
        if (data.agent_name) this.sessionInfo.agentName = data.agent_name
        if (data.max_context != null) this.sessionInfo.maxContext = data.max_context
        if (data.compact_threshold != null)
          this.sessionInfo.compactThreshold = data.compact_threshold
        return
      }

      if (at === "token_usage") {
        const prev = this.tokenUsage[source] || {
          prompt: 0,
          completion: 0,
          total: 0,
          cached: 0,
          lastPrompt: 0,
        }
        this.tokenUsage[source] = {
          prompt: prev.prompt + (data.prompt_tokens || 0),
          completion: prev.completion + (data.completion_tokens || 0),
          total: prev.total + (data.total_tokens || 0),
          cached: prev.cached + (data.cached_tokens || 0),
          lastPrompt: data.prompt_tokens || prev.lastPrompt,
        }
        return
      }

      // Ensure we have a tab for this source
      if (!this.messagesByTab[source]) return
      const msgs = this.messagesByTab[source]

      if (at === "compact_start") {
        const round = data.compact_round || data.round || 0
        const existing = [...msgs]
          .reverse()
          .find((msg) => msg.role === "compact" && msg.round === round)
        if (existing) {
          existing.summary = ""
          existing.status = "running"
          existing.messagesCompacted = 0
        } else {
          msgs.push({
            id: "compact_" + round + "_" + Date.now(),
            role: "compact",
            round,
            summary: "",
            status: "running",
            messagesCompacted: 0,
            timestamp: new Date().toISOString(),
          })
        }
        return
      }

      if (at === "compact_complete") {
        const round = data.compact_round || data.round || 0
        const existing =
          [...msgs].reverse().find((msg) => msg.role === "compact" && msg.round === round) ||
          [...msgs].reverse().find((msg) => msg.role === "compact" && msg.status === "running")
        if (existing) {
          existing.round = round
          existing.summary = data.summary || ""
          existing.messagesCompacted = data.messages_compacted || 0
          existing.status = "done"
          return
        }
        msgs.push({
          id: "compact_" + round + "_" + Date.now(),
          role: "compact",
          round,
          summary: data.summary || "",
          status: "done",
          messagesCompacted: data.messages_compacted || 0,
          timestamp: new Date().toISOString(),
        })
        return
      }

      if (at === "context_cleared") {
        msgs.push({
          id: "clear_" + Date.now(),
          role: "clear",
          messagesCleared: data.messages_cleared || 0,
          timestamp: new Date().toISOString(),
        })
        return
      }

      if (at === "processing_error") {
        const errorType = data.error_type || "Error"
        const errorMsg = data.error || data.detail || "Unknown error"
        msgs.push({
          id: "err_" + Date.now(),
          role: "error",
          errorType,
          content: errorMsg,
          timestamp: new Date().toISOString(),
        })
        if (source) this.processingByTab[source] = false
        return
      }

      if (at === "trigger_fired") {
        const channel = data.channel || ""
        const sender = data.sender || ""
        const label = channel ? `channel: ${channel}` : name
        const from = sender ? ` from ${sender}` : ""
        msgs.push({
          id: "trig_" + Date.now(),
          role: "trigger",
          content: `${label}${from}`,
          triggerContent: data.content || "",
          channel,
          sender,
          timestamp: new Date().toISOString(),
        })
        return
      }

      if (at === "tool_start" || at === "subagent_start") {
        // Tool/subagent activity means the agent is processing
        if (source && !this.processingByTab[source]) {
          this.processingByTab[source] = true
        }
        const last = this._ensureAssistantMsg(msgs)
        if (last.parts.length > 0) {
          const tail = last.parts[last.parts.length - 1]
          if (tail.type === "text") tail._streaming = false
        }
        const toolId = data.id || "tc_" + Date.now()
        const jobId = data.job_id || ""
        last.parts.push({
          type: "tool",
          id: toolId,
          jobId,
          name,
          kind: at === "subagent_start" ? "subagent" : "tool",
          args: data.args || { info: data.detail },
          status: "running",
          result: "",
          tools_used: data.tools_used || [],
          children: [],
          startedAt: Date.now(),
        })
        // Track all tasks as running jobs (direct tasks are promotable)
        const runKey = jobId || toolId
        const isBg = data.background || false
        this.runningJobs[runKey] = {
          name,
          type: at === "subagent_start" ? "subagent" : "tool",
          startedAt: Date.now(),
          promotable: !isBg,
        }
        this._ensureJobTimer()
      } else if (at === "tool_done" || at === "subagent_done") {
        let tc = this._findToolPart(msgs, name, data.job_id)
        if (!tc) {
          const last = this._ensureAssistantMsg(msgs)
          tc = {
            type: "tool",
            id: data.id || "tc_" + Date.now(),
            jobId: data.job_id || "",
            name,
            kind: at === "subagent_done" ? "subagent" : "tool",
            args: {},
            status: "done",
            result: "",
            tools_used: [],
            children: [],
          }
          last.parts.push(tc)
        }
        tc.status = "done"
        const payload = toolResultPayload(data.result || data.output || data.detail || "", data)
        tc.result = payload.result
        tc.resultParts = payload.resultParts
        tc.resultMeta = payload.resultMeta
        if (data.tools_used) tc.tools_used = data.tools_used
        if (data.turns != null) tc.turns = data.turns
        if (data.duration != null) tc.duration = data.duration
        if (data.total_tokens != null) tc.total_tokens = data.total_tokens
        if (data.prompt_tokens != null) tc.prompt_tokens = data.prompt_tokens
        if (data.completion_tokens != null) tc.completion_tokens = data.completion_tokens
        delete this.runningJobs[tc.jobId || tc.id]
        this._checkJobTimer()
      } else if (at === "tool_error" || at === "subagent_error") {
        let tc = this._findToolPart(msgs, name, data.job_id)
        if (!tc) {
          const last = this._ensureAssistantMsg(msgs)
          tc = {
            type: "tool",
            id: data.id || "tc_" + Date.now(),
            jobId: data.job_id || "",
            name,
            kind: at === "subagent_error" ? "subagent" : "tool",
            args: {},
            status: "error",
            result: "",
            tools_used: [],
            children: [],
          }
          last.parts.push(tc)
        }
        tc.status = data.interrupted || data.final_state === "interrupted" ? "interrupted" : "error"
        const payload = toolResultPayload(data.result || data.error || data.detail || "", data)
        tc.result = payload.result
        tc.resultParts = payload.resultParts
        tc.resultMeta = payload.resultMeta
        if (data.tools_used) tc.tools_used = data.tools_used
        if (data.turns != null) tc.turns = data.turns
        if (data.duration != null) tc.duration = data.duration
        if (data.total_tokens != null) tc.total_tokens = data.total_tokens
        if (data.prompt_tokens != null) tc.prompt_tokens = data.prompt_tokens
        if (data.completion_tokens != null) tc.completion_tokens = data.completion_tokens
        delete this.runningJobs[tc.jobId || tc.id]
        this._checkJobTimer()
      } else if (at === "subagent_token_update") {
        // Live token usage update from a running sub-agent
        const saName = data.subagent || ""
        const saJobId = data.job_id || ""
        const sa = this._findSubagentPart(msgs, saName, saJobId)
        if (sa) {
          if (data.total_tokens) sa.total_tokens = data.total_tokens
          if (data.prompt_tokens) sa.prompt_tokens = data.prompt_tokens
          if (data.completion_tokens) sa.completion_tokens = data.completion_tokens
        }
      } else if (at?.startsWith("subagent_tool_")) {
        // Sub-agent internal tool activity: find parent by job_id or name
        const saName = data.subagent || ""
        const saJobId = data.job_id || ""
        const sa = this._findSubagentPart(msgs, saName, saJobId)
        if (sa) {
          if (!sa.children) sa.children = []
          if (!sa.tools_used) sa.tools_used = []
          const toolName = data.tool || data.detail || ""
          const subAct = at.replace("subagent_", "")
          if (subAct === "tool_start" && toolName) {
            sa.children.push({
              type: "tool",
              name: toolName,
              kind: "tool",
              args: { info: data.detail || "" },
              status: "running",
              result: "",
            })
            if (!sa.tools_used.includes(toolName)) sa.tools_used.push(toolName)
          } else if (subAct === "tool_done" && toolName) {
            const child = [...sa.children]
              .reverse()
              .find((c) => c.name === toolName && c.status === "running")
            if (child) {
              child.status = "done"
              child.result = data.detail || ""
            }
          } else if (subAct === "tool_error" && toolName) {
            const child = [...sa.children]
              .reverse()
              .find((c) => c.name === toolName && c.status === "running")
            if (child) {
              child.status = "error"
              child.result = data.detail || ""
            }
          }
        }
      } else if (at === "task_promoted") {
        // Task promoted to background — mark as no longer promotable
        const promJobId = data.job_id || ""
        if (promJobId && this.runningJobs[promJobId]) {
          this.runningJobs[promJobId].promotable = false
        }
      }
    },

    /** Promote a running direct task to background via API. */
    async promoteTask(jobId) {
      if (!this._instanceId || !this._instanceType) return
      if (this.runningJobs[jobId]) {
        this.runningJobs[jobId].promotable = false
      }
      try {
        if (this._instanceType === "terrarium") {
          const target = this.activeTab
          if (target && !target.startsWith("ch:")) {
            const { terrariumAPI } = await import("@/utils/api")
            await terrariumAPI.promoteCreatureTask(this._instanceId, target, jobId)
          }
        } else {
          const { agentAPI } = await import("@/utils/api")
          await agentAPI.promote(this._instanceId, jobId)
        }
      } catch (e) {
        console.warn("Failed to promote task:", e)
      }
    },

    _markBranchResyncPending(tab = this.activeTab, expected = null) {
      if (!tab) return
      const pending = this._branchResyncPendingByTab[tab] || {}
      this._branchResyncPendingByTab[tab] = {
        active: true,
        expectedBranchByTurn: {
          ...(pending.expectedBranchByTurn || {}),
          ...(expected?.expectedBranchByTurn || {}),
        },
      }
    },

    _scheduleBranchResync(tab) {
      const pending = tab ? this._branchResyncPendingByTab[tab] : null
      if (!tab || !pending?.active) return
      if (this._branchResyncTimers[tab]) clearTimeout(this._branchResyncTimers[tab])
      this._branchResyncTimers[tab] = setTimeout(async () => {
        delete this._branchResyncTimers[tab]
        if (!this._branchResyncPendingByTab[tab]?.active) return
        await this._resyncHistory(tab)
      }, BRANCH_RESYNC_DELAY_MS)
    },

    _clearBranchResyncTimers() {
      for (const timer of Object.values(this._branchResyncTimers || {})) {
        clearTimeout(timer)
      }
      this._branchResyncTimers = {}
    },

    _conversationUserPosition(tab, messageIdx) {
      const msgs = this.messagesByTab[tab] || []
      if (messageIdx == null || messageIdx < 0 || messageIdx >= msgs.length) return null
      if (msgs[messageIdx]?.role !== "user") return null
      let pos = 0
      for (let i = 0; i < messageIdx; i++) {
        if (msgs[i]?.role === "user") pos += 1
      }
      return pos
    },

    /** Regenerate the last assistant response using current settings.
     *
     * Wipes the entire current assistant turn from the local message
     * list (including tool calls / sub-agent dispatches that were part
     * of it) before triggering regen so the user sees the old turn
     * disappear immediately and the new one stream in fresh as a new
     * branch. After ``processing_end`` arrives via WS, ``_resyncHistory``
     * pulls the canonical event log including branch metadata so the
     * ``<1/N>`` navigator can flip back.
     */
    async regenerateLastResponse() {
      if (!this._instanceId || this._instanceType === "terrarium") {
        console.warn("Regenerate only supported for standalone creature instances currently")
        return
      }
      // Dedupe rapid double-clicks: another regen already in flight.
      if (this._regenInFlight) return
      this._regenInFlight = true
      const tab = this.activeTab
      this._markBranchResyncPending(tab)
      if (tab) {
        const msgs = this.messagesByTab[tab] || []
        // Drop ALL trailing assistant messages back to the most recent
        // user message. A single assistant turn may include multiple
        // assistant entries (text → tool → text) — leaving stale
        // intermediate parts breaks resync ordering.
        let cutAt = msgs.length
        for (let i = msgs.length - 1; i >= 0; i--) {
          if (msgs[i].role === "user") break
          cutAt = i
        }
        if (cutAt < msgs.length) msgs.splice(cutAt)
      }
      try {
        const { agentAPI } = await import("@/utils/api")
        await agentAPI.regenerate(this._instanceId)
        await this._resyncHistory(tab)
      } catch (e) {
        console.warn("Failed to regenerate:", e)
        this._scheduleBranchResync(tab)
      } finally {
        this._regenInFlight = false
      }
    },

    /** Edit a user message and re-run from that point.
     *
     * Same wipe-and-stream UX as regen: drop everything from the
     * edited message onward, then let the new branch stream in.
     *
     * ``messageIdx`` is the FRONTEND list index (which may include
     * non-conversation rows: errors, triggers, channel posts, splitters,
     * compact bubbles). The backend's ``msg_idx`` counts only
     * conversation messages (user / assistant). We translate here so
     * editing message N in the UI lands on the N-th conversation entry
     * server-side regardless of how many decorations sit in front of it.
     */
    async editMessage(messageIdx, newContent, target = {}) {
      if (!this._instanceId || this._instanceType === "terrarium") return false
      if (messageIdx == null) return false
      if (this._regenInFlight) return false
      this._regenInFlight = true
      const tab = this.activeTab
      let backendIdx = messageIdx
      let userPosition = target.userPosition
      const turnIndex = target.turnIndex
      const expectedLatestBranch = target.latestBranch
      this._markBranchResyncPending(tab, {
        expectedBranchByTurn:
          turnIndex != null && expectedLatestBranch != null
            ? { [turnIndex]: expectedLatestBranch + 1 }
            : {},
      })
      let validTarget = false
      if (tab) {
        const msgs = this.messagesByTab[tab] || []
        if (messageIdx >= 0 && messageIdx < msgs.length && msgs[messageIdx]?.role === "user") {
          validTarget = true
          userPosition = userPosition ?? this._conversationUserPosition(tab, messageIdx)
          // Back-compat fallback for servers that only understand the
          // URL index: count rendered conversation rows, excluding
          // decorations. New servers prefer turnIndex/userPosition.
          backendIdx = 0
          for (let i = 0; i < messageIdx; i++) {
            const r = msgs[i]?.role
            if (r === "user" || r === "assistant") backendIdx += 1
          }
        }
      }
      if (!validTarget && turnIndex == null && userPosition == null) {
        delete this._branchResyncPendingByTab[tab]
        this._regenInFlight = false
        return false
      }
      const previousMessages = tab ? [...(this.messagesByTab[tab] || [])] : null
      if (validTarget && tab) {
        // Keep the user row at ``messageIdx`` visible (with the new
        // content) and drop everything after it — the old assistant
        // response, tool calls, etc. Previously we spliced from
        // ``messageIdx`` itself, which made the edited message vanish
        // until ``_resyncHistory`` ran AFTER the LLM finished. That
        // gave a several-second gap where the chat showed only the
        // streaming reply with the question that prompted it gone.
        // ``_handleUserInput`` dedupes against the visible last-user
        // message, so the WS replay from the new branch won't double
        // it up.
        const msgs = this.messagesByTab[tab]
        const original = msgs[messageIdx]
        const normalized = normalizeMessageContent(newContent)
        const editedRow = {
          ...original,
          content: normalized.content,
          contentParts: normalized.contentParts,
        }
        msgs.splice(messageIdx, msgs.length - messageIdx, editedRow)
      }
      try {
        const { agentAPI } = await import("@/utils/api")
        const editResponse = await agentAPI.editMessage(this._instanceId, backendIdx, newContent, {
          turnIndex,
          userPosition,
        })
        if (turnIndex != null && editResponse?.branch_id != null) {
          this._markBranchResyncPending(tab, {
            expectedBranchByTurn: { [turnIndex]: editResponse.branch_id },
          })
        }
        const resynced = await this._resyncHistory(tab)
        return resynced !== false
      } catch (e) {
        delete this._branchResyncPendingByTab[tab]
        if (previousMessages && tab) this.messagesByTab[tab] = previousMessages
        console.warn("Failed to edit message:", e)
        return false
      } finally {
        this._regenInFlight = false
      }
    },

    /** Rewind conversation to a point (drop later messages). */
    async rewindTo(messageIdx) {
      if (!this._instanceId || this._instanceType === "terrarium") return
      try {
        const { agentAPI } = await import("@/utils/api")
        await agentAPI.rewindTo(this._instanceId, messageIdx)
        await this._resyncHistory(this.activeTab)
      } catch (e) {
        console.warn("Failed to rewind:", e)
      }
    },

    /** Re-fetch conversation history from the backend and rebuild the
     *  local message list. Called after edit/regenerate/rewind so the
     *  frontend matches the backend's truncated conversation.
     *
     *  Robustness: ALWAYS rebuild messages from whatever events the
     *  backend has at the moment of the call, even when the expected
     *  branch hasn't promoted yet. Earlier this method bailed early on
     *  ``!complete`` and left ``messagesByTab[tab]`` untouched — which
     *  meant edit+regen flashed an empty chat (because ``editMessage``
     *  pre-splices the local list before awaiting the API call). The
     *  retry timer would eventually pick up the late-arriving branch
     *  events, but in the gap the user saw their message vanish.
     *
     *  We split the two concerns:
     *  1. Render now: rebuild from whatever events landed.
     *  2. Promote: keep retrying until ``branchSelection`` matches the
     *     expected branch_id, so the ``<N/M>`` navigator settles on the
     *     right value.
     */
    async _resyncHistory(tab = this.activeTab) {
      if (!this._instanceId || !tab) return false
      try {
        const { agentAPI } = await import("@/utils/api")
        const data = await agentAPI.getHistory(this._instanceId)
        if (!data?.events) return false
        // Cache events + clear any stale branch override BEFORE the
        // promotion check, so the rebuild path always has fresh data.
        this.eventsByTab[tab] = data.events
        this.branchViewByTab[tab] = {}
        this._rebuildMessages(tab)

        const pending = this._branchResyncPendingByTab[tab]
        const expectedBranchByTurn = pending?.expectedBranchByTurn || {}
        if (Object.keys(expectedBranchByTurn).length) {
          const { branchMeta } = _replayEvents([], data.events)
          const branchSelection = branchMeta?.branchSelection || new Map()
          let complete = true
          for (const [turn, branch] of Object.entries(expectedBranchByTurn)) {
            if (branchSelection.get(Number(turn)) !== branch) {
              complete = false
              break
            }
          }
          if (!complete) {
            // Messages already rebuilt with what we have; just keep
            // retrying so the navigator catches up once the new branch
            // is fully written to the session store.
            this._scheduleBranchResync(tab)
            return false
          }
        }
        delete this._branchResyncPendingByTab[tab]
        return true
      } catch (e) {
        console.warn("Failed to resync history:", e)
        throw e
      }
    },

    /**
     * Rebuild ``messagesByTab[tab]`` from the cached event log,
     * applying the current ``branchViewByTab[tab]`` override.
     */
    _rebuildMessages(tab) {
      const events = this.eventsByTab[tab]
      if (!events) return
      const branchView = this.branchViewByTab[tab] || null
      const { messages } = _replayEvents([], events, branchView)
      this.messagesByTab[tab] = messages
    },

    /**
     * Switch the active branch for a turn. Re-runs replay against
     * the cached event log; no network round-trip.
     */
    selectBranch(turnIndex, branchId) {
      const tab = this.activeTab
      if (!tab) return
      if (!this.branchViewByTab[tab]) this.branchViewByTab[tab] = {}
      this.branchViewByTab[tab][turnIndex] = branchId
      this._rebuildMessages(tab)
    },

    /**
     * Find a tool part by job_id (reliable, any status) or name (running only).
     * Searches all messages backwards.
     */
    _findToolPart(msgs, name, jobId) {
      for (let i = msgs.length - 1; i >= 0; i--) {
        const msg = msgs[i]
        if (!msg.parts) continue
        for (let j = msg.parts.length - 1; j >= 0; j--) {
          const p = msg.parts[j]
          if (p.type !== "tool") continue
          // Match by job_id: any status (handles replay "running" + live "running")
          if (jobId && p.jobId === jobId) return p
        }
      }
      // Fallback: match by name, running status only
      for (let i = msgs.length - 1; i >= 0; i--) {
        const msg = msgs[i]
        if (!msg.parts) continue
        for (let j = msg.parts.length - 1; j >= 0; j--) {
          const p = msg.parts[j]
          if (p.type === "tool" && p.name === name && p.status === "running") return p
        }
      }
      return null
    },

    /**
     * Find a sub-agent part. Match by job_id first, then name, then any running sub-agent.
     */
    _findSubagentPart(msgs, saName, saJobId) {
      // 1. Match by job_id (most reliable - connects sub-agent tool events to parent)
      if (saJobId) {
        for (let i = msgs.length - 1; i >= 0; i--) {
          const msg = msgs[i]
          if (!msg.parts) continue
          for (let j = msg.parts.length - 1; j >= 0; j--) {
            const p = msg.parts[j]
            if (p.type === "tool" && p.kind === "subagent" && p.jobId === saJobId) return p
          }
        }
      }
      // 2. Match by name (exact or partial - handles "researcher" matching "agent_researcher[abc]")
      if (saName) {
        for (let i = msgs.length - 1; i >= 0; i--) {
          const msg = msgs[i]
          if (!msg.parts) continue
          for (let j = msg.parts.length - 1; j >= 0; j--) {
            const p = msg.parts[j]
            if (p.type !== "tool" || p.kind !== "subagent" || p.status !== "running") continue
            if (p.name === saName) return p
            // Partial match: "researcher" in "agent_researcher[abc123]"
            if (p.name.includes(saName)) return p
          }
        }
      }
      // 3. Last resort: any running sub-agent
      for (let i = msgs.length - 1; i >= 0; i--) {
        const msg = msgs[i]
        if (!msg.parts) continue
        for (let j = msg.parts.length - 1; j >= 0; j--) {
          const p = msg.parts[j]
          if (p.type === "tool" && p.kind === "subagent" && p.status === "running") return p
        }
      }
      return null
    },

    _handleUserInput(source, data) {
      if (!source || !this.messagesByTab[source]) return
      const normalized = normalizeMessageContent(data.content)
      const signature = `${source}:${contentSignature(data.content)}`
      const now = Date.now()
      const seenAt = this._recentUserInputs[signature] || 0
      if (now - seenAt < 2000) return
      const msgs = this.messagesByTab[source]
      const last = msgs[msgs.length - 1]
      if (last?.role === "user" && last.content === normalized.content) {
        this._recentUserInputs[signature] = now
        return
      }
      this._recentUserInputs[signature] = now
      this._addMsg(source, {
        id: `u_sync_${now}`,
        role: "user",
        content: normalized.content,
        contentParts: normalized.contentParts,
        timestamp: data.timestamp || new Date((data.ts || now / 1000) * 1000).toISOString(),
      })
    },

    _handleChannelMessage(data) {
      const tabKey = `ch:${data.channel}`

      if (this.messagesByTab[tabKey]) {
        const existing = this.messagesByTab[tabKey]
        if (data.message_id && existing.some((m) => m.id === data.message_id)) {
          return
        }
        const normalized = normalizeMessageContent(data.content)
        this.messagesByTab[tabKey].push({
          id: data.message_id || "ch_" + Date.now(),
          role: "channel",
          sender: data.sender,
          content: normalized.content,
          contentParts: normalized.contentParts,
          timestamp: data.timestamp,
        })
        if (this.activeTab !== tabKey) {
          this.unreadCounts[tabKey] = (this.unreadCounts[tabKey] || 0) + 1
        }
      }

      const msgStore = useMessagesStore()
      const normalized = normalizeMessageContent(data.content)
      msgStore.addChannelMessage(data.channel, {
        channel: data.channel,
        sender: data.sender,
        content: normalized.content,
        contentParts: normalized.contentParts,
        timestamp: data.timestamp,
      })

      const instStore = useInstancesStore()
      if (instStore.current) {
        const ch = instStore.current.channels.find((c) => c.name === data.channel)
        if (ch) ch.message_count = (ch.message_count || 0) + 1
      }
    },

    /** Move queued messages from the hold queue into the main chat. */
    _promoteQueuedMessages(source) {
      if (!this.queuedMessages.length) return
      const msgs = this.messagesByTab[source]
      if (!msgs) return
      for (const msg of this.queuedMessages) {
        delete msg.queued
        msgs.push(msg)
      }
      this.queuedMessages = []
    },

    _ensureAssistantMsg(msgs) {
      let last = msgs[msgs.length - 1]
      if (!last || last.role !== "assistant" || !last._streaming) {
        last = {
          id: "m_" + Date.now(),
          role: "assistant",
          parts: [],
          timestamp: new Date().toISOString(),
          _streaming: true,
        }
        msgs.push(last)
      }
      if (!last.parts) last.parts = []
      return last
    },

    _appendStreamChunk(source, content) {
      const msgs = this.messagesByTab[source]
      if (!msgs) return
      const last = this._ensureAssistantMsg(msgs)
      const tail = last.parts.length > 0 ? last.parts[last.parts.length - 1] : null
      if (tail && tail.type === "text" && tail._streaming) {
        tail.content += content
      } else {
        last.parts.push({ type: "text", content, _streaming: true })
      }
    },

    /** Append an assistant-emitted image to the active assistant message.
     *
     * Shape matches the user-image render path in ChatMessage.vue:
     * `{type: "image_url", image_url: {url, detail}, meta: {...}}`.
     * Any streaming text part loses its _streaming flag so further
     * text chunks land in a fresh part after the image.
     */
    _handleAssistantImage(source, data) {
      const msgs = this.messagesByTab[source]
      if (!msgs) return
      const last = this._ensureAssistantMsg(msgs)
      for (const p of last.parts || []) {
        if (p.type === "text") p._streaming = false
      }
      last.parts.push({
        type: "image_url",
        image_url: {
          url: data.url,
          detail: data.detail || "auto",
        },
        meta: data.meta || {},
      })
    },

    _finishStream(source) {
      if (source) this.processingByTab[source] = false
      const msgs = this.messagesByTab[source]
      if (msgs) {
        const last = msgs[msgs.length - 1]
        if (last?._streaming) {
          last._streaming = false
          for (const p of last.parts || []) {
            if (p.type === "text") p._streaming = false
          }
        }
      }
    },

    _addMsg(tabKey, msg) {
      if (!this.messagesByTab[tabKey]) this.messagesByTab[tabKey] = []
      this.messagesByTab[tabKey].push(msg)
    },

    // ── Job timer (reactive elapsed tracking) ──

    /** Start 1s interval to tick _jobTick, making elapsed times reactive.
     *
     * Visibility-aware — while the tab is hidden the tick pauses and
     * every `getJobElapsed` subscriber stops re-rendering. A stale
     * elapsed time for a backgrounded tab is fine; what matters is
     * not burning GPU driving a reactive effect the user can't see.
     */
    _ensureJobTimer() {
      if (this._jobTimer !== null) return
      const ctrl = createVisibilityInterval(() => {
        this._jobTick++
      }, 1000)
      ctrl.start()
      this._jobTimer = ctrl
    },

    /** Stop timer if no more running jobs. */
    _checkJobTimer() {
      if (Object.keys(this.runningJobs).length === 0 && this._jobTimer !== null) {
        this._jobTimer.stop()
        this._jobTimer = null
      }
    },

    /** Get elapsed seconds for a job (reactive via _jobTick). */
    getJobElapsed(job) {
      // Reference _jobTick to make this reactive
      void this._jobTick
      if (!job?.startedAt) return ""
      const secs = Math.floor((Date.now() - job.startedAt) / 1000)
      return secs > 0 ? `${secs}s` : ""
    },

    /**
     * Wipe the chat store back to a neutral, disconnected state.
     *
     * Used when leaving a surface that borrowed the chat store (most
     * importantly the SessionHistoryViewer, which writes saved-session
     * data into ``messagesByTab``/``tabs``/``_instanceId``). Without
     * this, navigating from the session viewer back to a running
     * ``/instances/<id>`` page renders the previous session's content
     * for the brief window between mount and the async
     * ``initForInstance`` call — and ``_saveTabs``/``_restoreTabs``
     * keys can hit the wrong instance bucket because ``_instanceId``
     * still points at ``session:<name>``.
     *
     * Does NOT open a websocket; pair it with ``initForInstance`` if
     * you want to attach to a new live instance afterwards.
     */
    resetForRouteSwitch() {
      this._cleanup()
      this._instanceGeneration++
      this._instanceId = null
      this._instanceType = null
      this.tabs = []
      this.messagesByTab = {}
      this.tokenUsage = {}
      this.runningJobs = {}
      this.unreadCounts = {}
      this.queuedMessages = []
      this.processingByTab = {}
      this.eventsByTab = {}
      this.branchViewByTab = {}
      this._recentUserInputs = {}
      this._branchResyncPendingByTab = {}
      this._clearBranchResyncTimers()
      this.sessionInfo = {
        sessionId: "",
        model: "",
        llmName: "",
        agentName: "",
        compactThreshold: 0,
        maxContext: 0,
      }
      const statusStore = useStatusStore()
      statusStore.reset()
    },

    _cleanup() {
      this.activeTab = null
      this._historyLoaded = false
      this._wsBuffer = []
      this._branchResyncPendingByTab = {}
      this._clearBranchResyncTimers()
      if (this._reconnectTimer) {
        clearTimeout(this._reconnectTimer)
        this._reconnectTimer = null
      }
      this._reconnectDelay = 500
      this.wsStatus = "closed"
      if (this._ws) {
        // Null the callbacks first — otherwise onclose will fire during
        // close() and schedule a reconnect for the old instance.
        this._ws.onopen = null
        this._ws.onmessage = null
        this._ws.onclose = null
        this._ws.onerror = null
        try {
          this._ws.close()
        } catch {
          // ignore
        }
        this._ws = null
      }
      if (this._jobTimer !== null) {
        this._jobTimer.stop()
        this._jobTimer = null
      }
    },

    _saveTabs() {
      if (!this._instanceId) return
      const key = `chat-tabs-${this._instanceId}`
      setHybridPref(
        key,
        {
          tabs: this.tabs,
          activeTab: this.activeTab,
        },
        { json: true },
      )
    },

    _restoreTabs() {
      if (!this._instanceId) return
      const key = `chat-tabs-${this._instanceId}`
      const saved = getHybridPrefSync(key, null, { json: true })
      if (saved?.tabs?.length) {
        for (const tab of saved.tabs) {
          this._addTab(tab)
        }
        if (saved.activeTab && this.tabs.includes(saved.activeTab)) {
          this.activeTab = saved.activeTab
        }
      }
    },
  },
})
