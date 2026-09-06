import { ElMessage } from "element-plus"
import { getCurrentInstance, markRaw } from "vue"

import { injectScope, registerScopeDisposer, scopeOfStoreId } from "@/composables/useScope"
import { createVisibilityInterval } from "@/composables/useVisibilityInterval"
import {
  attentionSummary,
  createAttentionState,
  markAttentionRead,
  publishAttention,
  reduceAttentionEdge,
  removeAttentionScope,
  restoreAttentionFromHistory,
} from "@/stores/attention"
import {
  adoptLocalCommandResultSelections,
  bindLocalCommandResultContexts,
  buildLocalCommandResultMessage,
  captureLocalCommandResultContext,
  mergeLocalCommandResults,
  registerLocalCommandResultContext,
  releaseLocalCommandResultContext,
} from "@/stores/chatCommandResults"
import { useClusterStore } from "@/stores/cluster"
import { useInstancesStore } from "@/stores/instances"
import { useLocaleStore } from "@/stores/locale"
import { useMessagesStore } from "@/stores/messages"
import { useNotificationsStore } from "@/stores/notifications"
import { useStatusStore } from "@/stores/status"
import { agentAPI, terrariumAPI } from "@/utils/api"
import { translate } from "@/utils/i18n"
import { readLocalJsonPref, writeLocalJsonPref } from "@/utils/uiPrefs"
import { wsUrl } from "@/utils/wsUrl"

const BRANCH_RESYNC_DELAY_MS = 350
const COMMAND_INVENTORY_TTL_MS = 30_000
// A pending branch op whose expected branch never lands (e.g. the edit
// request was lost before dispatch) must not keep the stale-history
// guard alive forever — after this many incomplete retries the guard is
// dropped and the view reconciles to whatever the backend has.
const BRANCH_RESYNC_MAX_RETRIES = 40

/** Transcript marker for a turn that a Drive delivery started. */
function _driveTurnMessage(data, id, timestamp) {
  const kind = data.drive_kind || "drive"
  const reason = data.delivery_reason || ""
  return {
    id,
    role: "trigger",
    content: `drive turn: ${kind} ${data.drive_id || ""}${reason ? ` (${reason})` : ""}`,
    triggerContent: data.objective || "",
    channel: "",
    sender: "",
    driveId: data.drive_id || "",
    timestamp,
  }
}

function _newRequestId() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID()
  return `branch_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`
}

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

const ATTENTION_INTERACTIVE_TYPES = ["ask_text", "confirm", "selection", "card"]
const ATTENTION_SUMMARY_MAX = 200
// Accumulators re-normalize (whitespace + trim) once they pass this bound,
// then refuse further growth; summaries only ever read the first
// ATTENTION_SUMMARY_MAX characters, so the retained copy stays bounded no
// matter how long the turn streams.
const ATTENTION_ACCUMULATOR_CEILING = ATTENTION_SUMMARY_MAX * 2

function _truncateAttentionSummary(text) {
  const flat = String(text || "")
    .replace(/\s+/g, " ")
    .trim()
  if (!flat) return ""
  return flat.length > ATTENTION_SUMMARY_MAX ? `${flat.slice(0, ATTENTION_SUMMARY_MAX - 1)}…` : flat
}

/** Fold one streamed chunk into the per-tab accumulator, bounded.
 *
 * Normalizing at the ceiling keeps the retained copy tiny for arbitrarily
 * long responses while producing the same summary as normalizing the full
 * text: leading whitespace is dropped before the first ceiling compaction,
 * and a summary never reads past the first ATTENTION_SUMMARY_MAX chars.
 */
function _accumulateAttentionStreamText(accumulated, chunk) {
  const next = (accumulated || "") + chunk
  if (next.length > ATTENTION_ACCUMULATOR_CEILING) {
    const flat = next.replace(/\s+/g, " ").trim()
    return flat.length > ATTENTION_ACCUMULATOR_CEILING
      ? flat.slice(0, ATTENTION_ACCUMULATOR_CEILING)
      : flat
  }
  return next
}

/**
 * Extract a bounded notification summary from a live WS frame.
 *
 * Called from ``_onMessage`` BEFORE the frame's own handler stores anything.
 * Interactive prompts are read from the frame payload; completion summaries
 * come from the turn's raw stream text accumulated in
 * ``_attentionStreamTextByTab`` — which is collected before branch-view
 * gating, so the preview describes the response that just completed even
 * when the user is viewing a different branch, and a text-less turn
 * summaries to "" instead of surfacing a stale earlier turn.
 */
function _attentionEdgeSummary(data, streamedText) {
  if (ATTENTION_INTERACTIVE_TYPES.includes(data.type)) {
    const payload = data.payload || {}
    return _truncateAttentionSummary(
      payload.prompt || payload.body || payload.text || payload.title,
    )
  }
  if (data.type === "processing_end") return _truncateAttentionSummary(streamedText)
  return ""
}

function normalizeMessageContent(content) {
  const contentParts = normalizeContentParts(content)
  return {
    content: typeof content === "string" ? content : extractTextContent(content),
    contentParts,
  }
}

function _insertReasoningSegments(message, segments, idPrefix = "reasoning_") {
  if (!message || !Array.isArray(segments) || !segments.length) return
  const parts = Array.isArray(message.parts) ? message.parts : []
  const start = Number.isInteger(message._reasoningCursor) ? message._reasoningCursor : 0
  const out = []
  let cursor = Math.min(Math.max(start, 0), parts.length)
  let n = 0

  const nextText = (from) => parts.findIndex((part, idx) => idx >= from && part?.type === "text")
  const nextTool = (from, callId) =>
    parts.findIndex((part, idx) => idx >= from && part?.type === "tool" && part.jobId === callId)

  for (const segment of segments) {
    if (!segment || typeof segment !== "object") continue
    if (segment.type === "reasoning") {
      const part = {
        type: "reasoning",
        id: `${idPrefix}${n++}`,
        source: segment.source || "reasoning",
        text: segment.text || "",
      }
      if (segment.signature) part.signature = segment.signature
      out.push(part)
      continue
    }
    const anchor =
      segment.type === "text"
        ? nextText(cursor)
        : segment.type === "tool_call_ref"
          ? nextTool(cursor, segment.call_id)
          : -1
    if (anchor === -1) continue
    while (cursor < anchor) out.push(parts[cursor++])
    out.push(parts[cursor++])
  }
  while (cursor < parts.length) out.push(parts[cursor++])
  if (!out.length) return

  message.parts = [...parts.slice(0, start), ...out]
  message._reasoningCursor = message.parts.length
  const tail = message.parts[message.parts.length - 1]
  if (tail?.type === "text") {
    tail._roundEnd = true
    tail._streaming = false
  }
}

function contentSignature(content) {
  if (typeof content === "string") return `text:${content}`
  const normalized = normalizeContentParts(content)
  if (!normalized) return ""
  return JSON.stringify(normalized)
}

// ── Per-tab message indexes (module-level by design) ──
// Live WS frames look up tool parts by job_id on every tool event;
// scanning every message×part per frame is O(N) and dominates long
// sessions. The indexes are pure caches over the message arrays,
// rebuilt on every wholesale replacement (``_setMessages``), so they
// stay out of Pinia state — reactive, devtools-visible copies would
// cost more than the scans they replace.
const _indexesByStore = new WeakMap()

function _tabIndexes(store, tab) {
  let byTab = _indexesByStore.get(store)
  if (!byTab) {
    byTab = new Map()
    _indexesByStore.set(store, byTab)
  }
  let idx = byTab.get(tab)
  if (!idx) {
    idx = { tools: new Map(), channelIds: new Set() }
    byTab.set(tab, idx)
  }
  return idx
}

function _indexMsgToolsInto(idx, msg) {
  if (!Array.isArray(msg?.parts)) return
  for (const p of msg.parts) {
    if (p?.type === "tool" && p.jobId) idx.tools.set(p.jobId, p)
  }
}

/** Reuse the previous message object (and matching per-part objects)
 * when a rebuild produces a message with the same id, so ChatMessage
 * props keep their identity across resyncs and Vue can skip whole
 * re-renders of unchanged messages. */
function _mergeMessageInPlace(oldMsg, freshMsg) {
  const freshParts = Array.isArray(freshMsg.parts) ? freshMsg.parts : null
  if (freshParts && Array.isArray(oldMsg.parts)) {
    const partById = new Map()
    for (const p of oldMsg.parts) if (p?.id != null) partById.set(p.id, p)
    for (let i = 0; i < freshParts.length; i++) {
      const fp = freshParts[i]
      const op = fp?.id != null ? partById.get(fp.id) : undefined
      if (op && op !== fp) {
        Object.assign(op, fp)
        freshParts[i] = op
      }
    }
  }
  Object.assign(oldMsg, freshMsg)
}

export function _parseSlashCommand(content) {
  let text = null
  if (typeof content === "string") {
    text = content
  } else if (
    Array.isArray(content) &&
    content.length === 1 &&
    content[0]?.type === "text" &&
    typeof content[0].text === "string"
  ) {
    text = content[0].text
  }

  if (!text?.startsWith("/")) return null
  const match = /^\/([^\s]+)(?:\s+([\s\S]*))?$/.exec(text)
  if (!match) return null
  return { command: match[1].toLowerCase(), args: match[2] || "" }
}

function textSignature(content) {
  // Coarser comparator used as a fallback by ``_handleUserInputInjected``
  // when the strict ``contentSignature`` comparison fails. Strips
  // multimodal parts and trims whitespace so a queue entry whose
  // ``contentParts`` slightly differs from the backend's drained
  // ``data.content`` (extra image part, mismatched dict-vs-typed
  // shape, trailing whitespace) still matches by the text the user
  // actually typed. Returns ``""`` when no text content present.
  if (typeof content === "string") return content.trim()
  if (!Array.isArray(content)) return ""
  return content
    .filter((p) => p && typeof p === "object" && p.type === "text")
    .map((p) => String(p.text || "").trim())
    .join("\n")
    .trim()
}

function toolResultPayload(result, data = {}) {
  // Backend flattens whitelisted metadata fields onto the top level
  // of the WS frame (see ``_STREAM_METADATA_KEYS`` in
  // studio/attach/_event_stream.py) — including ``canvas_preview``.
  // History events do the same shape: canvas_preview lives at the
  // top level of the persisted ``tool_result`` event row. The FE's
  // ``resultMeta`` is the unified bag we expose to renderers, so
  // fold the flat keys back into a nested object here. Without this,
  // ``data.metadata`` is undefined and the canvas store never picks
  // the file preview up (Feat 1 wire-up bug).
  let resultMeta = data.result_meta || data.output_meta || data.metadata || null
  if (data.canvas_preview && (!resultMeta || !resultMeta.canvas_preview)) {
    resultMeta = { ...(resultMeta || {}), canvas_preview: data.canvas_preview }
  }
  if (data.tool_metadata) {
    resultMeta = { ...(resultMeta || {}), ...data.tool_metadata }
  }
  const parts = normalizeContentParts(result)
  return {
    result,
    resultParts: parts ? markRaw(parts) : parts,
    resultMeta: resultMeta ? markRaw(resultMeta) : resultMeta,
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
      const message = {
        id: "h_" + result.length,
        role: "assistant",
        content: normalized.content,
        contentParts: normalized.contentParts,
        timestamp: "",
        tool_calls: tcs.length ? tcs : undefined,
      }
      if (Array.isArray(msg._kt_assistant_segments) && msg._kt_assistant_segments.length) {
        const tcById = new Map(tcs.map((tc) => [tc.id, tc]))
        const parts = []
        for (const segment of msg._kt_assistant_segments) {
          if (!segment || typeof segment !== "object") continue
          if (segment.type === "reasoning") {
            const part = {
              type: "reasoning",
              id: `h_${result.length}_r${parts.length}`,
              source: segment.source || "reasoning",
              text: segment.text || "",
            }
            if (segment.signature) part.signature = segment.signature
            parts.push(part)
          } else if (segment.type === "text" && segment.text) {
            parts.push({
              type: "text",
              id: `h_${result.length}_c${parts.length}`,
              content: segment.text,
            })
          } else if (segment.type === "tool_call_ref" && tcById.has(segment.call_id)) {
            const tc = tcById.get(segment.call_id)
            parts.push({
              type: "tool",
              id: `h_${result.length}_t${tc.id}`,
              jobId: tc.id,
              name: tc.name,
              kind: tc.kind,
              args: tc.args,
              status: tc.status,
              result: tc.result,
              tools_used: [],
              children: [],
            })
          }
        }
        for (const part of normalized.contentParts || []) {
          if (part.type === "image_url") {
            parts.push({
              type: "image_url",
              id: `h_${result.length}_img${parts.length}`,
              image_url: part.image_url,
              meta: part.meta,
            })
          }
        }
        message.parts = parts
        message._reasoningCursor = parts.length
      }
      result.push(message)
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
    const hasExplicitPath = Array.isArray(evt?.parent_branch_path)
    const explicit = _coercePath(evt?.parent_branch_path)
    if (typeof eid === "number") {
      if (hasExplicitPath) {
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

function _pathMatches(path, selected, beforeTurn = Infinity) {
  for (const [t, b] of path) {
    if (t < beforeTurn && selected.has(t) && selected.get(t) !== b) return false
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
    if (branchView && Object.prototype.hasOwnProperty.call(branchView, ti)) {
      const requested = branchView[ti]
      const match = candidates.find((entry) => entry.branch === requested)
      if (match) {
        selected.set(ti, match.branch)
        continue
      }
      // Honor the override STRICTLY even when no candidate carries the
      // requested branch yet. Edit-regen / regenerate set ``branchView``
      // to a predicted branch BEFORE the backend's events arrive; the
      // previous ``Math.max(candidates)`` fallback flipped the render
      // back to the OLD branch during that gap, which the user
      // reported as "previous branch content suddenly displayed".
      // Returning ``requested`` keeps the predicted branch sticky;
      // ``liveIds`` then renders empty for that (turn, branch) until
      // the real events land. The next resync rebuilds correctly.
      selected.set(ti, requested)
      continue
    }
    if (!candidates.length) continue
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
    if (!_pathMatches(path, branchSelection, ti)) continue
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
    if (!_pathMatches(path, branchSelection, ti)) continue
    liveIds.add(eid)
  }
  return { byTurn, liveIds, branchSelection }
}

function _commandResultBranchSelection(events, branchView) {
  if (!Array.isArray(events)) return null
  return _collectBranchMetadata(events, branchView).branchSelection
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

export function _prepareReplayEvents(events, branchView = null) {
  const dedupedEvents = _dedupeAdjacentDuplicateEvents(events)
  return {
    events: dedupedEvents,
    branchMetadata: _collectBranchMetadata(dedupedEvents, branchView),
  }
}

export function _replayEvents(messages, events, branchView = null, tab = "") {
  return _replayPreparedEvents(messages, _prepareReplayEvents(events, branchView), tab)
}

function _replayPreparedEvents(messages, prepared, tab = "") {
  // A terminal event is authoritative: liveness comes from the backend,
  // which withholds a job's terminal (``normalize_resumable_events`` with
  // ``live_job_ids``) for as long as it sees the job running. A job with
  // no terminal falls through to the pendingJobs sweep as "running"; a
  // terminal that reaches here (real OR ``_synthetic_resume``) belongs to
  // a job that is genuinely dead and renders "interrupted"/"error"/"done"
  // — dead work resumed from a saved session reads as interrupted, never
  // stuck "running" forever (UXI-04).
  if (!prepared?.events?.length) {
    return {
      messages: _convertHistory(messages),
      pendingJobs: {},
      events: prepared?.events || [],
      branchMetadata: prepared?.branchMetadata || null,
    }
  }

  const events = prepared.events
  const { byTurn, liveIds, branchSelection } = prepared.branchMetadata

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
  const _seenUserRender = new Map()
  // Track job lifecycle: started jobs and completed jobs
  const startedJobs = {} // jobId -> tool part reference
  const completedJobs = new Set() // jobIds that received done/error
  const interactiveMessages = new Map()

  // Positional ids ("h_" + result.length) shift whenever an earlier
  // event is hidden (compact_replace ranges, branch filtering), which
  // remounts every later message on each resync. Events carry stable
  // numeric ids — derive message/part ids from the originating event so
  // rebuilds reuse component instances. Falls back to the positional
  // form for synthetic events without an event_id.
  let curEventKey = null

  function stableId(prefix) {
    return prefix + (curEventKey ?? String(result.length))
  }

  function ensureCur() {
    if (!cur) {
      cur = {
        id: stableId("h_"),
        role: "assistant",
        parts: [],
        timestamp: "",
      }
      result.push(cur)
    }
    return cur
  }

  function commitPendingReasoningRound(c) {
    if (!Number.isInteger(c._pendingReasoningCursor)) return
    c._reasoningCursor = c._pendingReasoningCursor
    c._pendingReasoningCursor = undefined
    const tail = c.parts.length ? c.parts[c.parts.length - 1] : null
    if (tail?.type === "text") {
      tail._roundEnd = true
      tail._streaming = false
    }
  }

  function appendText(content) {
    const c = ensureCur()
    commitPendingReasoningRound(c)
    const tail = c.parts.length ? c.parts[c.parts.length - 1] : null
    if (tail && tail.type === "text" && !tail._roundEnd) {
      tail.content += content
    } else {
      c.parts.push({ type: "text", content, id: stableId("txt_") })
    }
  }

  function _evtStartedTs(evt) {
    return typeof evt?.ts === "number" ? Math.round(evt.ts * 1000) : null
  }

  function addTool(name, kind, args, jobId, startedTs) {
    const c = ensureCur()
    commitPendingReasoningRound(c)
    const tail = c.parts.length ? c.parts[c.parts.length - 1] : null
    if (tail && tail.type === "text") tail._streaming = false
    // Sub-agents run asynchronously in the background — the assistant
    // turn that spawned them can complete before they produce a result.
    // Default their status to "running" so a rebuild that walks the
    // event stream before ``subagent_result`` arrives doesn't fall
    // through to the "interrupted" sweep below.  Synchronous tools
    // still default to "done" because their result event almost always
    // immediately follows the call in the stream.
    const initialStatus = kind === "subagent" ? "running" : "done"
    const tool = {
      type: "tool",
      id: curEventKey ? "tool_" + curEventKey : `tool_${_n++}`,
      jobId: jobId || "",
      name,
      kind,
      args: markRaw(args || {}),
      status: initialStatus,
      result: "",
      tools_used: [],
      children: [],
    }
    // Real start time from the persisted event — a rebuild mid-run
    // must not reset the visible elapsed timer to 0.
    if (startedTs) tool.startedAt = startedTs
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
        id: curEventKey ? "tool_" + curEventKey : `tool_${_n++}`,
        name,
        kind: "tool",
        args: markRaw(args || {}),
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
      const isInterrupted = opts?.interrupted || opts?.finalState === "interrupted"
      if (isInterrupted) {
        tc.status = "interrupted"
      } else if (opts?.error) {
        tc.status = "error"
      } else {
        // Successful completion — sub-agents default to "running" in
        // ``addTool`` so a stream walked before ``subagent_result`` isn't
        // wrongly "done"; the terminal flips them to "done" here.
        tc.status = "done"
      }
      if (opts?.tools_used) tc.tools_used = opts.tools_used
      if (opts?.turns != null) tc.turns = opts.turns
      if (opts?.duration != null) tc.duration = opts.duration
      if (opts?.total_tokens != null) tc.total_tokens = opts.total_tokens
      if (opts?.prompt_tokens != null) tc.prompt_tokens = opts.prompt_tokens
      if (opts?.completion_tokens != null) tc.completion_tokens = opts.completion_tokens
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
      id: stableId("compact_"),
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
    curEventKey = typeof evt.event_id === "number" ? `e${evt.event_id}` : null

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
      if (key && _seenUserRender.has(key)) {
        const prior = _seenUserRender.get(key)
        if (prior && typeof evt.pending_id === "string") {
          prior.eventId = evt.pending_id
        }
        if (t === "user_message" && prior && typeof evt.event_id === "number") {
          prior.locator = { eventId: evt.event_id, turnIndex: ti, branchId: bi }
        }
        continue
      }
      cur = null
      const normalized = normalizeMessageContent(evt.content)
      const userMessage = {
        id: stableId("h_"),
        role: "user",
        content: normalized.content,
        contentParts: normalized.contentParts,
        eventId: typeof evt.pending_id === "string" ? evt.pending_id : undefined,
        timestamp: "",
        locator:
          t === "user_message" && typeof evt.event_id === "number"
            ? { eventId: evt.event_id, turnIndex: ti, branchId: bi }
            : null,
      }
      result.push(userMessage)
      if (key) _seenUserRender.set(key, userMessage)
    } else if (t === "user_input_injected") {
      // Mid-turn injection (Feat 3) — the user typed during processing
      // and the backend folded it into the running turn. Distinct
      // event type because it shares (turn, branch) with the trigger
      // that started the turn and would collide with the dedupe above.
      //
      // Order invariant: this bubble lands AFTER the assistant that
      // was streaming when it arrived, and the next text_chunk starts
      // a FRESH assistant after it. Without resetting ``cur``, every
      // subsequent text part would keep folding into the round-1
      // assistant — yielding [user(A), user(B), assistant("to-A to-B")]
      // instead of [user(A), assistant("to-A"), user(B), assistant("to-B")].
      if (cur) {
        for (const p of cur.parts) {
          if (p.type === "text") p._streaming = false
        }
      }
      cur = null
      const normalized = normalizeMessageContent(evt.content)
      result.push({
        id: stableId("h_"),
        role: "user",
        content: normalized.content,
        contentParts: normalized.contentParts,
        eventId: typeof evt.pending_id === "string" ? evt.pending_id : undefined,
        turnIndex: typeof evt?.turn_index === "number" ? evt.turn_index : null,
        injectedMidTurn: true,
        timestamp: "",
      })
    } else if (["ask_text", "confirm", "selection", "card"].includes(t)) {
      const uiEventId = evt.ui_event_id ?? evt.payload?.event_id
      if (!uiEventId) continue
      cur = null
      const message = {
        id: `ui_${uiEventId}`,
        role: "ui_event",
        uiEventType: t,
        eventId: uiEventId,
        payload: evt.payload || {},
        interactive: !!evt.interactive,
        surface: evt.surface || "chat",
        tab,
        timestamp: "",
        replied: false,
        superseded: false,
        timedOut: false,
        repliedActionId: "",
        repliedValues: null,
      }
      result.push(message)
      interactiveMessages.set(uiEventId, message)
    } else if (t === "ui_supersede" || t === "timeout") {
      const uiEventId = evt.ui_event_id ?? evt.payload?.event_id
      const target = interactiveMessages.get(uiEventId)
      if (target && !target.replied) target.superseded = true
    } else if (t === "ui_reply_ack") {
      const uiEventId = evt.ui_event_id ?? evt.payload?.event_id
      const target = interactiveMessages.get(uiEventId)
      if (target && ["accepted", "superseded"].includes(evt.status)) {
        target.superseded = true
      }
    } else if (t === "processing_start") {
      cur = {
        id: stableId("h_"),
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
          id: stableId("h_"),
          role: "trigger",
          content: ch ? `channel: ${ch}${sender ? ` from ${sender}` : ""}` : evt.name,
          triggerContent: evt.content || "",
          channel: ch,
          sender,
          timestamp: "",
        })
      } else if (at === "drive_turn") {
        cur = null
        result.push(_driveTurnMessage(evt, stableId("drv_"), ""))
      } else if (at === "token_usage") {
        if (cur) cur._pendingReasoningCursor = cur.parts.length
      } else if (at === "processing_complete") {
        // skip
      } else if (at === "context_cleared") {
        cur = null
        result.push({
          id: stableId("clear_"),
          role: "clear",
          messagesCleared: evt.messages_cleared || 0,
          timestamp: "",
        })
      } else if (at === "processing_error") {
        cur = null
        result.push({
          id: stableId("err_"),
          role: "error",
          errorType: evt.error_type || "Error",
          content: evt.error || evt.detail || "Unknown error",
          timestamp: "",
        })
      } else if (at === "subagent_start") {
        const tool = addTool(
          evt.name,
          "subagent",
          evt.args || { info: evt.detail },
          evt.job_id,
          _evtStartedTs(evt),
        )
        tool.llm_name = evt.llm_name || ""
        tool.model = evt.model || ""
      } else if (at === "subagent_done") {
        const tool =
          findSubagent(evt.name, evt.job_id) ||
          addTool(evt.name, "subagent", {}, evt.job_id, _evtStartedTs(evt))
        tool.llm_name = evt.llm_name || tool.llm_name || ""
        tool.model = evt.model || tool.model || ""
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
        const tool =
          findSubagent(evt.name, evt.job_id) ||
          addTool(evt.name, "subagent", {}, evt.job_id, _evtStartedTs(evt))
        tool.llm_name = evt.llm_name || tool.llm_name || ""
        tool.model = evt.model || tool.model || ""
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
        addTool(evt.name, "tool", evt.args || { info: evt.detail }, evt.job_id, _evtStartedTs(evt))
      } else if (at === "tool_done") {
        updateTool(
          evt.name,
          evt.result || evt.output || evt.detail,
          { tools_used: evt.tools_used, canvas_preview: evt.canvas_preview },
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
    } else if (t === "activity:wire_inbound") {
      // Persisted output-wiring delivery — shows up on history reload
      // so the user sees the same "Inbound from X" accordion they had
      // live, instead of a mystery "B started processing" with no
      // explanation.
      cur = null
      result.push({
        id: stableId("h_"),
        role: "wire_inbound",
        from: evt.from || evt.detail || "",
        to: evt.to || "",
        preview: evt.content_preview || "",
        withContent: evt.with_content !== false,
        turnIndex: evt.source_turn_index || 0,
        // Cross-site delivery flag — backend sets metadata.cross_node
        // on remote forwards via terrarium.broadcast.  The frontend
        // chips the entry with a "cross-site" badge.
        crossNode: !!(evt.cross_node || evt.metadata?.cross_node),
        timestamp: "",
      })
    } else if (t === "trigger_fired") {
      cur = null
      const ch = evt.channel || ""
      const sender = evt.sender || ""
      result.push({
        id: stableId("h_"),
        role: "trigger",
        content: ch ? `channel: ${ch}${sender ? ` from ${sender}` : ""}` : "",
        triggerContent: evt.content || "",
        channel: ch,
        sender,
        timestamp: "",
      })
    } else if (t === "tool_call") {
      addTool(evt.name, "tool", evt.args || {}, evt.call_id || evt.job_id, _evtStartedTs(evt))
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
          // Backend persists canvas_preview at the top level of the
          // tool_result event row (session/output._handle_tool_done).
          // Forward it so updateTool's resultMeta picks it up — that's
          // what the canvas store later reads (Feat 1).
          canvas_preview: evt.canvas_preview,
          tool_metadata: evt.tool_metadata,
        },
        evt.call_id || evt.job_id,
      )
    } else if (t === "subagent_call") {
      const tool = addTool(
        evt.name,
        "subagent",
        { task: evt.task || "" },
        evt.job_id,
        _evtStartedTs(evt),
      )
      tool.llm_name = evt.llm_name || ""
      tool.model = evt.model || ""
    } else if (t === "subagent_result") {
      const tool =
        findSubagent(evt.name, evt.job_id) ||
        addTool(evt.name, "subagent", {}, evt.job_id, _evtStartedTs(evt))
      tool.llm_name = evt.llm_name || tool.llm_name || ""
      tool.model = evt.model || tool.model || ""
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
        id: stableId("ch_"),
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
    } else if (t === "background_result") {
      cur = null
      // A combined delivery banner carries `labels` (one per folded
      // completion); older single-event frames carry only `label`.
      result.push({
        id: stableId("bgres_"),
        role: "bg_result",
        label: (Array.isArray(evt.labels) ? evt.labels.join(", ") : evt.label) || evt.job_id || "",
        kind: evt.kind || "tool",
        jobId: evt.job_id || "",
        timestamp: "",
      })
    } else if (t === "compact_skipped") {
      // Terminal for a started round that didn't complete — without it
      // the bubble from compact_start spins forever on replay.
      cur = null
      const skipped = findCompactMessage(evt.compact_round || evt.round || 0, true)
      if (skipped) {
        skipped.status = "skipped"
        skipped.reason = evt.reason || ""
      }
    } else if (t === "processing_error") {
      cur = null
      result.push({
        id: stableId("err_"),
        role: "error",
        errorType: evt.error_type || "Error",
        content: evt.error || "",
        timestamp: "",
      })
    } else if (t === "context_cleared") {
      cur = null
      result.push({
        id: stableId("clear_"),
        role: "clear",
        messagesCleared: evt.messages_cleared || 0,
        timestamp: "",
      })
    } else if (t === "assistant_image") {
      // Replay the image into the current assistant message so resumed
      // sessions (and plain history reloads) show it in place. Mirrors
      // the live `_handleAssistantImage` path so shape + meta match.
      const c = ensureCur()
      commitPendingReasoningRound(c)
      for (const p of c.parts || []) {
        if (p.type === "text") p._streaming = false
      }
      c.parts.push({
        type: "image_url",
        id: stableId("img_"),
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
    } else if (t === "assistant_reasoning") {
      const c = ensureCur()
      _insertReasoningSegments(
        c,
        evt._kt_assistant_segments || [],
        typeof evt.event_id === "number" ? `h_${evt.event_id}_r` : "h_reasoning_",
      )
      c._pendingReasoningCursor = undefined
    } else if (t === "token_usage") {
      if (cur) cur._pendingReasoningCursor = cur.parts.length
    } else if (t === "processing_complete") {
      // skip
    }
  }

  // Determine which jobs are still pending (started but no done/error).
  // Mark them as "running" so live WS events can update them.
  const pendingJobs = {}
  for (const [jobId, toolPart] of Object.entries(startedJobs)) {
    if (!completedJobs.has(jobId)) {
      toolPart.status = "running"
      // Prefer the event-derived start; Date.now() only as last resort.
      toolPart.startedAt = toolPart.startedAt || Date.now()
      pendingJobs[jobId] = {
        name: toolPart.name,
        type: toolPart.kind === "subagent" ? "subagent" : "tool",
        startedAt: toolPart.startedAt,
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

  // Sub-agents are async / often background — ``addTool`` defaults
  // their initial status to "running" so a rebuild that walks the
  // stream before the (eventual) ``subagent_result`` arrives leaves
  // them as "running" rather than "done with no result". A missing
  // terminal means *still running*; the backend emits a terminal (real
  // or synthetic) once the job is actually done or interrupted.

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
      if (msg.injectedMidTurn) continue
      const ti = typeof msg.turnIndex === "number" ? msg.turnIndex : userTurnsForResult[userMsgIdx]
      msg.userPosition = userMsgIdx
      userMsgIdx += 1
      // Always stamp turnIndex on the message so the regen / edit
      // buttons can target THIS turn — even when there's no
      // <x/N> navigator yet (single-branch turns). Without this,
      // regenerate falls through to the conversation tail and
      // retries on a non-tail message silently target the last
      // message instead of the clicked one.
      if (typeof ti === "number") {
        msg.turnIndex = ti
        _attachUserNav(msg, ti)
      }
    } else if (msg.role === "assistant") {
      const ti = assistantTurnsForResult[assistantMsgIdx]
      assistantMsgIdx += 1
      if (typeof ti === "number") {
        msg.turnIndex = ti
        _attachAssistantNav(msg, ti)
      }
    }
  }

  return {
    messages: result,
    pendingJobs,
    events,
    branchMetadata: prepared.branchMetadata,
    branchMeta: { byTurn, branchSelection },
  }
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

// ─── Multi-chat-panel group tree helpers (Option E) ──────────────
//
// A *group* is the unit of chat surface — one ``ChatPanel`` instance
// in the workspace. A *groupTree* is a binary split-tree over group
// ids that mirrors the workspace ``LayoutNode`` shape so the same
// rendering pattern applies recursively. ``groupTree`` is per-scope
// and lives entirely in the chat store; the workspace layout tree
// is NOT involved.
//
//   leaf  ->  { type: "leaf", groupId: "g_<n>" }
//   split ->  { type: "split", direction: "horizontal" | "vertical",
//                ratio: 0-100, children: [Node, Node] }
//
// Helpers here are pure (no store mutation, no side effects) so
// vitest can pin each one against synthetic trees. Actions in the
// store mutate via these and persist the result.

/** Depth-first walk over a group tree. ``visit(groupId, path)`` runs
 *  on every leaf in tree-traversal order. ``path`` is an array of
 *  child indices used by the resize-handle path lookup. */
function _walkGroupTree(tree, visit, path = []) {
  if (!tree) return
  if (tree.type === "leaf") {
    visit(tree.groupId, path)
    return
  }
  if (tree.type === "split") {
    _walkGroupTree(tree.children?.[0], visit, [...path, 0])
    _walkGroupTree(tree.children?.[1], visit, [...path, 1])
  }
}

/** Return the first leaf's groupId in tree-traversal order. */
function _firstLeafGroupId(tree) {
  let found = null
  _walkGroupTree(tree, (gid) => {
    if (found == null) found = gid
  })
  return found
}

/** Find the path (array of child indices) to the leaf with the given
 *  groupId. Returns ``null`` if not present. */
function _findLeafPath(tree, groupId, path = []) {
  if (!tree) return null
  if (tree.type === "leaf") return tree.groupId === groupId ? path : null
  if (tree.type === "split") {
    const left = _findLeafPath(tree.children?.[0], groupId, [...path, 0])
    if (left) return left
    return _findLeafPath(tree.children?.[1], groupId, [...path, 1])
  }
  return null
}

/** Split the leaf with id ``targetGroupId`` into a split node. The
 *  new group lands on the side indicated by ``edge`` (``"before"`` =
 *  left/top, ``"after"`` = right/bottom). Returns a new tree.
 *  No-op if ``targetGroupId`` is not present. */
function _splitTreeLeaf(tree, targetGroupId, direction, edge, newGroupId) {
  if (!tree) return tree
  if (tree.type === "leaf") {
    if (tree.groupId !== targetGroupId) return tree
    const movedLeaf = { type: "leaf", groupId: newGroupId }
    const keptLeaf = { type: "leaf", groupId: tree.groupId }
    const children = edge === "before" ? [movedLeaf, keptLeaf] : [keptLeaf, movedLeaf]
    return { type: "split", direction, ratio: 50, children }
  }
  if (tree.type === "split") {
    return {
      ...tree,
      children: [
        _splitTreeLeaf(tree.children?.[0], targetGroupId, direction, edge, newGroupId),
        _splitTreeLeaf(tree.children?.[1], targetGroupId, direction, edge, newGroupId),
      ],
    }
  }
  return tree
}

/** Remove the leaf with the given groupId, collapsing the surviving
 *  sibling into the parent's slot. Returns the new tree (possibly
 *  ``null`` if the last leaf was removed). */
function _pruneTreeLeaf(tree, groupId) {
  if (!tree) return null
  if (tree.type === "leaf") {
    return tree.groupId === groupId ? null : tree
  }
  if (tree.type === "split") {
    const left = _pruneTreeLeaf(tree.children?.[0], groupId)
    const right = _pruneTreeLeaf(tree.children?.[1], groupId)
    if (!left && !right) return null
    if (!left) return right
    if (!right) return left
    return { ...tree, children: [left, right] }
  }
  return tree
}

/** Mutate a split's ``ratio`` at the given path, IN PLACE.
 *
 *  Why in-place: during a splitter drag, the ratio updates on every
 *  pointermove. If we replaced ``groupTree`` with a fresh object on
 *  every move, Vue would unmount + remount the recursive
 *  ``ChatGroupNode`` tree — the in-flight pointer capture lives on
 *  the OLD handle's DOM node, so the drag would break after the
 *  first move. Mutating in place keeps the DOM stable and the
 *  pointer capture intact. Pinia's reactive proxy still detects the
 *  mutation on the nested object, so the ``:style`` bindings update.
 *
 *  Silent no-op if the path doesn't point at a split node. */
function _setSplitRatio(tree, path, ratio) {
  if (!tree || !Array.isArray(path)) return
  if (path.length === 0) {
    if (tree.type !== "split") return
    tree.ratio = Math.max(10, Math.min(90, ratio))
    return
  }
  if (tree.type !== "split") return
  const [idx, ...rest] = path
  if (idx !== 0 && idx !== 1) return
  _setSplitRatio(tree.children?.[idx], rest, ratio)
}

/** Union of all tabs across the tree, in stable tree-traversal order,
 *  de-duplicated. */
function _unionGroupTabs(groups, tree) {
  if (!tree) return []
  const out = []
  const seen = new Set()
  _walkGroupTree(tree, (gid) => {
    const g = groups?.[gid]
    if (!g || !Array.isArray(g.tabs)) return
    for (const t of g.tabs) {
      if (seen.has(t)) continue
      seen.add(t)
      out.push(t)
    }
  })
  return out
}

/** Storage key for the per-scope group state. ``scope`` is the
 *  store's ``_instanceId`` (creature_id or terrarium_id), or
 *  ``"default"`` for the v1 singleton path. */
function _groupStorageKey(scope) {
  return `kt.chat.groupTree.${scope || "default"}`
}

/**
 * Chat store options. The same options block is fed into a Pinia
 * factory below — one store per scope id (creature_id /
 * terrarium_id) — so multiple ``AttachTab`` components in the macro
 * shell can each own their own messages, WebSocket, and tab state
 * without colliding on the shared singleton that v1 used. See
 * ``composables/useScope.js`` for the lifecycle contract.
 */
const _chatStoreOptions = {
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
    /**
     * Latest CUMULATIVE token snapshot per sub-agent ``job_id``. The
     * backend emits ``subagent_token_update`` / ``subagent_result`` as
     * running totals for one job (never per-turn deltas) and NEVER folds
     * a sub-agent's spend into its parent creature's ``token_usage``, so
     * the session total is ``sum(tokenUsage) + sum(subagentUsageByJob)``
     * with no double-count. Keyed by job so re-emitted snapshots replace
     * (not add) — see ``sessionTokenTotals`` (UXI-03).
     * @type {Object<string, {prompt: number, completion: number, total: number, cached: number}>}
     */
    subagentUsageByJob: {},
    /** @type {Object<string, {name: string, type: string, startedAt: number}>} Running background jobs */
    runningJobs: {},
    /** @type {Object<string, number>} Unread message counts per tab */
    unreadCounts: {},
    attentionByTab: {},
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
    /** Per-tab branch operation state for regenerate/edit UX. */
    branchOperationByTab: {},
    /** Last branch operation error, keyed by tab. */
    branchOperationErrorByTab: {},
    commandInventoryByTab: {},
    commandInventoryRevisionByTab: {},
    _commandInventoryFetchedAtByTab: {},
    _commandInventoryGenerationByTab: {},
    _commandInventoryRequestByTab: {},
    _slashTargetByTab: {},
    _localCommandResultsByTab: {},
    _pendingCommandResultContextsByTab: {},
    _commandResultDispatchSeq: 0,

    /** Primary creature session metadata, including runtime and config identities. */
    sessionInfo: {
      sessionId: "",
      model: "",
      llmName: "",
      agentName: "",
      configName: "",
      configRef: "",
      compactThreshold: 0,
      // Lab cluster site that hosts this session ("_host" or
      // worker-id). Set from the session payload at attach time.
      homeNode: "_host",
    },
    /**
     * Per-creature model info, keyed by tab/source name (WS
     * ``session_info`` events carry the emitting creature's name as
     * ``source``). ``sessionInfo`` above only tracks the PRIMARY
     * creature — before this map existed, every display surface read
     * the global object, so switching to another creature's tab kept
     * showing the primary's model.
     * @type {Object<string, {model: string, llmName: string, configName: string, configRef: string, maxContext: number, compactThreshold: number}>}
     */
    modelByTab: {},
    /** Real creature name behind the ``root`` tab alias (recipe
     *  terrariums), so source-keyed WS events also refresh the
     *  ``root`` entry. @type {string | null} */
    _rootSourceName: null,
    /** Reactive tick counter - incremented every second when jobs are running */
    _jobTick: 0,
    /** @type {number | null} */
    _jobTimer: null,
    /** @type {string | null} */
    _instanceId: null,
    /** Canonical graph id for the active instance. For terrarium-typed
     *  instances this is the routing key for ``terrariumAPI`` and the
     *  ``/ws/sessions/{gid}/creatures/{target}/chat`` URL. For solo
     *  creatures it equals ``_instanceId``. Tracked separately so
     *  ``/instances/<creature_id>`` URLs keep working after the
     *  graph grows past one member (the URL id is the creature id,
     *  but the routing id must be the graph id). */
    _instanceGraphId: null,
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
    /**
     * Per-tab user-message queue. Messages submitted while the target
     * tab is mid-stream sit here until ``_promoteQueuedMessages`` flushes
     * them into the main message list — and they MUST be tab-scoped so
     * typing into tab A while tab A's agent is busy does not show a
     * "queued" banner on tabs B/C the user happens to look at.
     * @type {Object<string, Array<{id: string, content: string, timestamp: string}>>}
     */
    queuedMessagesByTab: {},
    /** @type {number} Monotonic token to ignore stale history/WS callbacks after instance switches */
    _instanceGeneration: 0,
    /** @type {Record<string, number>} Recent user message signatures for cross-tab dedupe */
    _recentUserInputs: {},
    /** @type {Record<string, {active: boolean, expectedBranchByTurn?: Record<string, number>}>} Tabs needing canonical replay after regen/edit streaming finishes */
    _branchResyncPendingByTab: {},
    /** @type {Record<string, number>} Debounce timers for post-branch history resync */
    _branchResyncTimers: {},
    /** @type {Record<string, number>} Per-tab monotonic history-request id; a resync whose id is stale when it resolves has been superseded */
    _historyRequestSeqByTab: {},
    /** @type {Record<string, number>} Per-tab live/optimistic mutation generation invalidating in-flight snapshots */
    _historyMutationSeqByTab: {},
    /**
     * Per-tab raw text seen on the current turn's WS stream. Deliberately
     * branch-agnostic — notification summaries describe the turn that just
     * completed, not the branch the user is viewing — and reset on each
     * ``processing_start`` so a text-less turn can never surface a stale
     * preview from an earlier turn.
     */
    _attentionStreamTextByTab: {},
    /** @type {Record<string, number>} Per-tab watermark: max event_id last applied by a resync; a lower-max response is stale (branch ops exempt) */
    _appliedMaxEventIdByTab: {},
    /**
     * Per-tab streaming target — the (turn_index, branch_id) the
     * backend is currently writing to. Set by ``regenerateLastResponse`` /
     * ``editMessage`` BEFORE the new chunks land, cleared on
     * ``processing_end``. Two consumers:
     *
     * 1. WS frame routing — text / tool_* / subagent_* frames carry a
     *    ``branch_id`` (backend-tagged in studio/attach/_event_stream.py).
     *    Frames whose ``branch_id`` doesn't match the user's currently-
     *    viewed branch for the streaming turn are dropped from the live
     *    mutation path — they would otherwise corrupt the sibling
     *    branch the user actually has on screen. The events are still
     *    persisted by SessionOutput, so a switch back to the streaming
     *    branch resyncs the missed content.
     *
     * 2. KohakUwUing visibility — only shown when the viewed branch IS
     *    the one currently generating; the indicator follows the
     *    running branch, not the tab.
     * @type {Record<string, {turnIndex: number, branchId: number} | null>}
     */
    _streamingBranchByTab: {},

    // ── Multi-chat-panel state (Option E) ───────────────────────
    //
    // When ``groupTree`` is ``null`` the chat panel runs in legacy
    // single-group mode and reads/writes ``tabs``/``activeTab``
    // directly — visually byte-identical to pre-Option-E. Once the
    // user explicitly splits (or enables groups), ``groupTree`` is
    // populated and ``ChatPanelContainer`` switches to rendering the
    // recursive ``ChatGroupNode``. Every group-mutating action also
    // syncs ``tabs`` / ``activeTab`` for the legacy readers (chat
    // store internals, ``SessionHistoryViewer``, scripted-history
    // viewer, etc.) so the two stay in lock-step.
    //
    // Persisted per-scope to ``localStorage[kt.chat.groupTree.<scope>]``.

    /**
     * @type {Record<string, { tabs: string[], activeTab: string | null, draftText: string }>}
     */
    groups: {},
    /** @type {object | null} */
    groupTree: null,
    /** @type {string | null} */
    focusedGroupId: null,
    /** Monotonic counter for synthesising new group ids. Persisted. */
    _groupCounter: 0,
  }),

  getters: {
    currentMessages: (state) => {
      if (!state.activeTab) return []
      return state.messagesByTab[state.activeTab] || []
    },
    /**
     * Session token totals INCLUDING sub-agents: every creature's own
     * usage (``tokenUsage``, parent-controller LLM only) plus each
     * sub-agent job's latest cumulative snapshot. The two are disjoint
     * on the backend, so this sums each exactly once (UXI-03).
     */
    sessionTokenTotals: (state) => {
      const totals = { prompt: 0, completion: 0, cached: 0, total: 0 }
      for (const u of Object.values(state.tokenUsage || {})) {
        totals.prompt += u.prompt || 0
        totals.completion += u.completion || 0
        totals.cached += u.cached || 0
        totals.total += u.total || 0
      }
      for (const u of Object.values(state.subagentUsageByJob || {})) {
        totals.prompt += u.prompt || 0
        totals.completion += u.completion || 0
        totals.cached += u.cached || 0
        totals.total += u.total || 0
      }
      return totals
    },
    hasRunningJobs: (state) => Object.keys(state.runningJobs).length > 0,
    // Jobs owned by one tab must not light up indicators / stop
    // buttons in every other tab. Legacy entries without a tab stamp
    // stay visible everywhere.
    runningJobCountForTab: (state) => (tab) =>
      Object.values(state.runningJobs).filter((j) => !j.tab || !tab || j.tab === tab).length,
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
     * Per-tab queued-message accessor. Templates that previously read
     * ``chat.queuedMessages`` should switch to ``chat.activeQueuedMessages``
     * so the "Queued" banner only appears on the tab where the message
     * is waiting — typing into tab A's busy stream must not surface a
     * banner on tab B.
     */
    activeQueuedMessages: (state) => {
      const tab = state.activeTab
      if (!tab) return []
      return state.queuedMessagesByTab[tab] || []
    },
    /**
     * True when the active tab is currently generating AND the user is
     * viewing the very branch that's being generated. The "KohakUwUing..."
     * label binds to this — a regen / edit-rerun that opens branch 2 is
     * still generating in the background when the user clicks <1/2> to
     * see the old branch, but the label belongs to branch 2 (the running
     * one), not the bubble on screen. Without this gate the label would
     * mis-attach to whichever branch happens to be visible.
     *
     * Defaults to plain ``processing`` when no streaming-branch target
     * is set (legacy bursts, tail regens that don't carry branch info).
     */
    viewingRunningBranch: (state) => {
      const tab = state.activeTab
      if (!tab || !state.processingByTab[tab]) return false
      const target = state._streamingBranchByTab[tab]
      if (!target) return true
      const view = state.branchViewByTab[tab]
      // No explicit override means "latest branch" — the latest branch
      // for the streaming turn IS the streaming branch, since the
      // backend always opens a fresh max_branch+1. So an unset view
      // implies the user is on the running branch.
      if (!view || !Object.prototype.hasOwnProperty.call(view, target.turnIndex)) {
        return true
      }
      return view[target.turnIndex] === target.branchId
    },
    /**
     * Model info for the ACTIVE tab's creature, falling back to the
     * session-level (primary creature) values when the per-tab entry
     * hasn't been populated yet. Numeric fields treat 0 as unknown.
     */
    activeCreatureInfo(state) {
      const tab = state.activeTab
      const info = (tab && state.modelByTab[tab]) || {}
      return {
        model: info.model || state.sessionInfo.model || "",
        llmName: info.llmName || state.sessionInfo.llmName || "",
        configName: info.configName || state.sessionInfo.configName || "",
        configRef: info.configRef || state.sessionInfo.configRef || "",
        maxContext: info.maxContext || state.sessionInfo.maxContext || 0,
        compactThreshold: info.compactThreshold || state.sessionInfo.compactThreshold || 0,
      }
    },
    activeModelInfo() {
      return this.activeCreatureInfo
    },
    /**
     * Canonical display form of the ACTIVE tab's model, preferring the
     * ``provider/name[@variations]`` identifier so every display surface
     * shows the same string the user types into ``/model``. Falls back
     * to the raw API model id when ``llm_name`` hasn't been populated
     * yet (very first moments before the session_info event arrives).
     */
    modelDisplay() {
      const info = this.activeModelInfo
      return info.llmName || info.model || ""
    },
    /** Active creature target for per-creature endpoints. Returns the
     *  active tab name when the user is on a creature tab (not a
     *  channel tab). Used by side panels (scratchpad, env, …) to
     *  route to the correct creature within the session. */
    terrariumTarget: (state) => {
      const tab = state.activeTab
      if (!tab || tab.startsWith("ch:")) return null
      return tab
    },
    /** True when the chat surface is running in multi-group mode (the
     *  user explicitly split or enabled groups). When false the chat
     *  panel runs in legacy single-group mode. */
    groupsActive: (state) => state.groupTree != null,
    /** The currently-focused group's record, or ``null``. */
    focusedGroup: (state) => state.groups[state.focusedGroupId] || null,
  },

  actions: {
    _setEvents(tab, events) {
      if (!tab) return
      this.eventsByTab[tab] = markRaw(Array.isArray(events) ? events : [])
    },
    markAttentionRead(tabKey) {
      if (!this.attentionByTab[tabKey]) return
      this.attentionByTab[tabKey] = markAttentionRead(this.attentionByTab[tabKey])
      publishAttention(scopeOfStoreId(this.$id) || "default", tabKey, this.attentionByTab[tabKey])
    },
    attentionSummary(tabKey) {
      return attentionSummary(this.attentionByTab[tabKey] || createAttentionState())
    },
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

    initForInstance(instance, options = {}) {
      const { initialTab = null } = options
      // A solo creature that grew via group_add_node is reported by
      // the backend as ``terrarium`` after the second creature joins.
      // Without this type-change check the chat store stays in
      // creature mode forever — which is why the chat panel never
      // surfaces the new peers' tabs even though the polling loop
      // has the up-to-date ``instance`` shape.
      const typeChanged = this._instanceId === instance.id && this._instanceType !== instance.type
      // Preserve the previously-focused tab across any re-init (type
      // flip OR canonical-id swap). When a solo creature gains a peer,
      // the URL/tab handle stays as the creature_id but the canonical
      // ``instance.id`` flips to the graph_id — both code paths reach
      // the full re-init below, and in both cases the user is still
      // chatting with the original creature; defaulting to ``creatures[0]``
      // (sorted by creature_id) would yank focus to whichever creature
      // happens to sort first.
      const willReInit = !this._ws || this._instanceId !== instance.id || typeChanged
      const preservedActiveTab = willReInit ? this.activeTab : null
      if (this._instanceId === instance.id && this._ws && !typeChanged) {
        // Already inited; just switch the active tab if a hint was
        // passed (used by the graph editor's "open chat" on inner
        // creatures so the right sub-tab pops up without a reload).
        if (initialTab && initialTab !== this.activeTab) {
          this._addTab(initialTab)
          this.activeTab = initialTab
          this._saveTabs()
          this._loadHistory(initialTab)
        }
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
      this._instanceGraphId = instance.graph_id || instance.id
      this._instanceType = instance.type
      this.tabs = []
      this.messagesByTab = {}
      this.tokenUsage = {}
      this.subagentUsageByJob = {}
      this.runningJobs = {}
      this.unreadCounts = {}
      this.attentionByTab = {}
      this._attentionStreamTextByTab = {}
      removeAttentionScope(scopeOfStoreId(this.$id) || "default")
      this.queuedMessagesByTab = {}
      this.processingByTab = {}
      this._recentUserInputs = {}
      this._branchResyncPendingByTab = {}
      this._streamingBranchByTab = {}
      this.commandInventoryByTab = {}
      this.commandInventoryRevisionByTab = {}
      this._commandInventoryFetchedAtByTab = {}
      this._commandInventoryGenerationByTab = {}
      this._commandInventoryRequestByTab = {}
      this._slashTargetByTab = {}
      this._localCommandResultsByTab = {}
      this._pendingCommandResultContextsByTab = {}
      this._commandResultDispatchSeq = 0
      this._clearBranchResyncTimers()
      // Reset multi-group state — group tree is per-scope, so a
      // different ``_instanceId`` means a different layout to load.
      // ``ChatPanelContainer`` calls ``_loadGroupState()`` on mount
      // after this re-init lands, so saved state for the new scope
      // is restored without races against the legacy ``_addTab``
      // calls below.
      this.groups = {}
      this.groupTree = null
      this.focusedGroupId = null
      this._groupCounter = 0
      this.sessionInfo = {
        sessionId: instance.session_id || instance.id || "",
        model: instance.model || "",
        llmName: instance.llm_name || instance.model || "",
        agentName: instance.creatures?.[0]?.name || "",
        configName: instance.creature_config_name || instance.creatures?.[0]?.config_name || "",
        configRef: instance.config_ref || instance.creatures?.[0]?.config_ref || "",
        compactThreshold: instance.compact_threshold || 0,
        maxContext: instance.max_context || 0,
        // Cluster site this session runs on.  Backend payload now
        // carries ``home_node`` at both the session and per-creature
        // level (lifecycle.py).  Default ``_host`` keeps standalone
        // semantics intact.
        homeNode: instance.home_node || instance.creatures?.[0]?.home_node || "_host",
      }

      // Seed per-creature model info from the instance payload so
      // non-primary tabs show THEIR creature's model before any WS
      // session_info event arrives. The ``root`` tab aliases the
      // privileged creature; mirror its entry and remember the real
      // name so source-keyed WS events refresh both.
      this.modelByTab = {}
      this._rootSourceName = null
      for (const c of instance.creatures || []) {
        if (!c?.name) continue
        const info = {
          model: c.model || "",
          llmName: c.llm_name || c.model || "",
          configName: c.config_name || "",
          configRef: c.config_ref || "",
          maxContext: c.max_context || 0,
          compactThreshold: c.compact_threshold || 0,
        }
        this.modelByTab[c.name] = info
        if (c.is_root && instance.has_root) {
          this._rootSourceName = c.name
          this.modelByTab["root"] = { ...info }
        }
      }

      // Reset status store too. Actions run detached from any Vue
      // setup context, so ``injectScope()`` would return null —
      // recover scope from this store's ``$id`` (registered as
      // ``chat:<scope>``) and pass it explicitly.
      const statusStore = useStatusStore(scopeOfStoreId(this.$id))
      statusStore.reset()

      // Unified session connect: every session has a session_id and
      // a creatures[] roster. For solo sessions tabs[0] is the
      // creature's name; for recipe-built terrariums it's "root" (or
      // creatures[0].name when no root). The WS endpoint
      // ``/ws/sessions/{sid}/creatures/{target}/chat`` works for
      // both — there is no agent-vs-terrarium fork.
      let defaultTab = null
      if (instance.has_root) {
        defaultTab = "root"
      } else if (instance.creatures && instance.creatures.length > 0) {
        defaultTab = instance.creatures[0].name
      } else if (instance.channels && instance.channels.length > 0) {
        defaultTab = `ch:${instance.channels[0].name}`
      } else {
        defaultTab = instance.config_name
      }
      if (defaultTab) this._addTab(defaultTab)
      if (initialTab && initialTab !== defaultTab) {
        this._addTab(initialTab)
        this.activeTab = initialTab
      }
      this._connectTerrarium(this._instanceGraphId, generation)

      // Restore saved tabs/active tab for this instance
      this._restoreTabs()
      // After a type-change re-init, keep the user on the creature
      // they were chatting with (still present in the new graph
      // roster). Make sure the tab exists before we activate it —
      // ``_restoreTabs`` may not include it if the user never
      // explicitly switched tabs in the solo phase.
      if (preservedActiveTab && !preservedActiveTab.startsWith("ch:")) {
        const stillThere = (instance.creatures || []).some((c) => c.name === preservedActiveTab)
        if (stillThere) {
          this._addTab(preservedActiveTab)
          this.activeTab = preservedActiveTab
          this._saveTabs()
        }
      }
      if (!this.activeTab) this.activeTab = this.tabs[0] || null
    },

    openTab(tabKey) {
      this._addTab(tabKey)
      // When groups are active, the chat view renders from the per-group
      // active tab (``groups[gid].activeTab``); the legacy ``activeTab``
      // is DERIVED from the focused group by ``_syncLegacyFromGroups``.
      // ``_addTab`` only sets a group's activeTab when it was empty, so
      // clicking an already-open creature (CreaturesPanel / StatusDashboard
      // / AttachTab) wouldn't switch the visible chat. Drive the owning
      // group's focus + active tab explicitly, mirroring
      // ``ChatPanel.onTabClick``, so every "open tab" entry point switches.
      if (this.groupTree) {
        let targetId = this.focusedGroupId || _firstLeafGroupId(this.groupTree)
        for (const [gid, g] of Object.entries(this.groups)) {
          if (g.tabs.includes(tabKey)) {
            targetId = gid
            break
          }
        }
        if (targetId && this.groups[targetId]?.tabs.includes(tabKey)) {
          this.setFocusedGroup(targetId)
          this.setGroupActiveTab(targetId, tabKey)
        }
      }
      this.activeTab = tabKey
      this._saveTabs()
      // Always load history — the unified session endpoint handles
      // both creature tabs (target=creature_name) and channel tabs
      // (target=``ch:<channel>``).
      this._loadHistory(tabKey)
    },

    _addTab(key) {
      if (!this.tabs.includes(key)) {
        this.tabs.push(key)
        this._setMessages(key, [])
      }
      // When groups are active, also drop the tab into the focused
      // group so backend ``creature_added`` events surface in the
      // group the user is currently looking at. The ``_syncLegacy…``
      // call at the end re-derives ``tabs`` from groups, but since
      // we already pushed above the union order matches the legacy
      // append. No-op if already present in any group.
      if (this.groupTree) {
        let present = false
        for (const g of Object.values(this.groups)) {
          if (g.tabs.includes(key)) {
            present = true
            break
          }
        }
        if (!present) {
          const targetId = this.focusedGroupId || _firstLeafGroupId(this.groupTree)
          if (targetId && this.groups[targetId]) {
            this.groups[targetId].tabs.push(key)
            if (!this.groups[targetId].activeTab) {
              this.groups[targetId].activeTab = key
            }
          }
        }
        this._syncLegacyFromGroups()
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
      // Mirror removal into groups when active. ``pruneTab`` already
      // syncs legacy state — we've done that ourselves above, so call
      // it after the legacy mutation to keep groups authoritative.
      if (this.groupTree) {
        // Snapshot ids to avoid mutation during iteration.
        const ids = Object.keys(this.groups)
        for (const gid of ids) {
          const g = this.groups[gid]
          if (!g) continue
          const i2 = g.tabs.indexOf(tab)
          if (i2 === -1) continue
          g.tabs.splice(i2, 1)
          if (g.activeTab === tab) g.activeTab = g.tabs[0] || null
          if (g.tabs.length === 0) this.removeGroup(gid)
        }
        this._syncLegacyFromGroups()
        this._persistGroupState()
      }
    },

    setActiveTab(tab) {
      this.activeTab = tab
      if (tab) delete this.unreadCounts[tab]
      this._saveTabs()
      // Mirror to the focused group when groups are active so the
      // status bar / model switcher / per-group reads stay in sync.
      // Only updates the focused group; a different group's
      // ``activeTab`` is unaffected (use ``setGroupActiveTab`` for
      // explicit per-group changes).
      if (this.groupTree && this.focusedGroupId) {
        const g = this.groups[this.focusedGroupId]
        if (g && tab && g.tabs.includes(tab) && g.activeTab !== tab) {
          g.activeTab = tab
          this._persistGroupState()
        }
      }
      // Lazy-load history for any newly-focused empty tab. The
      // session endpoint accepts ``(session_id, creature_name)``
      // for both solo and multi-creature sessions, so there's
      // no need to fork by instance type here.
      if (tab) {
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
      const generation = this._instanceGeneration

      try {
        // Interrupt the main agent processing only.
        // Background jobs (sub-agents, background tools) are NOT cancelled —
        // they have their own lifecycle and must be stopped individually
        // via stopTask() from the running jobs panel.
        if (this.processingByTab[target]) {
          // Unified routing: every session has a session_id (graph_id)
          // and creatures keyed by name. ``interruptCreature`` works
          // for both solo (1-creature graph) and multi-creature.
          await terrariumAPI.interruptCreature(this._instanceGraphId, target)
          if (generation === this._instanceGeneration) {
            await this._resyncHistory(target, { generation })
          }
        }
        // Do NOT mark running parts as interrupted or remove running jobs.
        // The backend will send proper done/error events when jobs complete.
      } catch (err) {
        console.error("Interrupt failed:", err)
      }
    },

    async loadCommandInventory(tab, { force = false } = {}) {
      if (!tab || tab.type === "channel") return { commands: [], skills: [] }
      const tabKey = tab.key
      const cached = this.commandInventoryByTab[tabKey]
      const fetchedAt = this._commandInventoryFetchedAtByTab[tabKey] || 0
      if (!force && cached && Date.now() - fetchedAt < COMMAND_INVENTORY_TTL_MS) return cached
      if (!force && this._commandInventoryRequestByTab[tabKey]) {
        return this._commandInventoryRequestByTab[tabKey]
      }
      if (force) {
        this._commandInventoryGenerationByTab[tabKey] =
          (this._commandInventoryGenerationByTab[tabKey] || 0) + 1
      }
      const generation = this._commandInventoryGenerationByTab[tabKey] || 0
      const instanceGeneration = this._instanceGeneration
      const sessionId = this._instanceGraphId || this._instanceId
      const creature = tab.creature || tabKey
      const request = terrariumAPI
        .getCreatureCommandInventory(sessionId, creature)
        .then((inventory) => {
          const tabStillOpen =
            this.tabs.includes(tabKey) ||
            Object.values(this.groups).some((group) => group.tabs.includes(tabKey))
          const sessionStillOpen =
            this._instanceGeneration === instanceGeneration &&
            (this._instanceGraphId || this._instanceId) === sessionId
          if ((this._commandInventoryGenerationByTab[tabKey] || 0) !== generation) {
            const replacement = this._commandInventoryRequestByTab[tabKey]
            if (replacement && replacement !== request) return replacement
            if (sessionStillOpen && tabStillOpen) return this.loadCommandInventory(tab)
            return inventory
          }
          if (!sessionStillOpen || !tabStillOpen) {
            return inventory
          }
          this.commandInventoryByTab[tabKey] = inventory
          this._commandInventoryFetchedAtByTab[tabKey] = Date.now()
          this.commandInventoryRevisionByTab[tabKey] =
            (this.commandInventoryRevisionByTab[tabKey] || 0) + 1
          return inventory
        })
        .finally(() => {
          if (this._commandInventoryRequestByTab[tabKey] === request) {
            delete this._commandInventoryRequestByTab[tabKey]
          }
        })
      this._commandInventoryRequestByTab[tabKey] = request
      return request
    },

    invalidateCommandInventory(tab = null) {
      const invalidate = (tabKey) => {
        this._commandInventoryGenerationByTab[tabKey] =
          (this._commandInventoryGenerationByTab[tabKey] || 0) + 1
        delete this._commandInventoryFetchedAtByTab[tabKey]
        delete this._commandInventoryRequestByTab[tabKey]
      }
      if (tab) {
        invalidate(typeof tab === "string" ? tab : tab.key)
        return
      }
      const tabKeys = new Set([
        ...Object.keys(this.commandInventoryByTab),
        ...Object.keys(this._commandInventoryFetchedAtByTab),
        ...Object.keys(this._commandInventoryGenerationByTab),
        ...Object.keys(this._commandInventoryRequestByTab),
      ])
      for (const tabKey of tabKeys) invalidate(tabKey)
      this._commandInventoryFetchedAtByTab = {}
      this._commandInventoryRequestByTab = {}
    },

    markSlashTarget(tab, entry) {
      if (!tab) return
      const tabKey = typeof tab === "string" ? tab : tab.key
      if (entry) {
        this._slashTargetByTab[tabKey] = {
          type: entry.type || entry.kind,
          name: entry.name,
        }
      } else delete this._slashTargetByTab[tabKey]
    },

    async prepareSlashSend(tab, content) {
      const parsed = _parseSlashCommand(content)
      if (!parsed || !tab || tab.type === "channel") return null
      const marked = this._slashTargetByTab[tab.key]
      if (marked && marked.name.toLowerCase() === parsed.command) return marked
      const inventory = await this.loadCommandInventory(tab)
      const commands = inventory.commands || []
      const command = commands.find(
        (entry) =>
          entry.name.toLowerCase() === "goal" &&
          (entry.name.toLowerCase() === parsed.command ||
            entry.aliases?.some((alias) => alias.toLowerCase() === parsed.command)),
      )
      if (command) return { type: "command", name: command.name }
      const commandNamespace = new Set(
        commands.flatMap((entry) => [
          entry.name.toLowerCase(),
          ...(entry.aliases || []).map((alias) => alias.toLowerCase()),
        ]),
      )
      if (commandNamespace.has(parsed.command)) return null
      const skill = inventory.skills?.find(
        (entry) =>
          entry.enabled && !entry.invocation_blocked && entry.name.toLowerCase() === parsed.command,
      )
      return skill ? { type: "skill", name: skill.name } : null
    },

    async send(text) {
      if (!this.activeTab) return
      if (typeof text === "string" ? !text.trim() : !text.length) return

      const tab = this.activeTab
      const slashCommand = _parseSlashCommand(text)
      if (slashCommand && !tab.startsWith("ch:")) {
        const tabInfo = this.tabs[tab]
        const target = this._slashTargetByTab[tab]
        delete this._slashTargetByTab[tab]
        const isGoal =
          slashCommand.command === "goal" &&
          (!target ||
            (target.type === "command" && target.name.toLowerCase() === slashCommand.command))
        if (isGoal) {
          const result = await terrariumAPI.executeCreatureCommand(
            this._instanceGraphId || this._instanceId,
            tabInfo?.creature || tab,
            "goal",
            slashCommand.args,
          )
          this.invalidateCommandInventory(tab)
          return { handled: "command", result }
        }
      }
      if (!this._ws) return

      const now = Date.now()
      const contentParts = typeof text === "string" ? [{ type: "text", text }] : text
      const normalized = normalizeMessageContent(contentParts)
      const signature = contentSignature(contentParts)
      // Client-minted stable id so a message that gets buffered mid-turn
      // can be edited / cancelled by id (UXI-08a). The backend echoes it
      // as the pending id; the ``input_queued`` ack confirms the slot.
      const eventId = `c_${now}_${Math.random().toString(36).slice(2, 8)}`
      const msg = {
        id: "u_" + now,
        role: "user",
        content: normalized.content,
        contentParts: normalized.contentParts,
        eventId,
        timestamp: new Date(now).toISOString(),
      }

      this._recentUserInputs[`${tab}:${signature}`] = now
      if (this.processingByTab[tab]) {
        // Don't put in main chat — hold in this tab's queue, shown
        // above the input box. The queue is per-tab so a busy stream
        // on tab A never surfaces a "queued" banner on tab B (Bug 3).
        msg.queued = true
        msg.queuedTab = tab
        if (!this.queuedMessagesByTab[tab]) this.queuedMessagesByTab[tab] = []
        this.queuedMessagesByTab[tab].push(msg)
      } else {
        this._addMsg(tab, msg)
      }

      if (tab.startsWith("ch:")) {
        const chName = tab.slice(3)
        try {
          await terrariumAPI.sendToChannel(this._instanceGraphId, chName, contentParts, "human")
        } catch (err) {
          console.error("Channel send failed:", err)
        }
      } else {
        const target = tab
        if (this._ws.readyState === WebSocket.OPEN) {
          this._ws.send(
            JSON.stringify({ type: "input", target, content: contentParts, event_id: eventId }),
          )
          // Flip processing optimistically — the backend's
          // processing_start event will confirm it; this ensures the
          // indicator and interrupt button appear immediately on the
          // correct tab even before the first chunk arrives.
          this.processingByTab[target] = true
        }
      }
    },

    async _loadHistory(target, generation = this._instanceGeneration) {
      return this._resyncHistory(target, { generation, suppressErrors: true, initialLoad: true })
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
      // Bind the WS to whichever tab was added first — the backend
      // routes per-message ``target`` so any sub-tab can drive its
      // own creature over this connection.
      const target = this.tabs[0] || "root"
      this._openWs({
        generation,
        url: wsUrl(`/ws/sessions/${terrariumId}/creatures/${target}/chat`),
        onOpen: () => {
          // Load history for the *active* tab and the WS-bound tab
          // (tabs[0]) plus every channel tab. After a typeChanged
          // re-init the active tab may not be tabs[0] (we restore
          // the user's pre-upgrade focus), so loading only tabs[0]
          // would leave the active tab empty until the user clicks
          // away and back.
          const loads = []
          const seen = new Set()
          const enqueue = (tab) => {
            if (!tab || seen.has(tab)) return
            seen.add(tab)
            loads.push(this._loadHistory(tab, generation))
          }
          if (this.tabs[0]) enqueue(this.tabs[0])
          if (this.activeTab) enqueue(this.activeTab)
          for (const tab of this.tabs) {
            if (tab.startsWith("ch:")) enqueue(tab)
          }
          Promise.all(loads)
            .catch((err) => console.error("Terrarium history load failed:", err))
            .finally(() => this._flushWsBuffer(generation))
        },
        reconnect: () => this._connectTerrarium(terrariumId, generation),
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
        const wasOpen = this.wsStatus === "open"
        this.wsStatus = "reconnecting"
        for (const tab of Object.keys(this.branchOperationByTab)) {
          this._failBranchOperation(tab, "Connection closed before the operation completed.")
        }
        // First disconnect (was-open → reconnecting): if this session
        // is on a worker, surface a one-shot toast + mark the cluster
        // site offline.  We only fire on the FIRST close — subsequent
        // reconnect cycles don't re-spam.
        if (wasOpen) {
          this._notifyWorkerDisconnect()
        }
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
        for (const tab of Object.keys(this.branchOperationByTab)) {
          this._failBranchOperation(tab, "Connection lost before the operation completed.")
        }
      }
    },

    /**
     * Surface a one-shot toast when the WS drops for a worker-hosted
     * session.  Only fires when the cluster store is in lab-host mode
     * and the home site is not the host itself.  No-op in standalone.
     */
    _notifyWorkerDisconnect() {
      const home = this.sessionInfo?.homeNode || "_host"
      if (home === "_host") return
      const cluster = useClusterStore()
      if (!cluster.isCluster) return
      cluster.markSiteOffline(home)
      const locale = useLocaleStore()
      const lang = locale.current || "en"
      const notifications = useNotificationsStore()
      notifications.push({
        level: "warn",
        title: translate(lang, "cluster.disconnect.title"),
        body: translate(lang, "cluster.disconnect.body", { site: home }),
        timeoutMs: 8000,
      })
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

    /**
     * Reconcile ``runningJobs`` for one tab against a replay's
     * ``pendingJobs`` — the authoritative "still running per canonical
     * history" set for that tab.
     *
     * Additions/merges ALWAYS apply: stamp ``tab``, keep the earliest
     * known ``startedAt`` (a live entry pre-dates any rebuild
     * approximation and must not be pushed forward).
     *
     * Removals apply ONLY when ``fetchedAt`` is given (a history fetch
     * drove this rebuild) and only for tab-owned jobs whose
     * ``startedAt`` pre-dates the fetch: a job that started after the
     * fetch began is newer live state the stale history can't speak to.
     * Pure branch-switch rebuilds pass ``fetchedAt = null`` and never
     * remove.
     */
    _reconcileRunningJobs(tab, pendingJobs, fetchedAt = null) {
      const pendingIds = new Set(Object.keys(pendingJobs))
      for (const [jobId, job] of Object.entries(pendingJobs)) {
        const existing = this.runningJobs[jobId]
        // Keep the earliest known start — a live entry pre-dates any
        // rebuild approximation and must not be pushed forward.
        let startedAt = job.startedAt
        if (existing?.startedAt && (startedAt == null || existing.startedAt < startedAt)) {
          startedAt = existing.startedAt
        }
        // Merge, not replace: replay carries canonical fields (name,
        // type, startedAt) and wins for those, but UI-only fields set on
        // the live entry (promotable, cancelling, …) that the replay
        // never carries must survive.
        this.runningJobs[jobId] = {
          ...existing,
          ...job,
          tab: job.tab || existing?.tab || tab || undefined,
          startedAt,
        }
      }
      if (fetchedAt != null) {
        for (const [jobId, job] of Object.entries(this.runningJobs)) {
          if (job.tab !== tab) continue
          if (pendingIds.has(jobId)) continue
          if (job.startedAt == null || job.startedAt >= fetchedAt) continue
          delete this.runningJobs[jobId]
        }
      }
      if (pendingIds.size > 0) this._ensureJobTimer()
      this._checkJobTimer()
    },

    /** Restore running jobs from replay result. A fetch-driven load
     *  passes ``fetchedAt`` so stale tab-owned jobs the fresh history no
     *  longer lists are pruned (reconnect after a missed terminal). */
    _restoreRunningState(tabKey, pendingJobs, isProcessing = false, fetchedAt = null) {
      this._reconcileRunningJobs(tabKey, pendingJobs, fetchedAt)
      if (tabKey) {
        this.processingByTab[tabKey] = !!isProcessing
        this._rehydrateRunningParts(tabKey, pendingJobs)
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
    _restoreTokenUsage(source, events, alreadyDeduped = false) {
      // Canonical history is authoritative — rebuild this source's total
      // from scratch so a reconnect (which re-runs _loadHistory on the
      // same generation) does not add the persisted token_usage rows a
      // second time and double the count.
      this.tokenUsage[source] = { prompt: 0, completion: 0, total: 0, cached: 0, lastPrompt: 0 }
      const normalizedEvents = alreadyDeduped ? events : _dedupeAdjacentDuplicateEvents(events)
      for (const evt of normalizedEvents) {
        const isTokenEvt =
          (evt.type === "activity" && evt.activity_type === "token_usage") ||
          evt.type === "token_usage"
        if (isTokenEvt) {
          const prev = this.tokenUsage[source]
          this.tokenUsage[source] = {
            prompt: prev.prompt + (evt.prompt_tokens || 0),
            completion: prev.completion + (evt.completion_tokens || 0),
            total: prev.total + (evt.total_tokens || 0),
            cached: prev.cached + (evt.cached_tokens || 0),
            lastPrompt: evt.prompt_tokens || prev.lastPrompt,
          }
        }
        // Sub-agent cumulative snapshots persist as ``subagent_token_usage``
        // (running updates) and ``subagent_result`` (final); fold the
        // highest per job back into the session total (UXI-03).
        if (evt.type === "subagent_token_usage" || evt.type === "subagent_result") {
          this._recordSubagentUsage(source, evt.job_id, evt)
        }
      }
    },

    /** Record the latest cumulative token snapshot for a sub-agent job.
     *  Snapshots are cumulative per job (never deltas); keep the
     *  highest-total one so an out-of-order or older frame can't shrink
     *  the running total (UXI-03). */
    _recordSubagentUsage(source, jobId, data) {
      if (!jobId || data?.total_tokens == null) return
      // Key by source+job so two creatures in one terrarium-scoped store
      // with colliding job_ids don't max-overwrite each other (UXI-03).
      const key = `${source || ""}:${jobId}`
      const total = Number(data.total_tokens) || 0
      const prev = this.subagentUsageByJob[key]
      if (prev && total < (prev.total || 0)) return
      this.subagentUsageByJob[key] = {
        prompt: Number(data.prompt_tokens) || 0,
        completion: Number(data.completion_tokens) || 0,
        cached: Number(data.cached_tokens) || 0,
        total,
      }
    },

    /** Handle ALL incoming WS messages */
    _onMessage(data) {
      const source = this._tabForSource(data.source || "")
      if (source) {
        if (
          ["ask_text", "confirm", "selection", "card", "ui_supersede", "ui_reply_ack"].includes(
            data.type,
          )
        ) {
          this._historyMutationSeqByTab[source] = (this._historyMutationSeqByTab[source] || 0) + 1
        }
        const scope = scopeOfStoreId(this.$id) || "default"
        // Accumulate the turn's raw stream text before any branch-view
        // gating drops frames from the displayed message list. Bounded by
        // the accumulator ceiling — no full-response second copy is kept.
        if (data.type === "processing_start") {
          this._attentionStreamTextByTab[source] = ""
        } else if (data.type === "text" && typeof data.content === "string") {
          this._attentionStreamTextByTab[source] = _accumulateAttentionStreamText(
            this._attentionStreamTextByTab[source],
            data.content,
          )
        }
        const { state } = reduceAttentionEdge(
          this.attentionByTab[source] || createAttentionState(),
          data,
          {
            scope,
            tab: source,
            summary: _attentionEdgeSummary(data, this._attentionStreamTextByTab[source]),
          },
        )
        this.attentionByTab[source] = state
        publishAttention(scope, source, state)
      }

      if (data.type === "user_input") {
        this._handleUserInput(source, data)
      } else if (data.type === "text") {
        // If we get text chunks but the tab isn't marked processing
        // (e.g. reconnect mid-stream), flip it so the UI shows the
        // streaming indicator on the correct tab.
        if (source && !this.processingByTab[source]) {
          this.processingByTab[source] = true
        }
        // Branch isolation — only mutate ``messagesByTab`` when this
        // chunk belongs to the branch the user is currently viewing.
        // Off-branch chunks (e.g. user clicked <1/2> to see the old
        // branch while branch 2 is still streaming) are dropped from
        // the live mutation path; SessionOutput already persists them
        // and a switch back to the streaming branch will resync.
        if (this._frameMatchesViewedBranch(source, data)) {
          this._appendStreamChunk(source, data.content)
        }
      } else if (data.type === "processing_start") {
        if (source) this.processingByTab[source] = true
        // Update the streaming-branch target whenever the backend
        // tells us which (turn, branch) it just started on. The
        // optimistic prediction we made in regenerate/editMessage
        // gets corrected here if the real branch differs.
        if (source && typeof data.turn_index === "number" && typeof data.branch_id === "number") {
          this._reconcileBranchOperation(
            source,
            data.turn_index,
            data.branch_id,
            data.request_id ?? data.requestId ?? null,
          )
        }
        // Promote queued user messages (agent is now processing them)
        this._promoteQueuedMessages(source)
      } else if (data.type === "processing_end") {
        if (source) this.branchOperationByTab[source] = null
        this._finishStream(source)
        this._scheduleBranchResync(source)
      } else if (data.type === "idle") {
        if (source) {
          this.processingByTab[source] = false
          this.branchOperationByTab[source] = null
        }
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
      } else if (data.type === "input_queued") {
        this._handlePendingAck(source, data, "queued")
      } else if (data.type === "input_edit_ack") {
        this._handlePendingAck(source, data, "edit")
      } else if (data.type === "input_cancel_ack") {
        this._handlePendingAck(source, data, "cancel")
      } else if (data.type === "error") {
        this._addMsg(source, {
          id: "err_" + Date.now(),
          role: "error",
          content: data.content || "Unknown error",
          timestamp: new Date().toISOString(),
        })
        if (source) {
          this.processingByTab[source] = false
          this._failBranchOperation(source, data.content || "Branch operation failed")
        }
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
          this._historyMutationSeqByTab[tab] = (this._historyMutationSeqByTab[tab] || 0) + 1
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
            // Route to the creature that raised the prompt — a sibling
            // that isn't the WS-bound creature would otherwise hang its
            // tool forever waiting on a reply delivered to the wrong
            // router (UXI-09). Mirrors the input frame's ``target``.
            target: tab,
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

    /**
     * Edit a still-queued mid-turn message by id (UXI-08a). Optimistically
     * updates the banner's shown content — the backend edits its buffer,
     * and the drain's ``user_input_injected`` (carrying the edited content)
     * pops this exact entry. An ``input_edit_ack{already_sent}`` means the
     * edit lost the race and clears the (now-stale) banner entry.
     */
    editQueuedMessage(tab, eventId, newContent) {
      const queue = this.queuedMessagesByTab[tab] || []
      const msg = queue.find((m) => m.eventId === eventId)
      if (!msg) return
      const empty = typeof newContent === "string" ? !newContent.trim() : !newContent?.length
      if (empty) return
      const contentParts =
        typeof newContent === "string" ? [{ type: "text", text: newContent }] : newContent
      const normalized = normalizeMessageContent(contentParts)
      msg.content = normalized.content
      msg.contentParts = normalized.contentParts
      if (this._ws?.readyState === WebSocket.OPEN) {
        this._ws.send(
          JSON.stringify({
            type: "input_edit",
            target: tab,
            event_id: eventId,
            content: contentParts,
          }),
        )
      }
    },

    /**
     * Cancel a still-queued mid-turn message by id (UXI-08a). Marks the
     * entry cancelling and lets the ``input_cancel_ack`` remove it; with no
     * live socket the message never reached the backend, so drop it locally.
     */
    cancelQueuedMessage(tab, eventId) {
      const queue = this.queuedMessagesByTab[tab] || []
      const idx = queue.findIndex((m) => m.eventId === eventId)
      if (idx === -1) return
      if (this._ws?.readyState === WebSocket.OPEN) {
        queue[idx].cancelling = true
        this._ws.send(JSON.stringify({ type: "input_cancel", target: tab, event_id: eventId }))
      } else {
        queue.splice(idx, 1)
      }
    },

    /**
     * Handle the queued-input acks. ``queued`` confirms the backend
     * buffered the slot (client id adopted). ``edit``/``cancel`` acks
     * carry ``status``: ``edited`` keeps the (optimistically edited)
     * entry for the drain to pop; ``cancelled`` / ``already_sent`` drop
     * the banner entry — cancelled won't run, already_sent is promoted
     * into chat by the drain's ``user_input_injected``.
     */
    _handlePendingAck(source, data, kind) {
      const queue = this.queuedMessagesByTab[source || ""]
      if (!queue) return
      const idx = queue.findIndex((m) => m.eventId === data.event_id)
      if (idx === -1) return
      if (kind === "queued") {
        queue[idx].backendQueued = true
        return
      }
      // Edit committed — keep the (optimistically edited) entry for the
      // drain's user_input_injected to promote. Every other outcome
      // (cancelled, or already_sent for edit/cancel) drops the slot.
      if (kind === "edit" && data.status === "edited") return
      queue.splice(idx, 1)
    },

    _handleActivity(source, data) {
      const at = data.activity_type
      const name = data.name || "unknown"

      // Forward ALL activities to the per-attach status store. We
      // can't rely on inject here (actions run detached from setup),
      // so recover scope from this store's $id and pass it through.
      const statusStore = useStatusStore(scopeOfStoreId(this.$id))
      statusStore.handleActivity(data)

      if (at === "assistant_reasoning") {
        const tab = source || data.agent_name || this.activeTab
        const list = this.messagesByTab[tab] || []
        const target = [...list].reverse().find((message) => message?.role === "assistant")
        if (target) {
          _insertReasoningSegments(
            target,
            data._kt_assistant_segments || [],
            `live_${data.id || "reasoning"}_r`,
          )
          target._pendingReasoningCursor = undefined
        }
        return
      }

      if (at === "session_info") {
        // Session id is global regardless of which creature emitted.
        if (data.session_id) this.sessionInfo.sessionId = data.session_id
        // Model fields are PER CREATURE — key them by the emitting
        // source. Writing them straight into the global object let
        // whichever creature spoke last stomp the model shown for
        // every tab.
        const info = {}
        if (data.model) info.model = data.model
        if (data.llm_name) info.llmName = data.llm_name
        if (data.config_name) info.configName = data.config_name
        if (data.config_ref) info.configRef = data.config_ref
        if (data.max_context != null) info.maxContext = data.max_context
        if (data.compact_threshold != null) info.compactThreshold = data.compact_threshold
        const tab = source || data.agent_name || ""
        if (tab && Object.keys(info).length) {
          this.modelByTab[tab] = { ...(this.modelByTab[tab] || {}), ...info }
          if (tab === this._rootSourceName && this.tabs.includes("root")) {
            this.modelByTab["root"] = { ...(this.modelByTab["root"] || {}), ...info }
          }
        }
        // The global sessionInfo keeps tracking the PRIMARY (bound)
        // creature only — it is the fallback for tabs with no entry.
        const isPrimary =
          !tab || tab === this.tabs[0] || (this.tabs[0] === "root" && tab === this._rootSourceName)
        if (isPrimary) {
          if (data.model) this.sessionInfo.model = data.model
          if (data.llm_name) this.sessionInfo.llmName = data.llm_name
          if (data.agent_name) this.sessionInfo.agentName = data.agent_name
          if (data.config_name) this.sessionInfo.configName = data.config_name
          if (data.config_ref) this.sessionInfo.configRef = data.config_ref
          if (data.max_context != null) this.sessionInfo.maxContext = data.max_context
          if (data.compact_threshold != null)
            this.sessionInfo.compactThreshold = data.compact_threshold
        }
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
        const list = this.messagesByTab[source] || []
        const target = [...list].reverse().find((message) => message?.role === "assistant")
        if (target?.parts) target._pendingReasoningCursor = target.parts.length
        return
      }

      if (at === "user_input_injected") {
        // Feat 3 — backend just folded a buffered user_input into
        // the current turn. Clear the matching queued banner and
        // surface the message in the chat as a normal user bubble.
        // Branch-isolation: only relevant for the viewed branch.
        if (this._frameMatchesViewedBranch(source, data)) {
          this._handleUserInputInjected(source, data)
        }
        return
      }

      if (at === "interrupt") {
        // The agent's controller was cancelled mid-turn (user clicked
        // interrupt, or the backend's flush-after-interrupt promoted
        // buffered events into fresh turns). The FE's queue MUST clear
        // here — otherwise the queued banner sticks forever because the
        // expected ``user_input_injected`` activity never fires for the
        // cancelled turn. Promotes the queue into chat history so the
        // user still sees what they typed; the next turn (started by
        // the agent's flush-after-interrupt) will pick those messages
        // up as fresh inputs.
        this._promoteQueuedMessages(source)
        return
      }

      // Ensure we have a tab for this source
      if (!this.messagesByTab[source]) return
      // Branch isolation — every remaining activity below mutates the
      // displayed message list. If this frame is for a branch the user
      // isn't currently viewing (e.g. branch 2 is streaming while the
      // user clicked back to branch 1), DROP the mutation; the event is
      // still persisted server-side and the next resync picks it up
      // when the user switches back. Frames without branch metadata
      // pass through (legacy / non-per-turn activity).
      if (!this._frameMatchesViewedBranch(source, data)) return
      const msgs = this.messagesByTab[source]
      // Every remaining activity mutates live chat state. Bump the
      // mutation generation so an in-flight /history fetch that was
      // snapshotted before this frame cannot clobber the fresher live
      // state when it resolves (e.g. a tool_done landing during the
      // fetch must not be reverted to "running" by the stale replay).
      this._historyMutationSeqByTab[source] = (this._historyMutationSeqByTab[source] || 0) + 1

      if (at === "wire_inbound") {
        // Output-wiring delivery from another creature. The fields
        // arrive at the top level of the WS frame (filtered through
        // ``_STREAM_METADATA_KEYS`` on the backend) — not under a
        // nested ``metadata`` object. That's also the shape the
        // history replay produces, so reading from ``data.*``
        // directly keeps live + reload paths consistent.
        msgs.push({
          id: `wire_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`,
          role: "wire_inbound",
          from: data.from || data.detail || "",
          to: data.to || source,
          preview: data.content_preview || "",
          withContent: data.with_content !== false,
          // The sender's turn travels under its own key; the frame's
          // plain turn_index/branch_id are receiver-timeline stamps.
          turnIndex: data.source_turn_index || 0,
          // Cross-site delivery flag — set by the backend forwarder
          // (terrarium_output_wire.py) for cross-node wires.
          crossNode: !!(data.cross_node || data.metadata?.cross_node),
          timestamp: new Date().toISOString(),
        })
        return
      }

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

      if (at === "background_result") {
        // Combined delivery banner: `labels` lists every folded
        // completion; older single-event frames carry only `label`.
        msgs.push({
          id: "bgres_" + Date.now(),
          role: "bg_result",
          label:
            (Array.isArray(data.labels) ? data.labels.join(", ") : data.label) || data.job_id || "",
          kind: data.kind || "tool",
          jobId: data.job_id || "",
          timestamp: new Date().toISOString(),
        })
        return
      }

      if (at === "compact_skipped") {
        const round = data.compact_round || data.round || 0
        const existing =
          [...msgs]
            .reverse()
            .find(
              (msg) => msg.role === "compact" && msg.round === round && msg.status === "running",
            ) ||
          [...msgs].reverse().find((msg) => msg.role === "compact" && msg.status === "running")
        if (existing) {
          existing.status = "skipped"
          existing.reason = data.reason || ""
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

      if (at === "drive_turn") {
        msgs.push(_driveTurnMessage(data, "drv_" + Date.now(), new Date().toISOString()))
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
        this._commitPendingReasoningRound(last)
        if (last.parts.length > 0) {
          const tail = last.parts[last.parts.length - 1]
          if (tail.type === "text") tail._streaming = false
        }
        const toolId = data.id || "tc_" + Date.now()
        const jobId = data.job_id || ""
        const startedPart = {
          type: "tool",
          id: toolId,
          jobId,
          name,
          kind: at === "subagent_start" ? "subagent" : "tool",
          args: markRaw(data.args || { info: data.detail }),
          status: "running",
          result: "",
          tools_used: data.tools_used || [],
          children: [],
          startedAt: Date.now(),
          llm_name: at === "subagent_start" ? data.llm_name || "" : "",
          model: at === "subagent_start" ? data.model || "" : "",
        }
        last.parts.push(startedPart)
        this._indexToolPart(source, startedPart, msgs)
        // Track all tasks as running jobs (direct tasks are promotable)
        const runKey = jobId || toolId
        const isBg = data.background || false
        this.runningJobs[runKey] = {
          name,
          type: at === "subagent_start" ? "subagent" : "tool",
          startedAt: Date.now(),
          promotable: !isBg,
          tab: source,
        }
        this._ensureJobTimer()
      } else if (at === "tool_done" || at === "subagent_done") {
        let tc = this._findToolPart(source, msgs, name, data.job_id)
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
          this._indexToolPart(source, tc, msgs)
        }
        tc.status = "done"
        if (at === "subagent_done") {
          tc.llm_name = data.llm_name || tc.llm_name || ""
          tc.model = data.model || tc.model || ""
        }
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
        if (at === "subagent_done") this._recordSubagentUsage(source, data.job_id, data)
        delete this.runningJobs[tc.jobId || tc.id]
        this._checkJobTimer()
      } else if (at === "tool_error" || at === "subagent_error") {
        let tc = this._findToolPart(source, msgs, name, data.job_id)
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
          this._indexToolPart(source, tc, msgs)
        }
        tc.status = data.interrupted || data.final_state === "interrupted" ? "interrupted" : "error"
        if (at === "subagent_error") {
          tc.llm_name = data.llm_name || tc.llm_name || ""
          tc.model = data.model || tc.model || ""
        }
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
        if (at === "subagent_error") this._recordSubagentUsage(source, data.job_id, data)
        delete this.runningJobs[tc.jobId || tc.id]
        this._checkJobTimer()
      } else if (at === "subagent_token_update") {
        // Live token usage update from a running sub-agent
        const saName = data.subagent || ""
        const saJobId = data.job_id || ""
        this._recordSubagentUsage(source, saJobId, data)
        const sa = this._findSubagentPart(source, msgs, saName, saJobId)
        if (sa) {
          if (data.total_tokens) sa.total_tokens = data.total_tokens
          if (data.prompt_tokens) sa.prompt_tokens = data.prompt_tokens
          if (data.completion_tokens) sa.completion_tokens = data.completion_tokens
        }
      } else if (at?.startsWith("subagent_tool_")) {
        // Sub-agent internal tool activity: find parent by job_id or name
        const saName = data.subagent || ""
        const saJobId = data.job_id || ""
        const sa = this._findSubagentPart(source, msgs, saName, saJobId)
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
      if (!this._instanceId) return
      if (this.runningJobs[jobId]) {
        this.runningJobs[jobId].promotable = false
      }
      try {
        const target = this.activeTab
        if (!target || target.startsWith("ch:")) return
        const { terrariumAPI } = await import("@/utils/api")
        await terrariumAPI.promoteCreatureTask(this._instanceGraphId, target, jobId)
      } catch (e) {
        console.warn("Failed to promote task:", e)
      }
    },

    /** True when a failed edit/regen request plausibly reached the
     *  backend and may still be executing (these POSTs block through
     *  the whole rerun): a gateway timeout, or no HTTP response with
     *  either a post-dispatch cut (timeout) or a demonstrably
     *  reachable server (chat WS open). Offline/DNS/refused failures
     *  with a dead WS count as never-dispatched. */
    _requestMayStillBeRunning(err) {
      const status = err?.response?.status
      // 502/504: the proxy gave up while the backend kept going.
      if (status === 502 || status === 504) return true
      if (err?.response != null) return false
      if (err?.code === "ECONNABORTED" || err?.code === "ETIMEDOUT") return true
      return this.wsStatus === "open"
    },

    /** Highest persisted event_id in an event list (optimistic
     *  placeholders excluded); null when none carry one. */
    _maxEventId(events) {
      let max = null
      for (const ev of events || []) {
        if (ev?._optimistic) continue
        const id = ev?.event_id
        if (typeof id === "number" && (max == null || id > max)) max = id
      }
      return max
    },

    /** Highest branch_id recorded for a turn in the cached event log
     *  (1 when the turn exists but never branched; null when the turn
     *  is unknown). */
    _latestBranchForTurn(tab, turnIndex) {
      if (tab == null || turnIndex == null) return null
      let latest = null
      for (const ev of this.eventsByTab[tab] || []) {
        if (ev?.turn_index !== turnIndex) continue
        const b = ev?.branch_id
        if (typeof b === "number" && (latest == null || b > latest)) latest = b
      }
      return latest
    },

    _markBranchResyncPending(tab = this.activeTab, expected = null) {
      if (!tab) return
      const pending = this._branchResyncPendingByTab[tab] || {}
      const next = {
        active: true,
        retries: pending.retries || 0,
        expectedBranchByTurn: {
          ...(pending.expectedBranchByTurn || {}),
          ...(expected?.expectedBranchByTurn || {}),
        },
      }
      if (Object.hasOwn(pending, "baselineMaxEventId")) {
        next.baselineMaxEventId = pending.baselineMaxEventId
        next.baselinePhysicalFingerprint = pending.baselinePhysicalFingerprint
      }
      // Metadata-poor branch ops have no expected branch to verify, so
      // fingerprint the pre-op physical log and require actual advancement.
      if (expected?.baselineFromCache && !Object.hasOwn(next, "baselineMaxEventId")) {
        const physical = (this.eventsByTab[tab] || []).filter((evt) => !evt?._optimistic)
        next.baselineMaxEventId = this._maxEventId(physical)
        // Length, not content: the fingerprint only needs to detect that
        // the physical log advanced; stringifying full content
        // re-serializes megabytes on branch-heavy tabs.
        next.baselinePhysicalFingerprint = JSON.stringify(
          physical.map((evt) => [
            evt?.type,
            evt?.turn_index,
            evt?.branch_id,
            typeof evt?.content === "string" ? evt.content.length : 0,
          ]),
        )
      }
      this._branchResyncPendingByTab[tab] = next
    },

    _scheduleBranchResync(tab) {
      if (!tab) return
      // Fire a resync on EVERY processing_end, not only when a branch
      // op is pending. WS-streamed user/assistant messages don't
      // carry turn_index, so without the post-turn resync the
      // messages in ``messagesByTab`` stay turnIndex-less and the
      // retry button can't tell the backend which turn was clicked
      // (it silently falls through to tail-regen — that was the
      // "retry only retries the last message" bug).
      if (this._branchResyncTimers[tab]) clearTimeout(this._branchResyncTimers[tab])
      this._branchResyncTimers[tab] = setTimeout(async () => {
        delete this._branchResyncTimers[tab]
        const pending = this._branchResyncPendingByTab[tab]
        if (pending?.active) {
          pending.retries = (pending.retries || 0) + 1
          if (pending.retries > BRANCH_RESYNC_MAX_RETRIES) {
            this._dropStaleBranchOp(tab, pending)
          }
        }
        try {
          await this._resyncHistory(tab)
        } catch (e) {
          // A failed fetch must not end the retry chain mid-branch-op.
          if (this._branchResyncPendingByTab[tab]?.active) {
            this._scheduleBranchResync(tab)
          }
        }
      }, BRANCH_RESYNC_DELAY_MS)
    },

    _clearBranchResyncTimers() {
      for (const timer of Object.values(this._branchResyncTimers || {})) {
        clearTimeout(timer)
      }
      this._branchResyncTimers = {}
    },

    /** Drop a branch op whose expected branch never landed, INCLUDING
     *  its speculative view state — a dangling ``branchView`` entry
     *  for a nonexistent branch renders blank under strict selection,
     *  and the processing flag would spin forever. */
    _dropStaleBranchOp(tab, pending) {
      const view = this.branchViewByTab[tab]
      for (const [turn, branch] of Object.entries(pending?.expectedBranchByTurn || {})) {
        if (view && view[turn] === branch) delete view[turn]
        const streaming = this._streamingBranchByTab[tab]
        if (streaming && streaming.turnIndex === Number(turn) && streaming.branchId === branch) {
          delete this._streamingBranchByTab[tab]
        }
      }
      // Purge the synthetic events too — if the follow-up canonical
      // fetch fails there is no later overwrite to retire them.
      const events = this.eventsByTab[tab]
      if (events?.some((ev) => ev?._optimistic)) {
        this._setEvents(
          tab,
          events.filter((ev) => !ev?._optimistic),
        )
      }
      this.processingByTab[tab] = false
      delete this._branchResyncPendingByTab[tab]
      this._rebuildMessages(tab)
    },

    /**
     * Decide whether an incoming WS frame's ``(turn_index, branch_id)``
     * matches the branch the user is currently viewing for that turn.
     *
     * Backend frames now carry ``turn_index`` / ``branch_id``; legacy
     * frames don't. The contract:
     *
     *   - Frame has no ``branch_id`` → trusted (legacy stream, assume
     *     it's for the active branch — most callers have nothing else
     *     to compare against).
     *   - Frame's ``branch_id`` matches the user's branch selection
     *     for that turn (or matches the default-latest when no
     *     explicit selection) → trusted.
     *   - Otherwise → reject, return false. The caller drops the
     *     mutation; the chunk is still persisted server-side and a
     *     branch switch + resync will surface it later.
     */
    /**
     * Map a WS frame's ``source`` (always the creature's REAL name —
     * ``StreamOutput`` is constructed with ``creature.name``) onto the
     * tab key the store actually uses. Recipe-built terrariums key the
     * privileged creature's tab as ``"root"``, so frames tagged with
     * its real name would otherwise miss every ``messagesByTab`` /
     * ``processingByTab`` lookup and be silently dropped — only
     * ``session_info`` had this aliasing before.
     */
    _tabForSource(source) {
      if (
        source &&
        source === this._rootSourceName &&
        !this.messagesByTab[source] &&
        this.messagesByTab["root"]
      ) {
        return "root"
      }
      return source
    },

    _frameMatchesViewedBranch(tab, data) {
      const fb = data?.branch_id
      const ft = data?.turn_index
      if (typeof fb !== "number" || typeof ft !== "number") return true
      const view = this.branchViewByTab[tab]
      if (view && Object.prototype.hasOwnProperty.call(view, ft)) {
        return view[ft] === fb
      }
      // No explicit override — the replay defaults each turn to its
      // latest branch. The streaming target is by construction the
      // latest branch of its turn (the backend opens max+1 before
      // emitting any chunk), so an unset view aligns with "viewing
      // the running branch".
      const streaming = this._streamingBranchByTab[tab]
      if (streaming && streaming.turnIndex === ft) {
        return streaming.branchId === fb
      }
      // Fall back to the per-tab branch metadata: latest known branch
      // for this turn IS what the user sees by default.
      const cached = this.eventsByTab[tab]
      if (cached) {
        let latest = 0
        for (const evt of cached) {
          if (evt?.turn_index === ft && typeof evt?.branch_id === "number") {
            if (evt.branch_id > latest) latest = evt.branch_id
          }
        }
        if (latest > 0) return latest === fb
      }
      return true
    },

    /**
     * Splice synthetic ``user_input`` + ``user_message`` events for a
     * newly-opened branch into ``eventsByTab[tab]`` so the chevron
     * navigator promotes to ``<N/M>`` the instant Save & Rerun / Retry
     * fires — instead of waiting for the post-turn history resync.
     * The injected events get fenced with negative ``event_id`` so
     * the next real resync recognises them as placeholders and the
     * canonical ones from the backend take over without duplication.
     *
     * Caller is responsible for setting ``branchViewByTab[tab][turnIndex]``
     * to the new branch and calling ``_rebuildMessages`` afterwards.
     * Returns ``true`` when the splice landed, ``false`` if turn /
     * branch metadata were missing (callers can still proceed; the
     * navigator will catch up on resync).
     */
    _injectOptimisticBranch(tab, { turnIndex, branchId, content }) {
      if (!tab || typeof turnIndex !== "number" || typeof branchId !== "number") {
        return false
      }
      const events = this.eventsByTab[tab] || []
      // Compute parent_branch_path snapshot from currently-selected
      // branches of prior turns, so the optimistic event's path matches
      // what the backend will write (otherwise the path-aware replay
      // hides this branch when the user has chosen non-latest branches
      // on earlier turns).
      const view = this.branchViewByTab[tab] || {}
      const latestByTurn = new Map()
      for (const evt of events) {
        const ti = evt?.turn_index
        const bi = evt?.branch_id
        if (typeof ti !== "number" || typeof bi !== "number") continue
        const prev = latestByTurn.get(ti) || 0
        if (bi > prev) latestByTurn.set(ti, bi)
      }
      const parentPath = []
      for (const [ti, latest] of latestByTurn) {
        if (ti >= turnIndex) continue
        const chosen = Object.prototype.hasOwnProperty.call(view, ti) ? view[ti] : latest
        parentPath.push([ti, chosen])
      }
      parentPath.sort((a, b) => a[0] - b[0])
      // Negative event ids so the natural-number ones the backend
      // assigns sort after these; the next resync overwrites the cache
      // wholesale, so the negatives are inherently transient.
      const baseId = -Date.now()
      const userInput = {
        type: "user_input",
        content,
        event_id: baseId,
        turn_index: turnIndex,
        branch_id: branchId,
        parent_branch_path: parentPath,
        _optimistic: true,
      }
      const userMessage = {
        type: "user_message",
        content,
        event_id: baseId - 1,
        turn_index: turnIndex,
        branch_id: branchId,
        parent_branch_path: parentPath,
        _optimistic: true,
      }
      const processingStart = {
        type: "processing_start",
        event_id: baseId - 2,
        turn_index: turnIndex,
        branch_id: branchId,
        parent_branch_path: parentPath,
        _optimistic: true,
      }
      this._setEvents(tab, [...events, userInput, userMessage, processingStart])
      this._historyMutationSeqByTab[tab] = (this._historyMutationSeqByTab[tab] || 0) + 1
      return true
    },

    _branchOperationResult(ok, tab, operation = null, error = null) {
      return { ok, tab, operation, error }
    },

    _setBranchOperation(tab, operation) {
      this.branchOperationByTab[tab] = operation
      this.branchOperationErrorByTab[tab] = null
    },

    _failBranchOperation(tab, error) {
      const message =
        error instanceof Error ? error.message : String(error || "Branch operation failed")
      this.branchOperationByTab[tab] = null
      this.branchOperationErrorByTab[tab] = message
      return message
    },

    _reconcileBranchOperation(tab, turnIndex, branchId, requestId = null) {
      if (typeof turnIndex !== "number" || typeof branchId !== "number") return false
      const operation = this.branchOperationByTab[tab]
      if (
        operation?.instanceGeneration != null &&
        operation.instanceGeneration !== this._instanceGeneration
      )
        return false
      if (requestId && requestId !== operation?.requestId) return false
      const predictedTurn = operation?.turnIndex
      const predictedBranch = operation?.predictedBranch
      if (typeof predictedTurn === "number" && typeof predictedBranch === "number") {
        this._setEvents(
          tab,
          (this.eventsByTab[tab] || []).map((event) => {
            if (
              event?._optimistic &&
              event.turn_index === predictedTurn &&
              event.branch_id === predictedBranch
            ) {
              return { ...event, turn_index: turnIndex, branch_id: branchId }
            }
            return event
          }),
        )
        if (this.branchViewByTab[tab]?.[predictedTurn] === predictedBranch) {
          const nextView = { ...(this.branchViewByTab[tab] || {}) }
          delete nextView[predictedTurn]
          nextView[turnIndex] = branchId
          this.branchViewByTab[tab] = nextView
        }
      }
      const pending = this._branchResyncPendingByTab[tab]
      if (pending?.expectedBranchByTurn && typeof predictedTurn === "number") {
        const expectedBranchByTurn = { ...pending.expectedBranchByTurn }
        delete expectedBranchByTurn[predictedTurn]
        expectedBranchByTurn[turnIndex] = branchId
        this._branchResyncPendingByTab[tab] = { ...pending, expectedBranchByTurn }
      }
      this._streamingBranchByTab[tab] = { turnIndex, branchId }
      if (operation)
        this.branchOperationByTab[tab] = { ...operation, phase: "accepted", turnIndex, branchId }
      return true
    },

    _conversationUserPosition(tab, messageIdx) {
      const msgs = this.messagesByTab[tab] || []
      if (messageIdx == null || messageIdx < 0 || messageIdx >= msgs.length) return null
      if (msgs[messageIdx]?.role !== "user") return null
      let pos = 0
      for (let i = 0; i < messageIdx; i++) {
        if (msgs[i]?.role === "user" && !msgs[i]?.injectedMidTurn) pos += 1
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
    async regenerateLastResponse({ turnIndex = null } = {}) {
      const tab = this.activeTab
      if (!this._instanceId)
        return this._branchOperationResult(false, tab, null, "No active instance")
      if (this.branchOperationByTab[tab]) {
        return this._branchOperationResult(
          false,
          tab,
          this.branchOperationByTab[tab],
          "A branch operation is already running",
        )
      }
      // Channel-message tabs aren't regen-eligible (the channel isn't a
      // creature with a per-turn LLM response to retry).
      if (!tab || tab.startsWith("ch:")) {
        return this._branchOperationResult(
          false,
          tab,
          null,
          "This tab cannot start a branch operation",
        )
      }
      this._markBranchResyncPending(tab, { baselineFromCache: true })
      const msgs = this.messagesByTab[tab] || []
      // Resolve the target user message + its turn so we can predict
      // the freshly-opened branch and promote the chevron navigator
      // before the backend round-trip completes.
      let targetUserMsg = null
      let resolvedTurnIndex = turnIndex
      if (turnIndex != null) {
        targetUserMsg = msgs.find((m) => m?.role === "user" && m.turnIndex === turnIndex)
      } else {
        for (let i = msgs.length - 1; i >= 0; i--) {
          if (msgs[i]?.role === "user") {
            targetUserMsg = msgs[i]
            if (typeof targetUserMsg.turnIndex === "number") {
              resolvedTurnIndex = targetUserMsg.turnIndex
            }
            break
          }
        }
      }
      // Locally splice for instant feedback. With a specific
      // ``turnIndex`` (retry on non-tail), cut from the matching
      // user message onward so the user sees just-the-rerun-target
      // remain. Without a turnIndex (tail regen), drop trailing
      // assistant messages back to the most recent user message.
      let cutAt = msgs.length
      if (turnIndex != null) {
        for (let i = 0; i < msgs.length; i++) {
          if (msgs[i]?.role === "user" && msgs[i]?.turnIndex === turnIndex) {
            cutAt = i + 1
            break
          }
        }
      } else {
        for (let i = msgs.length - 1; i >= 0; i--) {
          if (msgs[i].role === "user") break
          cutAt = i
        }
      }
      if (cutAt < msgs.length) {
        msgs.splice(cutAt)
        this._rebuildMessageIndexes(tab)
      }
      // Snapshot state BEFORE the optimistic mutation so the catch
      // block can roll back cleanly when the API call fails. Without
      // this the tab gets stuck showing KohakUwUing forever (no WS
      // processing_end will fire if the backend never started a turn).
      const previousEvents = this.eventsByTab[tab]
      const previousBranchView =
        tab && this.branchViewByTab[tab] ? { ...this.branchViewByTab[tab] } : null
      const previousStreaming = tab ? this._streamingBranchByTab[tab] : null
      const previousProcessing = !!this.processingByTab[tab]
      // Optimistic branch promotion: when we know the turn AND its
      // current latest branch, synthesise placeholder events into the
      // event log so the navigator promotes to <N+1/N+1> immediately
      // and the KohakUwUing label binds to the right branch. The next
      // resync replaces these with canonical backend events.
      const knownLatestBranch =
        typeof targetUserMsg?.latestBranch === "number"
          ? targetUserMsg.latestBranch
          : this._latestBranchForTurn(tab, resolvedTurnIndex)
      const predictedBranch = typeof knownLatestBranch === "number" ? knownLatestBranch + 1 : null
      let optimisticApplied = false
      if (typeof resolvedTurnIndex === "number" && predictedBranch != null) {
        const originalContent = targetUserMsg?.contentParts || targetUserMsg?.content || ""
        const injected = this._injectOptimisticBranch(tab, {
          turnIndex: resolvedTurnIndex,
          branchId: predictedBranch,
          content: originalContent,
        })
        if (injected) {
          if (!this.branchViewByTab[tab]) this.branchViewByTab[tab] = {}
          this.branchViewByTab[tab][resolvedTurnIndex] = predictedBranch
          this._streamingBranchByTab[tab] = {
            turnIndex: resolvedTurnIndex,
            branchId: predictedBranch,
          }
          this.processingByTab[tab] = true
          this._rebuildMessages(tab)
          optimisticApplied = true
          // Arm the stale-history guard with the prediction (same as
          // editMessage).
          this._markBranchResyncPending(tab, {
            expectedBranchByTurn: { [resolvedTurnIndex]: predictedBranch },
          })
        }
      }
      const requestId = _newRequestId()
      this._setBranchOperation(tab, {
        type: "regenerate",
        phase: "starting",
        turnIndex: resolvedTurnIndex,
        predictedBranch,
        requestId,
        instanceGeneration: this._instanceGeneration,
      })
      try {
        const { agentAPI } = await import("@/utils/api")
        // For terrarium: session_id = the terrarium's id, creature_id =
        // the active sub-tab's creature name. For standalone: pass the
        // agent id as both (the API treats ``"_"`` as "any session").
        // Unified routing — every session has a graph_id and creatures
        // keyed by name. Solo sessions just have a 1-creature roster.
        const [sid, cid] = [this._instanceGraphId, tab]
        // Pass the user's ORIGINAL branch view (pre-optimistic) so the
        // backend reloads its in-memory conversation under the subtree
        // the user was actually viewing. The predicted-branch override
        // we set above is only for our own navigator; the backend
        // doesn't know about it yet (it opens that branch itself).
        const branchView = previousBranchView
        let regenResponse
        try {
          regenResponse = await agentAPI.regenerate(sid, cid, {
            turnIndex,
            branchView,
            requestId,
            locator: targetUserMsg?.locator,
          })
        } catch (e) {
          // No HTTP response ≠ rejected (this POST blocks through the
          // whole rerun) — keep the optimistic branch; only a real HTTP
          // error or a never-dispatched request rolls back.
          if (optimisticApplied && tab && this._requestMayStillBeRunning(e)) {
            console.warn("Regenerate transport error; keeping optimistic branch:", e)
            this._scheduleBranchResync(tab)
            return
          }
          console.warn("Failed to regenerate:", e)
          delete this._branchResyncPendingByTab[tab]
          if (optimisticApplied && tab) {
            if (previousEvents == null) delete this.eventsByTab[tab]
            else this._setEvents(tab, previousEvents)
            if (previousBranchView != null) this.branchViewByTab[tab] = previousBranchView
            else delete this.branchViewByTab[tab]
            if (previousStreaming != null) this._streamingBranchByTab[tab] = previousStreaming
            else delete this._streamingBranchByTab[tab]
            this.processingByTab[tab] = previousProcessing
            this._rebuildMessages(tab)
          }
          this._scheduleBranchResync(tab)
          const error = this._failBranchOperation(tab, e)
          return this._branchOperationResult(false, tab, null, error)
        }
        if (regenResponse?.branch_id != null && regenResponse?.turn_index != null) {
          // Trust the backend's exact branch_id over our prediction —
          // the latest seen by the user might lag the persisted state
          // (e.g. another tab also branched this turn before us). Re-
          // select the navigator and the streaming target to match.
          const realTurn = regenResponse.turn_index
          const realBranch = regenResponse.branch_id
          this._reconcileBranchOperation(tab, realTurn, realBranch)
          this._markBranchResyncPending(tab, {
            expectedBranchByTurn: { [realTurn]: realBranch },
          })
        }
        // The regen is committed server-side from here on — resync
        // failures must not roll it back.
        try {
          await this._resyncHistory(tab)
        } catch (e) {
          this._scheduleBranchResync(tab)
        }
        return this._branchOperationResult(true, tab, this.branchOperationByTab[tab])
      } catch (e) {
        console.warn("Failed to regenerate:", e)
        const error = this._failBranchOperation(tab, e)
        return this._branchOperationResult(false, tab, null, error)
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
      const tab = target.tabId || this.activeTab
      if (!this._instanceId)
        return this._branchOperationResult(false, tab, null, "No active instance")
      if (messageIdx == null)
        return this._branchOperationResult(false, tab, null, "No message selected")
      if (tab?.startsWith("ch:"))
        return this._branchOperationResult(false, tab, null, "Channel messages cannot be edited")
      if (this.branchOperationByTab[tab]) {
        return this._branchOperationResult(
          false,
          tab,
          this.branchOperationByTab[tab],
          "A branch operation is already running",
        )
      }
      let backendIdx = messageIdx
      let userPosition = target.userPosition
      const locator = target.locator ?? this.messagesByTab[tab]?.[messageIdx]?.locator
      const turnIndex = locator?.turnIndex ?? target.turnIndex
      // Never-branched messages carry no ``latestBranch`` metadata —
      // derive the current max from the cached event log so the
      // prediction (and the stale-history guard) still arm.
      const expectedLatestBranch = target.latestBranch ?? this._latestBranchForTurn(tab, turnIndex)
      this._markBranchResyncPending(tab, {
        expectedBranchByTurn:
          turnIndex != null && expectedLatestBranch != null
            ? { [turnIndex]: expectedLatestBranch + 1 }
            : {},
        baselineFromCache: turnIndex == null || expectedLatestBranch == null,
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
        return this._branchOperationResult(false, tab, null, "The message has no editable turn")
      }
      const previousMessages = tab ? [...(this.messagesByTab[tab] || [])] : null
      const previousEvents = tab ? this.eventsByTab[tab] : null
      const previousBranchView =
        tab && this.branchViewByTab[tab] ? { ...this.branchViewByTab[tab] } : null
      const previousStreaming = tab ? this._streamingBranchByTab[tab] : null
      const previousProcessing = tab ? !!this.processingByTab[tab] : false
      // Optimistic branch promotion: predict the new branch_id and
      // splice synthetic events so the navigator flips to <N+1/N+1>
      // BEFORE the API round-trip. ``latestBranch`` came from the
      // message metadata when the user clicked Edit, so it's the
      // accurate "previous max" for this turn.
      const predictedBranch =
        turnIndex != null && typeof expectedLatestBranch === "number"
          ? expectedLatestBranch + 1
          : null
      let optimisticApplied = false
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
        if (tab) this._rebuildMessageIndexes(tab)
      }
      if (predictedBranch != null && tab) {
        const injected = this._injectOptimisticBranch(tab, {
          turnIndex,
          branchId: predictedBranch,
          content: newContent,
        })
        if (injected) {
          if (!this.branchViewByTab[tab]) this.branchViewByTab[tab] = {}
          this.branchViewByTab[tab][turnIndex] = predictedBranch
          this._streamingBranchByTab[tab] = {
            turnIndex,
            branchId: predictedBranch,
          }
          this.processingByTab[tab] = true
          this._rebuildMessages(tab)
          optimisticApplied = true
        }
      }
      const requestId = _newRequestId()
      this._setBranchOperation(tab, {
        type: "edit",
        phase: "starting",
        turnIndex,
        predictedBranch,
        requestId,
        instanceGeneration: this._instanceGeneration,
      })
      try {
        const { agentAPI } = await import("@/utils/api")
        const [sid, cid] = [this._instanceGraphId, tab]
        // Pass the user's ORIGINAL branch selection (pre-optimistic)
        // so the backend reloads its in-memory conversation under the
        // subtree the user was viewing when they clicked Edit. The
        // predicted-branch override we wrote into ``branchViewByTab``
        // above is only for our own navigator; the backend doesn't
        // know about it yet (it opens that branch itself).
        const branchView = previousBranchView
        let editResponse
        try {
          editResponse = await agentAPI.editMessage(sid, cid, backendIdx, newContent, {
            turnIndex,
            userPosition,
            branchView,
            attachments: Array.isArray(target.attachments) ? target.attachments : [],
            requestId,
            locator,
          })
        } catch (e) {
          // No HTTP response ≠ rejected: this POST blocks through the
          // whole rerun, so the backend may still be running the edit.
          // Keep the optimistic branch; the pending-resync loop
          // reconciles once the new branch lands.
          if (tab && (optimisticApplied || validTarget) && this._requestMayStillBeRunning(e)) {
            console.warn("Edit transport error; keeping optimistic branch:", e)
            this._scheduleBranchResync(tab)
            return this._branchOperationResult(true, tab, this.branchOperationByTab[tab])
          }
          delete this._branchResyncPendingByTab[tab]
          if (previousMessages && tab) this._restoreMessages(tab, previousMessages)
          if (optimisticApplied && tab) {
            if (previousEvents !== undefined) {
              if (previousEvents == null) delete this.eventsByTab[tab]
              else this._setEvents(tab, previousEvents)
            }
            if (previousBranchView != null) {
              this.branchViewByTab[tab] = previousBranchView
            } else {
              delete this.branchViewByTab[tab]
            }
            if (previousStreaming != null) {
              this._streamingBranchByTab[tab] = previousStreaming
            } else {
              delete this._streamingBranchByTab[tab]
            }
            this.processingByTab[tab] = previousProcessing
          }
          console.warn("Failed to edit message:", e)
          const error = this._failBranchOperation(tab, e)
          return this._branchOperationResult(false, tab, null, error)
        }
        if (turnIndex != null && editResponse?.branch_id != null) {
          const realTurn = editResponse.turn_index ?? turnIndex
          this._markBranchResyncPending(tab, {
            expectedBranchByTurn: { [realTurn]: editResponse.branch_id },
          })
          this._reconcileBranchOperation(tab, realTurn, editResponse.branch_id)
        }
        // The edit is committed server-side from here on — resync
        // failures must not roll it back or reopen the editor.
        try {
          const resynced = await this._resyncHistory(tab)
          if (resynced === false) this._scheduleBranchResync(tab)
        } catch (e) {
          this._scheduleBranchResync(tab)
        }
        return this._branchOperationResult(true, tab, this.branchOperationByTab[tab])
      } catch (e) {
        console.warn("Failed to edit message:", e)
        const error = this._failBranchOperation(tab, e)
        return this._branchOperationResult(false, tab, null, error)
      }
    },

    /** Rewind conversation to a point (drop later messages). */
    async rewindTo(messageIdx) {
      if (!this._instanceId) return
      const tab = this.activeTab
      if (!tab || tab.startsWith("ch:")) return
      try {
        const { agentAPI } = await import("@/utils/api")
        const [sid, cid] = [this._instanceGraphId, tab]
        await agentAPI.rewindTo(sid, cid, messageIdx)
        await this._resyncHistory(tab)
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
    async _resyncHistory(tab = this.activeTab, options = {}) {
      if (!this._instanceId || !tab) return false
      // Per-tab request sequence. Two resyncs for the same tab can be in
      // flight at once (e.g. a processing_end resync racing a branch-op
      // retry); the one that STARTED LATER is authoritative. Capture our
      // id now and, after the await, bail if a newer request has since
      // bumped the counter — applying our older payload would clobber
      // the newer view (resurrect old branch content / prune jobs the
      // newer resync restored).
      const requestId = (this._historyRequestSeqByTab[tab] =
        (this._historyRequestSeqByTab[tab] || 0) + 1)
      const instanceGeneration = options.generation ?? this._instanceGeneration
      const mutationGeneration = this._historyMutationSeqByTab[tab] || 0
      const requestedInstanceId = this._instanceId
      const preFetchMessages = options.initialLoad ? this.messagesByTab[tab] || [] : null
      try {
        const { terrariumAPI } = await import("@/utils/api")
        // Capture BEFORE the fetch: a tab-owned job that starts while
        // this request is in flight is newer than the history it
        // returns, so job-reconciliation must not prune it.
        const fetchedAt = Date.now()
        const data = await terrariumAPI.getHistory(this._instanceGraphId, tab)
        if (
          requestId !== this._historyRequestSeqByTab[tab] ||
          this._instanceId !== requestedInstanceId ||
          this._instanceGeneration !== instanceGeneration ||
          (this._historyMutationSeqByTab[tab] || 0) !== mutationGeneration
        ) {
          return false
        }
        if (!data?.events) {
          if (this._branchResyncPendingByTab[tab]?.active) return false
          if (Array.isArray(data?.messages)) {
            const branchSelection = new Map()
            adoptLocalCommandResultSelections(
              this._pendingCommandResultContextsByTab[tab],
              this._localCommandResultsByTab[tab],
              branchSelection,
            )
            this._setMessages(
              tab,
              mergeLocalCommandResults(
                _convertHistory(data.messages),
                this._localCommandResultsByTab[tab],
                branchSelection,
              ),
            )
          }
          if (data?.is_processing) this.processingByTab[tab] = true
          return true
        }
        if (options.initialLoad && data.events.length === 0 && !data.messages?.length) {
          const messages = (preFetchMessages || []).filter(
            (message) => message.role !== "command_result",
          )
          const branchSelection = new Map()
          adoptLocalCommandResultSelections(
            this._pendingCommandResultContextsByTab[tab],
            this._localCommandResultsByTab[tab],
            branchSelection,
          )
          this._setMessages(
            tab,
            mergeLocalCommandResults(
              messages,
              this._localCommandResultsByTab[tab],
              branchSelection,
            ),
          )
          if (data?.is_processing) this.processingByTab[tab] = true
          return true
        }

        // Out-of-order guard by content: a response whose newest
        // persisted event predates what we already applied for this tab
        // is stale and must not clobber it. event_id is monotonic and
        // append-only, so a lower max means an earlier backend snapshot.
        // Branch ops are exempt — a pending regen/edit legitimately
        // presents a smaller live view until the new branch lands, and
        // the completeness logic below governs that case.
        const branchOpActive = !!this._branchResyncPendingByTab[tab]?.active
        const incomingMax = this._maxEventId(data.events)
        const watermark = this._appliedMaxEventIdByTab[tab]
        if (
          !branchOpActive &&
          incomingMax != null &&
          watermark != null &&
          incomingMax < watermark
        ) {
          return true
        }

        // Check completeness BEFORE touching state. If a branch op is
        // pending (regen / edit-and-rerun) and the expected new branch
        // hasn't landed in /history yet, do NOT clobber the optimistic
        // ``eventsByTab`` / ``messagesByTab`` with stale data.
        //
        // Bug class fixed here (user-reported): with an edit pending
        // for ``turn=1, expected_branch=2``, a /history fetch that
        // races ahead of backend persistence may return ONLY old
        // branch events. ``_resolveSelectedBranches`` then falls back
        // to ``Math.max(candidates)=1`` (old branch), and the rebuild
        // POPS the old branch's content onto the screen — even though
        // ``branchView[1]=2``. Plus this used to return ``false`` →
        // ``ChatMessage.confirmEdit`` re-opens the edit panel with
        // the user's text. Guarding the rebuild keeps the optimistic
        // UI visible until the new branch lands.
        const pending = this._branchResyncPendingByTab[tab]
        const expectedBranchByTurn = pending?.expectedBranchByTurn || {}
        let complete = true
        if (Object.keys(expectedBranchByTurn).length) {
          const parentPaths = _indexParentPaths(data.events)
          const expected = new Map(
            Object.entries(expectedBranchByTurn).map(([turn, branch]) => [
              Number(turn),
              Number(branch),
            ]),
          )
          complete = [...expected].every(([turn, branch]) =>
            data.events.some((evt) => {
              if (evt?._optimistic || evt?.turn_index !== turn || evt?.branch_id !== branch) {
                return false
              }
              const path = parentPaths.get(evt?.event_id) || _coercePath(evt?.parent_branch_path)
              return [...expected].every(([parentTurn, parentBranch]) => {
                if (parentTurn >= turn) return true
                return path.some(([t, b]) => t === parentTurn && b === parentBranch)
              })
            }),
          )
        } else if (pending?.active && Object.hasOwn(pending, "baselineMaxEventId")) {
          const fetchedMax = this._maxEventId(data.events)
          if (pending.baselineMaxEventId != null) {
            complete = fetchedMax != null && fetchedMax > pending.baselineMaxEventId
          } else {
            const physicalFingerprint = JSON.stringify(
              data.events
                .filter((evt) => !evt?._optimistic)
                .map((evt) => [evt?.type, evt?.turn_index, evt?.branch_id, evt?.content]),
            )
            complete = physicalFingerprint !== pending.baselinePhysicalFingerprint
          }
        }

        if (!complete) {
          // Don't replace events / rebuild — keep optimistic state
          // visible. Schedule a retry so the navigator eventually
          // catches up once the new branch is fully written.
          this._scheduleBranchResync(tab)
          // Return true so callers (editMessage / regenerate) don't
          // treat this as a hard failure that re-opens the edit panel.
          // The retry timer will reconcile when the backend catches up.
          return true
        }

        // Cache fresh events. PRESERVE the user's branch overrides:
        // wiping ``branchViewByTab`` here was the historical source of
        // "I switched to branch 1 of turn 2, did an unrelated action,
        // and was yanked back to the latest branch."
        if (!this.branchViewByTab[tab]) this.branchViewByTab[tab] = {}
        const prepared = _prepareReplayEvents(data.events, this.branchViewByTab[tab])
        this._setEvents(tab, prepared.events)
        this._restoreTokenUsage(tab, prepared.events, true)
        this._rebuildMessages(tab, fetchedAt, prepared)
        const scope = scopeOfStoreId(this.$id) || "default"
        this.attentionByTab[tab] = restoreAttentionFromHistory(
          this.eventsByTab[tab],
          this.attentionByTab[tab] || createAttentionState(),
        )
        publishAttention(scope, tab, this.attentionByTab[tab])
        // Advance the applied-history watermark so a later out-of-order
        // response carrying an older snapshot is rejected above.
        if (incomingMax != null) this._appliedMaxEventIdByTab[tab] = incomingMax
        // Restore a running turn's flag (e.g. after a cap-drop forced
        // it off); the false edge stays WS-owned (idle frame).
        if (data.is_processing === true) this.processingByTab[tab] = true
        delete this._branchResyncPendingByTab[tab]
        return true
      } catch (e) {
        console.warn("Failed to resync history:", e)
        if (options.suppressErrors) return false
        throw e
      }
    },

    /**
     * Rebuild ``messagesByTab[tab]`` from the cached event log,
     * applying the current ``branchViewByTab[tab]`` override.
     */
    _rebuildMessages(tab, fetchedAt = null, prepared = null) {
      const events = this.eventsByTab[tab]
      if (!events) return
      const branchView = this.branchViewByTab[tab] || null
      // Canonical history is the liveness authority (see _loadHistory):
      // the backend withholds synthetic terminals for still-live jobs,
      // so a terminal in the cached log means the job is dead.
      const replay = prepared
        ? _replayPreparedEvents([], prepared, tab)
        : _replayEvents([], events, branchView, tab)
      const { messages, pendingJobs } = replay
      const branchSelection = replay.branchMetadata.branchSelection
      adoptLocalCommandResultSelections(
        this._pendingCommandResultContextsByTab[tab],
        this._localCommandResultsByTab[tab],
        branchSelection,
      )
      this._setMessages(
        tab,
        mergeLocalCommandResults(messages, this._localCommandResultsByTab[tab], branchSelection),
      )
      // Canonical history is authoritative for this tab's running jobs.
      // ``fetchedAt`` (set only by _resyncHistory) unlocks removals of
      // tab-owned jobs the history no longer lists; branch-switch
      // rebuilds pass null and only add/merge.
      this._reconcileRunningJobs(tab, pendingJobs, fetchedAt)
    },

    /**
     * Switch the active branch for a turn. Re-runs replay against
     * the cached event log; no network round-trip.
     *
     * When the user switches AWAY from the streaming branch (or
     * back to it), schedule a quick resync so any chunks dropped
     * by the branch-isolation gate while they were elsewhere get
     * pulled in from the persisted event log.
     */
    selectBranch(turnIndex, branchId, tabOverride = null) {
      const tab = tabOverride || this.activeTab
      if (!tab || this.branchOperationByTab[tab]) return false
      if (!this.branchViewByTab[tab]) this.branchViewByTab[tab] = {}
      this.branchViewByTab[tab][turnIndex] = branchId
      this._rebuildMessages(tab)
      // If a stream is still in flight on this tab, an off-branch
      // switch may have missed chunks; the on-branch path back also
      // benefits from a fresh pull of persisted chunks.
      if (this._streamingBranchByTab[tab]) {
        this._scheduleBranchResync(tab)
      }
      return true
    },

    /**
     * Replace a tab's message array wholesale — the single choke point
     * every rebuild goes through. Reuses previous message/part objects
     * by id so component props keep their identity, then refreshes the
     * per-tab lookup indexes.
     */
    _setMessages(tab, next) {
      const prev = this.messagesByTab[tab]
      if (Array.isArray(prev) && Array.isArray(next)) {
        const byId = new Map()
        for (const m of prev) if (m?.id != null) byId.set(m.id, m)
        for (let i = 0; i < next.length; i++) {
          const fresh = next[i]
          const old = fresh?.id != null ? byId.get(fresh.id) : undefined
          if (old && old !== fresh) {
            _mergeMessageInPlace(old, fresh)
            next[i] = old
          }
        }
      }
      this.messagesByTab[tab] = next
      this._rebuildMessageIndexes(tab)
    },

    /** Restore a previously captured array (rollback) — identities are
     * already correct; only the indexes need a rebuild. */
    _restoreMessages(tab, arr) {
      this.messagesByTab[tab] = arr
      this._rebuildMessageIndexes(tab)
    },

    _rebuildMessageIndexes(tab) {
      if (!tab) return
      const idx = _tabIndexes(this, tab)
      idx.tools.clear()
      idx.channelIds.clear()
      const msgs = this.messagesByTab[tab]
      if (!Array.isArray(msgs)) return
      if (tab.startsWith("ch:")) {
        for (const m of msgs) if (m?.id != null) idx.channelIds.add(m.id)
      }
      for (const m of msgs) _indexMsgToolsInto(idx, m)
    },

    _indexMessage(tab, msg) {
      if (!tab || !msg) return
      const idx = _tabIndexes(this, tab)
      if (tab.startsWith("ch:") && msg.id != null) idx.channelIds.add(msg.id)
      _indexMsgToolsInto(idx, msg)
    },

    _indexToolPart(tab, part, msgs = null) {
      if (!tab) return
      if (part?.type !== "tool" || !part.jobId) return
      // ``part`` is the raw object the caller constructed; the array
      // push makes Vue create a reactive proxy around it. The job-id
      // index must hold that proxy — mutating the raw target bypasses
      // Vue's triggers and leaves computed status icons cached as
      // "running" even after tool_done/tool_error.
      if (Array.isArray(msgs) && msgs.length) {
        const tail = msgs[msgs.length - 1]
        const parts = tail?.parts
        const rendered = Array.isArray(parts) ? parts[parts.length - 1] : null
        if (rendered && (rendered.id === part.id || rendered.jobId === part.jobId)) {
          part = rendered
        }
      }
      _tabIndexes(this, tab).tools.set(part.jobId, part)
    },

    _hasChannelMessage(tab, id) {
      const idx = _tabIndexes(this, tab)
      if (idx.channelIds.size === 0) {
        const msgs = this.messagesByTab[tab]
        if (Array.isArray(msgs)) {
          for (const m of msgs) if (m?.id != null) idx.channelIds.add(m.id)
        }
      }
      return idx.channelIds.has(id)
    },

    /**
     * Find a tool part by job_id (reliable, any status) or name (running only).
     * Job-id lookups hit the per-tab index; the scans below are the
     * fallback for parts the index hasn't seen (and reseed it).
     */
    _findToolPart(tab, msgs, name, jobId) {
      if (jobId) {
        const hit = _tabIndexes(this, tab).tools.get(jobId)
        if (hit) return hit
      }
      for (let i = msgs.length - 1; i >= 0; i--) {
        const msg = msgs[i]
        if (!msg.parts) continue
        for (let j = msg.parts.length - 1; j >= 0; j--) {
          const p = msg.parts[j]
          if (p.type !== "tool") continue
          // Match by job_id: any status (handles replay "running" + live "running")
          if (jobId && p.jobId === jobId) {
            if (tab) _tabIndexes(this, tab).tools.set(jobId, p)
            return p
          }
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
     * Find a sub-agent part. Indexed job_id match first, then name,
     * then any running sub-agent (scans below double as the index
     * seeding fallback).
     */
    _findSubagentPart(tab, msgs, saName, saJobId) {
      // 1. Match by job_id (most reliable - connects sub-agent tool events to parent)
      if (saJobId) {
        const hit = _tabIndexes(this, tab).tools.get(saJobId)
        if (hit && hit.kind === "subagent") return hit
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
      // Branch isolation: a user_input echo for a non-viewed branch
      // (e.g. another tab branched while this tab was on branch 1)
      // must not push into the visible message list.
      if (!this._frameMatchesViewedBranch(source, data)) return
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

    _handleUserInputInjected(source, data) {
      // Backend just folded a buffered ``user_input`` into the
      // current turn (Feat 3 mid-turn drain). If the FE had a
      // matching ``queuedMessagesByTab[source]`` entry — the typical
      // path when the user typed during processing — promote that
      // exact entry to the visible chat. If no match exists (e.g.
      // a programmatic/trigger injection), append a fresh user
      // bubble so the model's view stays consistent with the chat.
      if (!source) return
      if (!this.messagesByTab[source]) return
      const queue = this.queuedMessagesByTab[source] || []
      const target = contentSignature(data.content || "")
      const targetText = textSignature(data.content || "")
      // Try strict JSON signature match first (catches the common
      // case where FE queued contentParts exactly match the backend's
      // drained content). Fall back to a text-only comparator so a
      // queue entry whose shape diverged from the backend's drained
      // form (e.g. backend emitted a plain string while FE queued a
      // content-parts list) still pops the right entry instead of
      // sticking the banner forever AND appending a phantom bubble.
      let idx =
        typeof data.pending_id === "string"
          ? queue.findIndex((message) => message.eventId === data.pending_id)
          : -1
      if (idx === -1) {
        idx = queue.findIndex((m) => contentSignature(m.contentParts || m.content || "") === target)
      }
      if (idx === -1 && targetText) {
        idx = queue.findIndex(
          (m) => textSignature(m.contentParts || m.content || "") === targetText,
        )
      }
      if (idx !== -1) {
        // Snapshot a fresh object before splicing — mutating + reusing
        // the reactive proxy that just left ``queuedMessagesByTab`` has
        // historically caused render hiccups when both collections
        // briefly track the same identity. Building a clean clone
        // detaches the new message from the queue's reactivity graph.
        const original = queue[idx]
        queue.splice(idx, 1)
        // Close the currently-streaming assistant (if any) before
        // pushing user(B). Without this, the next text_chunk's
        // ``_ensureAssistantMsg`` reuses the same assistant because
        // it's still ``_streaming``, folding round-2 chunks into the
        // round-1 bubble — same bug class as the replay path.
        this._closeStreamingAssistant(source)
        this._addMsg(source, {
          id: original.id,
          role: "user",
          content: original.content,
          contentParts: original.contentParts,
          eventId: original.eventId,
          timestamp: original.timestamp,
          injectedMidTurn: true,
        })
        return
      }
      // Programmatic / trigger injection — no FE counterpart.
      const normalized = normalizeMessageContent(data.content || "")
      const now = Date.now()
      this._closeStreamingAssistant(source)
      this._addMsg(source, {
        id: `u_inj_${now}`,
        role: "user",
        content: normalized.content,
        contentParts: normalized.contentParts,
        timestamp: data.timestamp || new Date((data.ts || now / 1000) * 1000).toISOString(),
        injectedMidTurn: true,
      })
    },

    _closeStreamingAssistant(source) {
      // Mark the currently-streaming assistant (last in
      // ``messagesByTab[source]``) as finished so the NEXT text_chunk
      // starts a fresh assistant via ``_ensureAssistantMsg`` instead
      // of folding into the old one. Used by mid-turn injection paths
      // to keep the interleaving order
      //   [user(A), assistant("to-A"), user(B), assistant("to-B")]
      // visible to the user instead of merging round 1 + round 2 text
      // into one bubble.
      const msgs = this.messagesByTab[source]
      if (!msgs || !msgs.length) return
      const last = msgs[msgs.length - 1]
      if (!last || last.role !== "assistant") return
      last._streaming = false
      if (Array.isArray(last.parts)) {
        for (const p of last.parts) {
          if (p && p.type === "text") p._streaming = false
        }
      }
    },

    _handleChannelMessage(data) {
      const tabKey = `ch:${data.channel}`

      if (this.messagesByTab[tabKey]) {
        const existing = this.messagesByTab[tabKey]
        if (data.message_id && this._hasChannelMessage(tabKey, data.message_id)) {
          return
        }
        const normalized = normalizeMessageContent(data.content)
        const pushed = {
          id: data.message_id || "ch_" + Date.now(),
          role: "channel",
          sender: data.sender,
          content: normalized.content,
          contentParts: normalized.contentParts,
          timestamp: data.timestamp,
        }
        existing.push(pushed)
        this._indexMessage(tabKey, pushed)
        if (this.activeTab !== tabKey) {
          this.unreadCounts[tabKey] = (this.unreadCounts[tabKey] || 0) + 1
        }
      }

      const msgStore = useMessagesStore(scopeOfStoreId(this.$id))
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

    /** Move queued messages for ``source`` from its hold queue into the
     *  main message list. Other tabs' queues are untouched — they each
     *  flush on their own ``processing_start``. */
    _promoteQueuedMessages(source) {
      if (!source) return
      const queue = this.queuedMessagesByTab[source]
      if (!queue || !queue.length) return
      const msgs = this.messagesByTab[source]
      if (!msgs) return
      for (const msg of queue) {
        delete msg.queued
        delete msg.queuedTab
        msgs.push(msg)
      }
      this.queuedMessagesByTab[source] = []
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

    _commitPendingReasoningRound(message) {
      if (!message || !Array.isArray(message.parts)) return
      if (!Number.isInteger(message._pendingReasoningCursor)) return
      message._reasoningCursor = message._pendingReasoningCursor
      message._pendingReasoningCursor = undefined
      const tail = message.parts.length ? message.parts[message.parts.length - 1] : null
      if (tail?.type === "text") {
        tail._roundEnd = true
        tail._streaming = false
      }
    },

    _appendStreamChunk(source, content) {
      const msgs = this.messagesByTab[source]
      if (!msgs) return
      this._historyMutationSeqByTab[source] = (this._historyMutationSeqByTab[source] || 0) + 1
      const last = this._ensureAssistantMsg(msgs)
      this._commitPendingReasoningRound(last)
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
      this._commitPendingReasoningRound(last)
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
      // The branch the user just generated on becomes "frozen" — no
      // more chunks arrive for it. The next resync rebuilds messages
      // from canonical events (including the persisted text_chunks),
      // so we drop the streaming-branch target here to free the
      // navigator from the "this is a live target" gate.
      if (source && this._streamingBranchByTab[source]) {
        delete this._streamingBranchByTab[source]
      }
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
      if (
        msg?.role === "user" &&
        typeof msg?.eventId === "string" &&
        msg.eventId.startsWith("c_") &&
        this._pendingCommandResultContextsByTab[tabKey]?.length
      ) {
        const pending = this._pendingCommandResultContextsByTab[tabKey] || []
        const currentSelection = _commandResultBranchSelection(
          this.eventsByTab[tabKey],
          this.branchViewByTab[tabKey] || null,
        )
        const remaining = bindLocalCommandResultContexts(
          pending,
          this._localCommandResultsByTab[tabKey],
          msg,
          currentSelection,
        )
        if (remaining.length) {
          this._pendingCommandResultContextsByTab[tabKey] = remaining
        } else {
          delete this._pendingCommandResultContextsByTab[tabKey]
        }
      }
      this.messagesByTab[tabKey].push(msg)
      this._indexMessage(tabKey, msg)
      this._historyMutationSeqByTab[tabKey] = (this._historyMutationSeqByTab[tabKey] || 0) + 1
    },

    captureCommandResultContext(tabKey) {
      const events = this.eventsByTab[tabKey]
      const branchSelection = _commandResultBranchSelection(
        events,
        this.branchViewByTab[tabKey] || null,
      )
      return captureLocalCommandResultContext(
        this.messagesByTab[tabKey],
        Array.isArray(events),
        branchSelection,
      )
    },

    registerCommandResultContext(tabKey) {
      const context = registerLocalCommandResultContext(
        this.captureCommandResultContext(tabKey),
        ++this._commandResultDispatchSeq,
      )
      if (!this._pendingCommandResultContextsByTab[tabKey]) {
        this._pendingCommandResultContextsByTab[tabKey] = []
      }
      this._pendingCommandResultContextsByTab[tabKey].push(context)
      return context
    },

    releaseCommandResultContext(tabKey, context) {
      const pending = this._pendingCommandResultContextsByTab[tabKey]
      if (!pending?.length || !context) return
      const remaining = releaseLocalCommandResultContext(pending, context)
      if (remaining.length) {
        this._pendingCommandResultContextsByTab[tabKey] = remaining
      } else {
        delete this._pendingCommandResultContextsByTab[tabKey]
      }
    },

    addCommandResult(tabKey, commandText, response, context = null) {
      if (!tabKey) return
      const resultContext = context ?? this.captureCommandResultContext(tabKey)
      const message = buildLocalCommandResultMessage({
        id: `command_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
        commandText,
        response,
        context: resultContext,
        timestamp: new Date().toISOString(),
      })
      if (!this._localCommandResultsByTab[tabKey]) {
        this._localCommandResultsByTab[tabKey] = []
      }
      this._localCommandResultsByTab[tabKey].push(message)
      const canonical = (this.messagesByTab[tabKey] || []).filter(
        (current) => current.role !== "command_result",
      )
      const currentSelection = _commandResultBranchSelection(
        this.eventsByTab[tabKey],
        this.branchViewByTab[tabKey] || null,
      )
      this._setMessages(
        tabKey,
        mergeLocalCommandResults(
          canonical,
          this._localCommandResultsByTab[tabKey],
          currentSelection,
        ),
      )
      if (
        Number.isInteger(resultContext?.dispatchSeq) &&
        !Number.isInteger(message._anchorIndex) &&
        !resultContext.beforeMessageId &&
        !resultContext.beforeEventId
      ) {
        resultContext.resultAdded = true
      } else {
        this.releaseCommandResultContext(tabKey, resultContext)
      }
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
      this._instanceGraphId = null
      this._instanceType = null
      this.tabs = []
      this.messagesByTab = {}
      this.tokenUsage = {}
      this.subagentUsageByJob = {}
      this.runningJobs = {}
      this.unreadCounts = {}
      this.attentionByTab = {}
      this._attentionStreamTextByTab = {}
      removeAttentionScope(scopeOfStoreId(this.$id) || "default")
      this.queuedMessagesByTab = {}
      this.processingByTab = {}
      this.eventsByTab = {}
      this._localCommandResultsByTab = {}
      this._pendingCommandResultContextsByTab = {}
      this._commandResultDispatchSeq = 0
      this.branchViewByTab = {}
      this.branchOperationByTab = {}
      this.branchOperationErrorByTab = {}
      this._recentUserInputs = {}
      this._branchResyncPendingByTab = {}
      this._streamingBranchByTab = {}
      this._historyRequestSeqByTab = {}
      this._appliedMaxEventIdByTab = {}
      this._clearBranchResyncTimers()
      // Drop multi-group state along with the legacy buckets — the
      // next ``initForInstance`` runs for a different scope.
      this.groups = {}
      this.groupTree = null
      this.focusedGroupId = null
      this._groupCounter = 0
      this.sessionInfo = {
        sessionId: "",
        model: "",
        llmName: "",
        agentName: "",
        configName: "",
        configRef: "",
        compactThreshold: 0,
        maxContext: 0,
        homeNode: "_host",
      }
      this.modelByTab = {}
      this._rootSourceName = null
      const statusStore = useStatusStore(scopeOfStoreId(this.$id))
      statusStore.reset()
    },

    _cleanup() {
      this.activeTab = null
      this._historyLoaded = false
      this._wsBuffer = []
      this.branchOperationByTab = {}
      this.branchOperationErrorByTab = {}
      this._branchResyncPendingByTab = {}
      this._streamingBranchByTab = {}
      this._historyRequestSeqByTab = {}
      this._pendingCommandResultContextsByTab = {}
      this._appliedMaxEventIdByTab = {}
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
      // Per-instance tab state is transient UI state, not a UI
      // setting — keep it in localStorage only so tab churn never
      // reaches the ui-prefs API.
      writeLocalJsonPref(key, {
        tabs: this.tabs,
        activeTab: this.activeTab,
      })
    },

    _restoreTabs() {
      if (!this._instanceId) return
      const key = `chat-tabs-${this._instanceId}`
      const saved = readLocalJsonPref(key, null)
      if (saved?.tabs?.length) {
        for (const tab of saved.tabs) {
          this._addTab(tab)
        }
        if (saved.activeTab && this.tabs.includes(saved.activeTab)) {
          this.activeTab = saved.activeTab
        }
      }
    },

    // ─── Multi-chat-panel actions (Option E) ─────────────────────
    //
    // Group state is the source of truth ONLY when ``groupTree`` is
    // non-null. Otherwise legacy ``tabs`` / ``activeTab`` are
    // authoritative and the group bucket is empty. The transition
    // happens lazily via ``enableGroups()`` (called when the user
    // splits for the first time or when the user toggles the
    // Settings flag mid-session).
    //
    // Every group-mutating action calls ``_syncLegacyFromGroups()``
    // before returning so back-compat readers (chat-store internals,
    // the v1 ``SessionHistoryViewer`` viewer, tests) keep observing
    // a consistent ``tabs`` / ``activeTab``.

    /** Mirror ``tabs[]`` and ``activeTab`` from the current group
     *  state. Called after every group mutation. Safe to call when
     *  ``groupTree`` is null — does nothing in that case. */
    _syncLegacyFromGroups() {
      if (!this.groupTree) return
      const union = _unionGroupTabs(this.groups, this.groupTree)
      this.tabs = union
      const focused = this.groups[this.focusedGroupId]
      const nextActive = focused?.activeTab || union[0] || null
      if (this.activeTab !== nextActive) this.activeTab = nextActive
    },

    /** Persist groups + groupTree + focusedGroupId to localStorage
     *  under ``kt.chat.groupTree.<scope>``. Schema version ``1`` so
     *  future shape changes can migrate. localStorage only — group
     *  churn is transient UI state and must not hit the ui-prefs
     *  API. */
    _persistGroupState() {
      const scope = this._instanceId || "default"
      const key = _groupStorageKey(scope)
      if (!this.groupTree) {
        // No groups active — clear any stale storage so the next
        // load doesn't resurrect a stale tree.
        try {
          writeLocalJsonPref(key, null)
        } catch {
          /* swallow */
        }
        return
      }
      const payload = {
        version: 1,
        groups: this.groups,
        groupTree: this.groupTree,
        focusedGroupId: this.focusedGroupId,
        _groupCounter: this._groupCounter,
      }
      try {
        writeLocalJsonPref(key, payload)
      } catch {
        /* swallow — storage may be unavailable */
      }
    },

    /** Read persisted group state for the current scope. Returns
     *  ``true`` if a valid version-1 payload was applied. */
    _loadGroupState() {
      const scope = this._instanceId || "default"
      const key = _groupStorageKey(scope)
      let saved = null
      try {
        saved = readLocalJsonPref(key, null)
      } catch {
        return false
      }
      if (!saved || saved.version !== 1) return false
      if (!saved.groupTree || !saved.groups) return false
      // Sanity: every leaf groupId must exist in groups.
      let ok = true
      _walkGroupTree(saved.groupTree, (gid) => {
        if (!saved.groups[gid]) ok = false
      })
      if (!ok) return false
      this.groups = saved.groups
      this.groupTree = saved.groupTree
      this.focusedGroupId =
        saved.focusedGroupId && saved.groups[saved.focusedGroupId]
          ? saved.focusedGroupId
          : _firstLeafGroupId(saved.groupTree)
      this._groupCounter = saved._groupCounter || Object.keys(saved.groups).length
      this._syncLegacyFromGroups()
      return true
    },

    /** Activate multi-group mode by wrapping the current ``tabs`` /
     *  ``activeTab`` into a single group + single-leaf tree. Safe to
     *  call when already active (no-op). */
    enableGroups() {
      if (this.groupTree) return this.focusedGroupId
      const legacyTabs = Array.isArray(this.tabs) ? [...this.tabs] : []
      const legacyActive = this.activeTab || legacyTabs[0] || null
      this._groupCounter += 1
      const id = `g_${this._groupCounter}`
      this.groups = {
        [id]: {
          tabs: legacyTabs,
          activeTab: legacyActive,
          draftText: "",
        },
      }
      this.groupTree = { type: "leaf", groupId: id }
      this.focusedGroupId = id
      this._syncLegacyFromGroups()
      this._persistGroupState()
      return id
    },

    /** Deactivate multi-group mode — collapse back to legacy single-
     *  group state. Tabs/activeTab survive the transition (taken from
     *  the previously-focused group, falling back to the union). */
    disableGroups() {
      if (!this.groupTree) return
      // Preserve the focused group's tabs/activeTab as the new
      // single-surface state — that's the surface the user was last
      // looking at, so least surprise.
      const focused = this.groups[this.focusedGroupId]
      const union = _unionGroupTabs(this.groups, this.groupTree)
      const nextTabs = focused?.tabs?.length ? [...focused.tabs] : union
      const nextActive = focused?.activeTab || nextTabs[0] || null
      this.groups = {}
      this.groupTree = null
      this.focusedGroupId = null
      this.tabs = nextTabs
      this.activeTab = nextActive
      // Clear storage so a future page-load doesn't auto-re-enable.
      this._persistGroupState()
    },

    /** Allocate a new group with the given tabs. ``activeTab``
     *  defaults to the first tab. Returns the new groupId. Does NOT
     *  insert the group into ``groupTree`` — callers are responsible
     *  for placing it (e.g. via ``splitGroup``). */
    addGroup(tabs = [], activeTab = null) {
      this._groupCounter += 1
      const id = `g_${this._groupCounter}`
      const tabList = Array.isArray(tabs) ? [...tabs] : []
      this.groups[id] = {
        tabs: tabList,
        activeTab: activeTab || tabList[0] || null,
        draftText: "",
      }
      return id
    },

    /** Remove a group: drops its entry from ``groups`` and prunes its
     *  leaf from ``groupTree``. Promotes the surviving sibling when a
     *  split collapses. If the focused group is removed, focus jumps
     *  to the new first leaf (or ``null`` when nothing is left). */
    removeGroup(groupId) {
      if (!this.groups[groupId]) return
      delete this.groups[groupId]
      this.groupTree = _pruneTreeLeaf(this.groupTree, groupId)
      if (!this.groupTree) {
        // Last group removed → fall back to legacy single-group
        // mode so the chat panel keeps rendering something.
        this.focusedGroupId = null
        this.groups = {}
        // Keep tabs / activeTab as-is so the panel re-renders the
        // last view rather than going blank.
      } else if (this.focusedGroupId === groupId) {
        this.focusedGroupId = _firstLeafGroupId(this.groupTree)
      }
      this._syncLegacyFromGroups()
      this._persistGroupState()
    },

    /** Set a group's active tab. No-op if the group doesn't exist or
     *  the tab isn't in the group's tab list. */
    setGroupActiveTab(groupId, tab) {
      const g = this.groups[groupId]
      if (!g || !g.tabs.includes(tab)) return
      g.activeTab = tab
      if (tab) delete this.unreadCounts[tab]
      this._syncLegacyFromGroups()
      this._persistGroupState()
    },

    /** Bring a group to keyboard focus. Drives which group receives
     *  the global ``/`` hotkey, where new tabs land, and what the
     *  StatusBar model switcher reads. */
    setFocusedGroup(groupId) {
      if (!this.groups[groupId]) return
      if (this.focusedGroupId === groupId) return
      this.focusedGroupId = groupId
      this._syncLegacyFromGroups()
      this._persistGroupState()
    },

    /** Resize the split at ``path`` (array of child indices) to
     *  ``ratio``. Path ``[]`` targets the root split. Mutates the
     *  ``groupTree`` in place — see ``_setSplitRatio`` for why.
     *  Persistence runs on every call; cheap localStorage writes
     *  during a drag are acceptable, and committing on every move
     *  means a refresh mid-drag preserves what the user saw. */
    setGroupSplitRatio(path, ratio) {
      if (!this.groupTree) return
      _setSplitRatio(this.groupTree, path || [], ratio)
      this._persistGroupState()
    },

    /** Split the target group's leaf in two. The target group stays
     *  on one side, a fresh group lands on the other (carrying
     *  ``movedTab`` as its single tab, or empty when ``movedTab`` is
     *  ``null``).
     *
     *  When ``movedTab`` originated in a DIFFERENT group (a drag-to-
     *  split across the chat-internal tree), pass that group's id as
     *  ``srcGroupId`` so the tab is removed from there. Without it,
     *  the tab would be duplicated (left in the source AND in the new
     *  split's sibling) — that's the
     *  ``a|b|c drag c onto a's side → a|c|b|c`` bug.
     *
     *  Returns the new groupId, or ``null`` on no-op. */
    splitGroup(targetGroupId, direction, edge, movedTab = null, srcGroupId = null) {
      if (!this.groups[targetGroupId]) return null
      if (direction !== "horizontal" && direction !== "vertical") return null
      if (edge !== "before" && edge !== "after") return null
      // Refuse the split if it would empty the source group with no
      // sibling to promote — collapse-then-split races the
      // tree-mutation reflow. The "moving a tab is the only way to
      // create a split" path doesn't apply to keyboard / context-menu
      // splits which pass ``movedTab=null``.
      const realSrcId = srcGroupId || targetGroupId
      if (movedTab) {
        const realSrc = this.groups[realSrcId]
        if (!realSrc || !realSrc.tabs.includes(movedTab)) return null
        if (realSrc.tabs.length <= 1 && realSrcId === targetGroupId) return null
      }
      const newId = this.addGroup(movedTab ? [movedTab] : [])
      this.groupTree = _splitTreeLeaf(this.groupTree, targetGroupId, direction, edge, newId)
      if (movedTab) {
        // Remove from the REAL source group; the new group already
        // owns the moved tab via ``addGroup``.
        const realSrc = this.groups[realSrcId]
        const idx = realSrc.tabs.indexOf(movedTab)
        if (idx !== -1) {
          realSrc.tabs.splice(idx, 1)
          if (realSrc.activeTab === movedTab) realSrc.activeTab = realSrc.tabs[0] || null
        }
        // If the source group emptied (cross-group split where src
        // had only that one tab), collapse it. The tree-prune is
        // safe because the split we just did inserted the new leaf
        // beside the target — the empty leaf is unrelated.
        if (realSrcId !== targetGroupId && realSrc.tabs.length === 0) {
          this.removeGroup(realSrcId)
        }
      }
      this.focusedGroupId = newId
      this._syncLegacyFromGroups()
      this._persistGroupState()
      return newId
    },

    /** Move a tab between groups (or reorder within one). When
     *  ``dstIndex`` is past the end, appends. If the source group is
     *  emptied by the move, it is removed and its tree slot collapses. */
    moveTab(srcGroupId, tab, dstGroupId, dstIndex = -1, opts = {}) {
      const src = this.groups[srcGroupId]
      const dst = this.groups[dstGroupId]
      if (!src || !dst) return
      const idx = src.tabs.indexOf(tab)
      if (idx === -1) return
      // Same-group reorder
      if (srcGroupId === dstGroupId) {
        src.tabs.splice(idx, 1)
        const insertAt = dstIndex < 0 ? src.tabs.length : Math.min(dstIndex, src.tabs.length)
        src.tabs.splice(insertAt, 0, tab)
        src.activeTab = tab
        this._syncLegacyFromGroups()
        this._persistGroupState()
        return
      }
      // Cross-group move
      src.tabs.splice(idx, 1)
      if (src.activeTab === tab) src.activeTab = src.tabs[0] || null
      // Remove from destination first (if it was already there); we
      // re-insert at the requested index in canonical position.
      const dstExisting = dst.tabs.indexOf(tab)
      if (dstExisting !== -1) dst.tabs.splice(dstExisting, 1)
      const insertAt = dstIndex < 0 ? dst.tabs.length : Math.min(dstIndex, dst.tabs.length)
      dst.tabs.splice(insertAt, 0, tab)
      dst.activeTab = tab
      if (!opts.preserveFocus) this.focusedGroupId = dstGroupId
      if (src.tabs.length === 0) {
        // Source emptied → collapse the group. ``removeGroup`` will
        // call ``_syncLegacyFromGroups`` + persist on its own.
        this.removeGroup(srcGroupId)
        return
      }
      this._syncLegacyFromGroups()
      this._persistGroupState()
    },

    /** Add ``tab`` to the focused group (or first group) when groups
     *  are active. When groups are inactive, falls through to
     *  ``_addTab`` for legacy single-surface behaviour. Idempotent —
     *  duplicate tabs are no-ops. */
    ensureTab(tab) {
      if (!tab) return
      this._addTab(tab)
      if (!this.groupTree) return
      // Already in any group? Done.
      for (const g of Object.values(this.groups)) {
        if (g.tabs.includes(tab)) return
      }
      const targetId = this.focusedGroupId || _firstLeafGroupId(this.groupTree)
      if (!targetId) return
      const g = this.groups[targetId]
      g.tabs.push(tab)
      if (!g.activeTab) g.activeTab = tab
      this._syncLegacyFromGroups()
      this._persistGroupState()
    },

    /** Remove ``tab`` from every group + the legacy tabs list. Groups
     *  emptied as a result collapse. */
    pruneTab(tab) {
      if (!tab) return
      // Legacy tabs
      const legacyIdx = this.tabs.indexOf(tab)
      if (legacyIdx !== -1) {
        this.tabs = this.tabs.filter((t) => t !== tab)
        if (this.activeTab === tab) {
          this.activeTab = this.tabs[Math.min(legacyIdx, this.tabs.length - 1)] || null
        }
      }
      if (!this.groupTree) return
      // Snapshot ids first — ``removeGroup`` mutates the dict while we iterate.
      const ids = Object.keys(this.groups)
      for (const gid of ids) {
        const g = this.groups[gid]
        if (!g) continue
        const idx = g.tabs.indexOf(tab)
        if (idx === -1) continue
        g.tabs.splice(idx, 1)
        if (g.activeTab === tab) g.activeTab = g.tabs[0] || null
        if (g.tabs.length === 0) this.removeGroup(gid)
      }
      this._syncLegacyFromGroups()
      this._persistGroupState()
    },
  },
}

// ── Per-scope Pinia factory ────────────────────────────────────────
//
// Each unique scope (creature_id / terrarium_id, or the literal
// "default" for the v1 singleton path) gets its own Pinia store
// instance. The factory caches the ``defineStore`` result so the
// SAME use-fn is returned for the SAME scope on every call — Pinia
// then handles instance reuse internally.

const _chatStoreFactories = new Map()

function _factoryFor(scope) {
  const key = scope || "default"
  let useFn = _chatStoreFactories.get(key)
  if (!useFn) {
    useFn = defineStore(`chat:${key}`, _chatStoreOptions)
    _chatStoreFactories.set(key, useFn)
    if (scope) {
      // When the macro shell tabs store closes the last tab carrying
      // this scope, free the chat store + its WS. The default scope
      // ("default") lives forever — v1 never explicitly disposes.
      registerScopeDisposer(scope, () => {
        try {
          removeAttentionScope(scope)
          useFn()._cleanup?.()
          useFn().$dispose?.()
        } catch {
          /* swallow — disposer must not throw */
        }
        _chatStoreFactories.delete(key)
      })
    }
  }
  return useFn
}

/**
 * Resolve the active chat store for the call site.
 *
 * - Explicit ``scope`` argument → that-scoped store. Useful for
 *   programmatic / test access.
 * - Inside a Vue setup with a ``provideScope`` ancestor (the v2
 *   macro shell's ``AttachTab``) → the per-attach store.
 * - Anywhere else → the legacy singleton "default" store, so the v1
 *   page-routed flow and helper composables that run outside a Vue
 *   setup keep working unchanged.
 */
export function useChatStore(scope) {
  if (scope !== undefined) return _factoryFor(scope)()
  if (getCurrentInstance()) {
    return _factoryFor(injectScope())()
  }
  return _factoryFor(null)()
}
