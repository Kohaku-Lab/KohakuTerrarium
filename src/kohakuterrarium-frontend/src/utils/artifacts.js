// Dashboard-side compatibility surface. The canonical definitions live in
// ``public/chat/mediaRefs.js`` so the shared public Markdown graph is
// self-contained; this module just re-exports them for the existing
// ``@/utils/artifacts`` callers.
export {
  fileReferencePath,
  mediaSourceUrl,
  safeArtifactUrl,
  safeMediaParts,
} from "../public/chat/mediaRefs.js"
