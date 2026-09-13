// Binds the transcript viewport events to the current viewport identity so
// a late event from a detached scroll surface can never reach the active
// controller. Scroll, wheel, key, touch, and reply all share the identity
// fence; unknown handlers are simply omitted.
export function createTranscriptBindings({ onViewportReady, onScroll, onWheel, onKeydown, onTouchStart, onTouchMove, onReply }) {
  let identity = null
  let current = null

  return {
    forIdentity(nextIdentity) {
      if (current && nextIdentity === identity) return current
      identity = nextIdentity
      const boundIdentity = nextIdentity
      current = {
        onViewportReady: (viewport) => onViewportReady(viewport, boundIdentity),
        onScroll: (event) => onScroll(event, boundIdentity),
        onWheel: onWheel ? (event) => onWheel(event, boundIdentity) : undefined,
        onKeydown: onKeydown ? (event) => onKeydown(event, boundIdentity) : undefined,
        onTouchstart: onTouchStart ? (event) => onTouchStart(event, boundIdentity) : undefined,
        onTouchmove: onTouchMove ? (event) => onTouchMove(event, boundIdentity) : undefined,
        onReply,
      }
      return current
    },
  }
}
