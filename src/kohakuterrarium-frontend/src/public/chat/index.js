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
