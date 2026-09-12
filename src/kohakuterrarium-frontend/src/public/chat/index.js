export { default as MarkdownRenderer } from "./MarkdownRenderer.vue"
export { default as ChatComposer } from "./ChatComposer.vue"
export { shouldSendOnEnter } from "./chatInput.js"
export {
  MAX_ATTACHMENT_BYTES,
  MAX_IMAGE_BYTES,
  attachmentToPart,
  buildMessageParts,
  contentToEditableDraft,
  detectAttachmentKind,
  formatBytes,
  genericFileToPart,
  imageFileToPart,
  validateAttachment,
  validateAttachments,
} from "./chatAttachments.js"
export { default as ConversationMessage } from "../../components/chat/shared/ConversationMessage.js"
// The production command-result leaf is reused verbatim by the Dashboard and
// the VS Code webview through this package boundary; it keeps importing the
// host i18n seam so each build graph supplies the real dictionary provider.
export { default as CommandResultMessage } from "../../components/chat/CommandResultMessage.vue"
// The single production video leaf. Both hosts render this exact component (the
// Dashboard directly, the VS Code webview through the shared ConversationMessage
// that imports it by relative path), so there is no second video renderer to
// drift. It is resolved host-neutrally through the injected media resolver.
export { default as VideoFilePreview } from "../../components/chat/VideoFilePreview.vue"
// The one production UI-event widget (ask_text/confirm/selection/progress/
// notification/card). The shared ConversationMessage renders it as the default
// for every ``ui_event`` role, so the Dashboard and the VS Code webview share
// the real interactive surfaces — Element Plus widgets, card Markdown, field
// defaults, progress and safe link actions — instead of a reduced fallback.
export { default as UIEventBlock } from "../../components/chat/UIEventBlock.vue"
// The one production tool pair. The shared ConversationMessage renders these as
// the default for every ``tool`` / ``tool-batch`` part, so both hosts show the
// same ToolCallBlock/ToolCallBatch (args, result parts, media, truncation,
// promotion, and the nested SubagentConversationPanel) instead of a reduced
// native fallback.
export { default as ToolCallBlock } from "../../components/chat/ToolCallBlock.vue"
export { default as ToolCallBatch } from "../../components/chat/ToolCallBatch.vue"
// The one production nested sub-agent conversation surface: read a persisted or
// live run, disambiguate repeat runs through the runs selector, and send to a
// live run. It resolves its Host API and visibility timer through the shared
// seams (``@/utils/api`` / ``@/composables/useVisibilityInterval``), and is what
// ToolCallBlock renders inside an expanded sub-agent. Exported so both hosts
// consume the same instance-level poll state rather than forking a second one.
export { default as SubagentConversationPanel } from "../../components/subagents/SubagentConversationPanel.vue"
export { default as ChatTranscriptSection } from "../../components/chat/shared/ChatTranscriptSection.js"
export {
  DEFAULT_TOOL_BATCH_THRESHOLD,
  computeRenderGroups,
  summarizeBatch,
} from "./chatToolGrouping.js"
// Shared transcript viewport: the render window, the physical-history-key
// semantic anchor, and the coordinator that drives paged older fetches all
// live here so the dashboard panel and the VS Code webview share one
// implementation instead of forking a second page cache or anchor.
export {
  CHAT_AUTO_EXPAND_TOP_PX,
  CHAT_HISTORY_AUTO_STEP,
  CHAT_HISTORY_MANUAL_STEP,
  captureSemanticAnchor,
  createChatHistoryExpander,
  restoreSemanticAnchor,
} from "../../components/chat/chatHistoryExpand.js"
export {
  CHAT_RENDER_EXPAND_MESSAGE_LIMIT,
  CHAT_RENDER_EXPAND_UNIT_BUDGET,
  CHAT_RENDER_MESSAGE_LIMIT,
  CHAT_RENDER_UNIT_BUDGET,
  findRenderWindowStart,
  indexOfSemanticKey,
  isTailRenderBudgetFull,
  messageRenderUnits,
  semanticKey,
  useChatRenderWindow,
} from "../../components/chat/chatRenderWindow.js"

// Host-neutral media resolution seam: one resolver contract installed by each
// host (Dashboard = direct same-origin browser URL, VS Code = Host-spooled
// webview URI) and consumed by the shared media leaves below.
export {
  MEDIA_RESOLVER_KEY,
  createBrowserMediaResolver,
  createMarkdownMediaResolver,
  fileReferencePath,
  mediaSourceUrl,
  provideMediaResolver,
  safeArtifactUrl,
  safeImageUrl,
  safeMediaParts,
  useMediaResolver,
  useMediaResource,
} from "./mediaResolver.js"
// Host-neutral platform-origin seam: the origin same-origin backend URLs resolve
// against. The Dashboard leaves it uninstalled (browser origin); the VS Code
// webview installs an explicit value so its ``vscode-webview://`` document origin
// is never mistaken for the backend origin when a card renders Markdown links.
export { PLATFORM_ORIGIN_KEY, providePlatformOrigin, usePlatformOrigin } from "./platformOrigin.js"
// Host-neutral platform link opener: the one seam a host installs to resolve a
// card/Markdown link the page itself cannot (the VS Code webview forwards it to
// the Host's ``platform.openLink`` operation). The Dashboard installs none and
// keeps native browser navigation.
export {
  PLATFORM_LINK_OPENER_KEY,
  providePlatformLinkOpener,
  usePlatformLinkOpener,
} from "./platformLink.js"
// Shared link policy: the single safe resolver for model-authored card/link
// targets. Both hosts (Dashboard and the VS Code webview) resolve a link through
// this so relative-URL, hash, `javascript:` and unknown-origin handling can never
// drift between them. ``shouldOpenThroughHost`` is the matching decision for
// when the installed platform opener (not the page) owns the click.
export { isExternalUrl, resolvePlatformLink, shouldOpenThroughHost } from "./externalLinks.js"
export { MediaImage } from "./MediaPreview.js"
