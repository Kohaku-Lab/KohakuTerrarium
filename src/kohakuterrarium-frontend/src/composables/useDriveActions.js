/**
 * Drive record actions with user feedback, shared by every surface that
 * manages drives (the full Drives panel and the compact Creature State tab)
 * so their behaviour cannot drift.
 *
 * Every handler goes through the drives store, which owns CAS revisions,
 * idempotency keys, and conflict reloads; this layer only adds the toast and
 * the create-scope defaulting.
 */

import { ref } from "vue"
import { ElMessage, ElMessageBox } from "element-plus"

function _read(source) {
  if (typeof source === "function") return source()
  if (source && typeof source === "object" && "value" in source) return source.value
  return source
}

export function useDriveActions(store, { sessionId = null } = {}) {
  const saving = ref(false)
  const replaying = ref(null)

  async function runAction(fn, okMsg) {
    const res = await fn()
    if (res.ok) ElMessage.success(okMsg)
    else if (res.conflict) ElMessage.warning("This Drive changed — reloaded the current version.")
    else ElMessage.error(res.detail || "Action failed.")
    return !!res.ok
  }

  /** Create a record; resolves true when the editor may close. */
  async function onCreate(request) {
    saving.value = true
    try {
      // A graph-scoped Drive lives in this session's graph; a creature-scoped
      // one carries the picked creature id from the editor — never override it
      // with the graph id (R1-38).
      const scope_id = request.scope_type === "creature" ? request.scope_id : _read(sessionId)
      const res = await store.create({ ...request, scope_id })
      if (res.ok) {
        ElMessage.success("Drive created.")
        return true
      }
      ElMessage.error(res.detail || "Create failed.")
      return false
    } finally {
      saving.value = false
    }
  }

  /** Save an edit; resolves true when the editor may close. */
  async function onSave({ patch, expectedRevision }) {
    saving.value = true
    try {
      // Thread the editor's captured base revision through unchanged so a stale
      // draft conflicts instead of overwriting a newer record (R1-35).
      const res = await store.update(store.selectedId, patch, expectedRevision)
      if (res.ok) {
        ElMessage.success("Saved.")
        return true
      }
      if (res.conflict) ElMessage.warning("This Drive changed — compare and retry.")
      else ElMessage.error(res.detail || "Save failed.")
      return false
    } finally {
      saving.value = false
    }
  }

  function onTransition(target, opts) {
    return runAction(() => store.transition(store.selectedId, target, opts), `Moved to ${target}.`)
  }

  function onWake() {
    return runAction(() => store.wake(store.selectedId), "Woken.")
  }

  async function onAssign() {
    const id = store.selectedId
    try {
      const { value } = await ElMessageBox.prompt("Creature id to assign", "Assign Drive", {
        inputPlaceholder: "creature-id",
      })
      if (value) return await runAction(() => store.assign(id, value), "Assigned.")
    } catch {
      /* cancelled */
    }
    return false
  }

  function onUnassign() {
    return runAction(() => store.unassign(store.selectedId), "Unassigned.")
  }

  async function onTransferOwner() {
    const id = store.selectedId
    try {
      const { value } = await ElMessageBox.prompt("New owner (e.g. user:alice)", "Transfer owner", {
        inputPlaceholder: "kind:identity",
      })
      if (value) return await runAction(() => store.setOwner(id, value), "Owner transferred.")
    } catch {
      /* cancelled */
    }
    return false
  }

  function onProgress(summary) {
    return runAction(() => store.reportProgress(store.selectedId, summary), "Progress recorded.")
  }

  async function onProposeTerminal() {
    const res = await store.propose(store.selectedId, "completed")
    if (res.ok && res.pending) ElMessage.info("Completion proposed — awaiting verification.")
    else if (res.ok) ElMessage.success("Completed.")
    else if (res.conflict) ElMessage.warning("This Drive changed — reloaded the current version.")
    else ElMessage.error(res.detail || "Propose failed.")
    return !!res.ok
  }

  function onVerifyTerminal(approved) {
    if (approved) return runAction(() => store.approve(store.selectedId), "Approved.")
    // No dedicated reject route; block the Drive so it leaves the pending
    // terminal state and can be reworked.
    return runAction(
      () => store.transition(store.selectedId, "blocked", { reason: "verification rejected" }),
      "Rejected.",
    )
  }

  async function onReplay(deliveryId) {
    replaying.value = deliveryId
    try {
      const res = await store.replayDelivery(store.selectedId, deliveryId)
      if (res.ok) ElMessage.success("Delivery replayed.")
      else ElMessage.error(res.detail || "Replay failed.")
      return !!res.ok
    } finally {
      replaying.value = null
    }
  }

  return {
    saving,
    replaying,
    onCreate,
    onSave,
    onTransition,
    onWake,
    onAssign,
    onUnassign,
    onTransferOwner,
    onProgress,
    onProposeTerminal,
    onVerifyTerminal,
    onReplay,
  }
}
