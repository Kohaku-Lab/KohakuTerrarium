// Pure admission policy for the webview platform link opener.
//
// A click captures the ready epoch it belongs to at the instant the user
// activates the link. The Host resolves and opens the reference against the
// live backend for whoever made the request; if the epoch moves while the
// request is in flight the result belongs to a document that no longer exists.
// Kept free of Vue/Element Plus so the capture rules can be unit-tested
// directly instead of only through the built bundle.

// ``ownerReadyId`` is the ready epoch captured at click time (the requestId of
// the ``ready`` handshake). A click with no live epoch has nothing the Host can
// resolve against: an envelope without one is refused by ``protocol.cjs`` and
// silently dropped, so it must never be sent.
export function isOpenReady(readyId) {
  return Number.isInteger(readyId) && readyId > 0
}

// Decide how to surface the settlement of a ``platform.openLink`` request whose
// click captured ``ownerReadyId``:
//   - the epoch moved on  -> 'stale': the open may already have happened under
//     the previous owner, so the result is suppressed rather than reported as a
//     failure (which would falsely claim the browser did not open);
//   - still owned         -> 'failed': surface the localized open failure.
export function classifyFailure({ ownerReadyId, currentReadyId }) {
  if (currentReadyId !== ownerReadyId) return 'stale'
  return 'failed'
}
