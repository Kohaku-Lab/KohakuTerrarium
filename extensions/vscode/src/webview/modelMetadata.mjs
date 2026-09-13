// Owner-fenced metadata read-back for the shared model picker.
//
// A model switch records a STABLE owner (session + selected creature + host ready
// epoch) and the exact per-creature entry object it wrote. A read-back applies
// ONLY while that owner still holds AND the entry object is still the one the
// switch wrote — so a late or superseded response can never overwrite a newer
// selection, a newer ``session_info`` (even at the same canonical with a newer
// ``max_context``), a new target, or a torn-down webview.
export function createMetadataReadback({ getOwner, getEntry }) {
  function sameOwner(left, right) {
    return (
      left.runtimeId != null &&
      left.runtimeId === right.runtimeId &&
      left.creatureId != null &&
      left.creatureId === right.creatureId &&
      left.epoch === right.epoch
    )
  }

  return {
    captureOwner: () => getOwner(),
    ownsOwner: (owner) => sameOwner(owner, getOwner()),
    // Capture the entry the switch just wrote; ``owns`` then requires it to still
    // be the live entry, so any newer per-creature write supersedes the read-back.
    captureEntry: (entryKey, owner) => ({ owner, entryKey, entry: getEntry(entryKey) }),
    owns: (captured) => sameOwner(captured.owner, getOwner()) && getEntry(captured.entryKey) === captured.entry,
  }
}

// The single creature entry a read-back is allowed to apply: the one the switch
// owned, only while the backend still reports the accepted canonical selector.
// Every other creature in the response is deliberately ignored, so a newer live
// ``session_info`` for a creature the read-back did not target is never clobbered.
export function selectOwnedMetadata(metadata, target, canonical, existing) {
  const creature = (metadata?.creatures || []).find((candidate) => candidate?.name === target)
  if (!creature) return null
  const llmName = creature.llm_name || creature.model
  if (!llmName) return null
  if (canonical != null && llmName !== canonical) return null
  return {
    ...(existing || {}),
    model: creature.model || llmName,
    llmName,
    maxContext: creature.max_context ?? existing?.maxContext,
  }
}
