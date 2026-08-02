function _selectionEntries(selection) {
  if (selection == null) return null
  if (selection instanceof Map) return [...selection.entries()]
  if (Array.isArray(selection)) return selection
  if (typeof selection === "object") return Object.entries(selection)
  return []
}

export function normalizeCommandResultBranchSelection(selection) {
  const entries = _selectionEntries(selection)
  if (entries == null) return null
  return entries
    .map(([turn, branch]) => [Number(turn), Number(branch)])
    .filter(([turn, branch]) => Number.isFinite(turn) && Number.isFinite(branch))
    .sort(([leftTurn, leftBranch], [rightTurn, rightBranch]) => {
      if (leftTurn !== rightTurn) return leftTurn - rightTurn
      return leftBranch - rightBranch
    })
}

export function commandResultViewKey(selection) {
  const normalized = normalizeCommandResultBranchSelection(selection)
  return normalized == null ? "__unknown__" : JSON.stringify(normalized)
}

function _branchSelectionsAreCompatible(dispatchSelection, currentSelection) {
  const dispatch = normalizeCommandResultBranchSelection(dispatchSelection)
  const current = normalizeCommandResultBranchSelection(currentSelection)
  if (dispatch == null || current == null) return true

  const currentByTurn = new Map(current)
  for (const [turn, branch] of dispatch) {
    if (currentByTurn.has(turn) && currentByTurn.get(turn) !== branch) return false
  }
  return true
}

function _adoptKnownSelection(target, currentSelection, key = "branchSelection") {
  if (!target || target[key] != null || currentSelection == null) return
  target[key] = normalizeCommandResultBranchSelection(currentSelection)
}

export function adoptLocalCommandResultSelections(pending, commandResults, currentSelection) {
  const normalizedCurrent = normalizeCommandResultBranchSelection(currentSelection)
  if (normalizedCurrent == null) return

  for (const context of pending || []) {
    _adoptKnownSelection(context, normalizedCurrent)
    const result = (commandResults || []).find(
      (candidate) => candidate._dispatchSeq === context.dispatchSeq,
    )
    _adoptKnownSelection(result, context.branchSelection, "_branchSelection")
  }
}

export function mergeLocalCommandResults(messages, commandResults, currentSelection = null) {
  const canonical = Array.isArray(messages) ? messages : []
  if (!commandResults?.length) return [...canonical]

  const normalizedCurrent = normalizeCommandResultBranchSelection(currentSelection)
  const viewKey = commandResultViewKey(normalizedCurrent)
  const buckets = Array.from({ length: canonical.length + 1 }, () => [])
  for (const result of commandResults) {
    _adoptKnownSelection(result, normalizedCurrent, "_branchSelection")
    if (!_branchSelectionsAreCompatible(result?._branchSelection, normalizedCurrent)) continue

    const viewAnchors = result?._anchorIndexesByView || {}
    const viewAnchor = viewAnchors[viewKey]
    const beforeMessageId = result?._beforeMessageId
    const beforeEventId = result?._beforeEventId
    const beforeIndex =
      beforeMessageId || beforeEventId
        ? canonical.findIndex(
            (message) =>
              (beforeMessageId && message?.id === beforeMessageId) ||
              (beforeEventId &&
                (message?.eventId === beforeEventId || message?.id === beforeEventId)),
          )
        : -1
    const rawAnchor =
      beforeIndex >= 0
        ? beforeIndex
        : Number.isInteger(viewAnchor)
          ? viewAnchor
          : Number.isInteger(result?._anchorIndex)
            ? result._anchorIndex
            : canonical.length
    const anchor = Math.max(0, Math.min(rawAnchor, canonical.length))
    if (normalizedCurrent != null) {
      result._anchorIndexesByView = { ...viewAnchors, [viewKey]: anchor }
    }
    buckets[anchor].push(result)
  }
  for (const bucket of buckets) {
    bucket.sort(
      (left, right) =>
        (Number.isInteger(left?._dispatchSeq) ? left._dispatchSeq : Number.MAX_SAFE_INTEGER) -
        (Number.isInteger(right?._dispatchSeq) ? right._dispatchSeq : Number.MAX_SAFE_INTEGER),
    )
  }

  const merged = []
  for (let index = 0; index <= canonical.length; index += 1) {
    if (index > 0) merged.push(canonical[index - 1])
    merged.push(...buckets[index])
  }
  return merged
}

export function captureLocalCommandResultContext(messages, eventsKnown, branchSelection) {
  return {
    branchSelection: eventsKnown ? normalizeCommandResultBranchSelection(branchSelection) : null,
    anchorIndex: eventsKnown
      ? (messages || []).filter((message) => message.role !== "command_result").length
      : null,
  }
}

export function registerLocalCommandResultContext(snapshot, dispatchSeq) {
  return {
    ...snapshot,
    dispatchSeq,
    beforeMessageId: null,
    beforeEventId: null,
  }
}

export function releaseLocalCommandResultContext(pending, context) {
  if (!pending?.length || !context) return pending || []
  const dispatchSeq = context.dispatchSeq
  return pending.filter(
    (candidate) =>
      candidate !== context &&
      (!Number.isInteger(dispatchSeq) || candidate.dispatchSeq !== dispatchSeq),
  )
}

export function bindLocalCommandResultContexts(pending, commandResults, message, currentSelection) {
  if (!pending?.length) return []
  const normalizedCurrent = normalizeCommandResultBranchSelection(currentSelection)

  for (const context of pending) {
    if (context.beforeMessageId || context.beforeEventId) continue
    _adoptKnownSelection(context, normalizedCurrent)
    if (!_branchSelectionsAreCompatible(context.branchSelection, normalizedCurrent)) continue

    context.beforeMessageId = message.id || null
    context.beforeEventId = message.eventId
    const commandResult = (commandResults || []).find(
      (result) => result._dispatchSeq === context.dispatchSeq,
    )
    if (commandResult) {
      _adoptKnownSelection(commandResult, context.branchSelection, "_branchSelection")
      commandResult._beforeMessageId = context.beforeMessageId
      commandResult._beforeEventId = context.beforeEventId
    }
  }

  return pending.filter(
    (context) => !context.resultAdded || (!context.beforeMessageId && !context.beforeEventId),
  )
}

export function buildLocalCommandResultMessage({ id, commandText, response, context, timestamp }) {
  const anchorIndex = Number.isInteger(context?.anchorIndex) ? context.anchorIndex : null
  const branchSelection = normalizeCommandResultBranchSelection(context?.branchSelection)
  return {
    id,
    role: "command_result",
    command: commandText,
    content: response?.output || "",
    error: response?.error || "",
    data: response?.data || null,
    timestamp,
    _anchorIndex: anchorIndex,
    _beforeMessageId: context?.beforeMessageId || null,
    _beforeEventId: context?.beforeEventId || null,
    _dispatchSeq: Number.isInteger(context?.dispatchSeq) ? context.dispatchSeq : null,
    _branchSelection: branchSelection,
    _anchorIndexesByView:
      Number.isInteger(anchorIndex) && branchSelection != null
        ? { [commandResultViewKey(branchSelection)]: anchorIndex }
        : {},
  }
}
