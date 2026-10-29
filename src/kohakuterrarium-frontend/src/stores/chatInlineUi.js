/**
 * Inline ``<ui>`` block state for the chat store.
 *
 * A block is addressed by its reply's turn and branch plus a block id (the
 * FNV-1a hash of its source and its occurrence; see ``ktui/blockId.js``).
 * State recorded in the session arrives as ``inline_ui_state`` events in the
 * history and as live frames; local edits are debounced and sent as
 * ``inline_ui_state`` frames. Edits made before the reply has a turn are held
 * per block id and claimed once the block is addressable.
 */

import { markRaw } from "vue"

export const INLINE_UI_STATE_DEBOUNCE_MS = 400

export function inlineUiKey(turn, branch, block) {
  return `${turn}:${branch}:${block}`
}

function isAddress(turn, branch, block) {
  return (
    Number.isInteger(turn) && Number.isInteger(branch) && typeof block === "string" && block !== ""
  )
}

/** The latest recorded state per block key in a session event list. */
export function inlineUiStatesFromEvents(events) {
  const out = {}
  for (const evt of events || []) {
    if (evt?.type !== "inline_ui_state") continue
    if (!isAddress(evt.ui_turn, evt.ui_branch, evt.block)) continue
    if (!evt.state || typeof evt.state !== "object") continue
    out[inlineUiKey(evt.ui_turn, evt.ui_branch, evt.block)] = { ...evt.state }
  }
  return out
}

/** Store actions; spread into the chat store's ``actions``. */
export const inlineUiActions = {
  /** The known state of one block, or ``null``. */
  inlineUiStateOf(tab, turn, branch, block) {
    if (!isAddress(turn, branch, block)) return null
    return this.inlineUiStateByTab[tab]?.[inlineUiKey(turn, branch, block)] || null
  },

  /** Merge ``patch`` into a block's state now and send it, debounced per block. */
  setInlineUiState(tab, turn, branch, block, patch) {
    if (!tab || !isAddress(turn, branch, block) || !patch || typeof patch !== "object") return
    const key = inlineUiKey(turn, branch, block)
    const states = this.inlineUiStateByTab[tab] || (this.inlineUiStateByTab[tab] = {})
    states[key] = { ...(states[key] || {}), ...patch }
    if (!this._inlineUiTimers) this._inlineUiTimers = markRaw(new Map())
    const timerKey = `${tab}|${key}`
    const pending = this._inlineUiTimers.get(timerKey)
    if (pending) clearTimeout(pending.timer)
    const state = { ...(pending?.state || {}), ...patch }
    const timer = setTimeout(() => {
      this._inlineUiTimers.delete(timerKey)
      this._sendWidgetFrame({
        type: "inline_ui_state",
        target: tab,
        ui_turn: turn,
        ui_branch: branch,
        block,
        state,
      })
    }, INLINE_UI_STATE_DEBOUNCE_MS)
    this._inlineUiTimers.set(timerKey, { timer, state })
  },

  /** Keep edits of a block whose reply has no turn yet. */
  holdInlineUiState(tab, block, patch) {
    if (!tab || typeof block !== "string" || !patch || typeof patch !== "object") return
    if (!this._inlineUiHeld) this._inlineUiHeld = markRaw(new Map())
    const key = `${tab}|${block}`
    this._inlineUiHeld.set(key, { ...(this._inlineUiHeld.get(key) || {}), ...patch })
  },

  /** Move held edits onto the now-addressable block and send them. */
  claimInlineUiState(tab, turn, branch, block) {
    const key = `${tab}|${block}`
    const held = this._inlineUiHeld?.get(key)
    if (!held) return
    this._inlineUiHeld.delete(key)
    this.setInlineUiState(tab, turn, branch, block, held)
  },

  /** Adopt recorded block state from a (re)loaded event list. */
  _adoptInlineUiStates(tab, events) {
    const recorded = inlineUiStatesFromEvents(events)
    if (!Object.keys(recorded).length) return
    this.inlineUiStateByTab[tab] = { ...(this.inlineUiStateByTab[tab] || {}), ...recorded }
  },

  /** ``inline_ui_state`` frame from another client. */
  _handleInlineUiState(source, data) {
    if (!source || !isAddress(data?.ui_turn, data?.ui_branch, data?.block)) return
    if (!data.state || typeof data.state !== "object") return
    const states = this.inlineUiStateByTab[source] || (this.inlineUiStateByTab[source] = {})
    states[inlineUiKey(data.ui_turn, data.ui_branch, data.block)] = { ...data.state }
  },

  _clearInlineUiTimers() {
    for (const { timer } of this._inlineUiTimers?.values() || []) clearTimeout(timer)
    this._inlineUiTimers?.clear()
    this._inlineUiHeld?.clear()
  },
}
