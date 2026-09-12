import { computed, nextTick, onScopeDispose, ref, watch } from "vue"
import { ElMessage } from "element-plus"

import { useMessageEditDraft } from "../../components/chat/shared/messageEditDrafts"

import {
  buildMessageParts,
  contentToEditableDraft,
  formatBytes,
  MAX_ATTACHMENT_BYTES,
  MAX_IMAGE_BYTES,
} from "@/utils/chatAttachments"
import { useMessageActions } from "./messageActions.js"

/** Extract plain text from content that may be a string or array of content parts. */
export function contentToText(content) {
  if (typeof content === "string") return content
  if (Array.isArray(content)) {
    return content
      .filter((p) => p?.type === "text")
      .map((p) => p.text || "")
      .join("\n")
  }
  return ""
}

// Same IME semantics the composer uses: a keydown that is mid-composition (or
// the legacy keyCode 229) must never commit or cancel the inline editor.
function isComposingKey(event) {
  return !!event && (event.isComposing === true || event.keyCode === 229)
}

function logicalKey(message) {
  if (!message) return null
  const id = message.id ?? message.eventId ?? null
  const locator = message.locator ?? null
  const eventId = locator?.eventId ?? null
  const turnIndex = locator?.turnIndex ?? message.turnIndex ?? null
  if (id == null && eventId == null) return null
  return JSON.stringify([id, eventId, turnIndex, locator?.branchId ?? null])
}

export function useMessageRowActions({ props, chat, messageTab, writeClipboard, t, getViewOwner }) {
  let alive = true
  onScopeDispose(() => {
    alive = false
  })

  const seamOwner = useMessageActions()?.getViewOwner
  const viewOwner = getViewOwner ?? seamOwner
  const captureViewOwner = () => (typeof viewOwner === "function" ? viewOwner() : null)
  const targetKey = () =>
    JSON.stringify([
      chat._instanceId,
      chat._instanceGraphId,
      chat._instanceGeneration,
      messageTab.value,
      captureViewOwner(),
      !Array.isArray(chat.tabs) || chat.tabs.includes(messageTab.value),
    ])
  let targetRevision = 0
  let rowRevision = 0
  watch(
    targetKey,
    () => {
      targetRevision++
    },
    { flush: "sync" },
  )
  watch(
    () => logicalKey(props.message),
    () => {
      rowRevision++
    },
    { flush: "sync" },
  )
  const captureTarget = () => {
    const key = targetKey()
    const revision = targetRevision
    return () => targetKey() === key && targetRevision === revision
  }
  const draft = useMessageEditDraft(
    () => JSON.stringify([targetKey(), logicalKey(props.message)]),
    targetKey,
  )
  const { editing, editText, editAttachments, editSaving, editError } = draft
  const editTextareaEl = ref(null)
  const editImageInputEl = ref(null)
  const editFileInputEl = ref(null)

  const branchOperation = computed(() => chat.branchOperationByTab[messageTab.value] || null)
  const branchOperationBusy = computed(() => branchOperation.value != null)
  const branchOperationError = computed(
    () => chat.branchOperationErrorByTab[messageTab.value] || "",
  )

  async function _writeClipboard(text) {
    const ownsTarget = captureTarget()
    const row = rowRevision
    try {
      await writeClipboard(text)
      return true
    } catch (err) {
      if (alive && ownsTarget() && rowRevision === row) ElMessage.error(t("chat.copyFailed"))
      return false
    }
  }

  function copyMessage() {
    const text = contentToText(props.message.contentParts || props.message.content)
    return _writeClipboard(text)
  }

  function copyAssistantText() {
    // Real message copy body: text parts only, so tool cards / media never leak
    // into the clipboard.
    let text = ""
    if (props.message.parts) {
      for (const part of props.message.parts) {
        if (part.type === "text" && part.content) text += part.content
      }
    } else if (props.message.content) {
      text = props.message.content
    }
    return _writeClipboard(text)
  }

  function startEdit() {
    const draft = contentToEditableDraft(props.message.contentParts || props.message.content)
    editText.value = draft.text
    editAttachments.value = draft.attachments
    editing.value = true
    nextTick(() => editTextareaEl.value?.focus())
  }

  function cancelEdit(event) {
    if (isComposingKey(event)) return
    if (editSaving.value) return
    editing.value = false
    editText.value = ""
    editAttachments.value = []
  }

  function _pushEditAttachment(file, kind) {
    const limit = kind === "image" ? MAX_IMAGE_BYTES : MAX_ATTACHMENT_BYTES
    if (file.size > limit) {
      ElMessage.error(
        `${file.name} is too large (${formatBytes(file.size)} > ${formatBytes(limit)})`,
      )
      return false
    }
    if (kind === "image" && file.type && !file.type.startsWith("image/")) {
      ElMessage.error(`${file.name} is not an image file`)
      return false
    }
    editAttachments.value.push({
      id: `new_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`,
      file,
      name: file.name,
      kind,
    })
    return true
  }

  function onEditFileChange(e, kind = "file") {
    const files = Array.from(e.target.files || [])
    for (const file of files) _pushEditAttachment(file, kind)
    e.target.value = ""
  }

  function removeEditAttachment(index) {
    editAttachments.value.splice(index, 1)
  }

  async function confirmEdit(event) {
    if (isComposingKey(event)) return
    if (editSaving.value || (!editText.value.trim() && editAttachments.value.length === 0)) return
    const retained = draft.retain()
    const state = retained.state
    const sourceMessage = props.message
    const sourceKey = logicalKey(sourceMessage)
    const ownsTarget = captureTarget()
    const sourceRowRevision = rowRevision
    const sourceTab = messageTab.value
    const targetIdx = props.messageIdx
    const targetLocator = sourceMessage.locator ? { ...sourceMessage.locator } : null
    const targetTurnIndex = sourceMessage.turnIndex
    const targetUserPosition = sourceMessage.userPosition
    const targetLatestBranch = sourceMessage.latestBranch
    const capturedAttachments = [...state.editAttachments]
    const viewIsCurrent = () =>
      alive &&
      ownsTarget() &&
      rowRevision === sourceRowRevision &&
      (sourceKey != null
        ? logicalKey(props.message) === sourceKey
        : props.message === sourceMessage)
    const resolveEditIndex = () => {
      if (targetIdx == null) return targetIdx
      const msgs = chat?.messagesByTab?.[sourceTab]
      if (!Array.isArray(msgs)) return targetIdx
      const atIndex = msgs[targetIdx]
      if (atIndex && logicalKey(atIndex) === sourceKey) return targetIdx
      if (targetLocator && targetLocator.eventId != null) {
        for (let i = 0; i < msgs.length; i++) {
          const row = msgs[i]
          if (
            row?.role === "user" &&
            row.locator &&
            row.locator.eventId === targetLocator.eventId &&
            row.locator.turnIndex === targetLocator.turnIndex &&
            row.locator.branchId === targetLocator.branchId
          )
            return i
        }
      }
      return -1
    }
    state.editSaving = true
    state.editError = ""
    try {
      const newContent = await buildMessageParts(state.editText, capturedAttachments)
      if (!viewIsCurrent()) return
      const index = resolveEditIndex()
      if (index == null || index < 0) return
      const operation = chat.editMessage(index, newContent, {
        turnIndex: targetTurnIndex,
        userPosition: targetUserPosition,
        latestBranch: targetLatestBranch,
        locator: targetLocator,
        attachments: capturedAttachments,
        tabId: sourceTab,
      })
      await nextTick()
      if (ownsTarget() && chat.branchOperationByTab[sourceTab]) state.editing = false
      const result = await operation
      if (!ownsTarget() || result?.superseded) return
      if (result?.ok) {
        state.editing = false
        state.editText = ""
        state.editAttachments = []
        return
      }
      state.editing = true
      state.editError =
        result?.error || chat.branchOperationErrorByTab[sourceTab] || "Failed to start edit"
      await nextTick()
      if (viewIsCurrent()) editTextareaEl.value?.focus()
    } catch (err) {
      if (viewIsCurrent()) {
        state.editing = true
        state.editError = err instanceof Error ? err.message : String(err)
      }
    } finally {
      state.editSaving = false
      retained.release()
    }
  }

  function regenerate() {
    // Pass the clicked message's turnIndex so the backend regenerates at THIS
    // turn (creates a new branch under the current subtree) rather than silently
    // retargeting the conversation tail. Falls back to the legacy tail-regen
    // path when the message lacks a turn_index (e.g. pre-v2 sessions).
    const tIdx = props.message?.turnIndex
    if (tIdx != null) chat.regenerateLastResponse({ turnIndex: tIdx, tabId: messageTab.value })
    else chat.regenerateLastResponse({ tabId: messageTab.value })
  }

  // ── Branch navigator ──
  //
  // User-side <x/N>: walks distinct user_message contents (edits).
  // Assistant-side <x/N>: walks regens within the current user content.
  // The two are independent — a turn can have neither, either, or both.

  const hasUserGroups = computed(
    () =>
      props.message.branchAnchor === "user" &&
      typeof props.message.userGroupCount === "number" &&
      props.message.userGroupCount > 1,
  )
  const hasPrevUserGroup = computed(
    () => hasUserGroups.value && (props.message.currentUserGroupIdx ?? 0) > 0,
  )
  const hasNextUserGroup = computed(
    () =>
      hasUserGroups.value &&
      (props.message.currentUserGroupIdx ?? 0) < props.message.userGroupCount - 1,
  )

  function _switchUserGroup(delta) {
    if (branchOperationBusy.value) return
    const idx = props.message.currentUserGroupIdx ?? 0
    const target = idx + delta
    const groups = props.message.userGroupBranches || []
    if (target < 0 || target >= groups.length) return
    chat.selectBranch(props.message.turnIndex, groups[target], messageTab.value)
  }
  function goToPrevUserGroup() {
    if (hasPrevUserGroup.value) _switchUserGroup(-1)
  }
  function goToNextUserGroup() {
    if (hasNextUserGroup.value) _switchUserGroup(1)
  }

  const hasAssistantBranches = computed(
    () =>
      props.message.branchAnchor === "assistant" &&
      typeof props.message.assistantBranchCount === "number" &&
      props.message.assistantBranchCount > 1,
  )
  const hasPrevAssistantBranch = computed(
    () => hasAssistantBranches.value && (props.message.currentAssistantIdx ?? 0) > 0,
  )
  const hasNextAssistantBranch = computed(
    () =>
      hasAssistantBranches.value &&
      (props.message.currentAssistantIdx ?? 0) < props.message.assistantBranchCount - 1,
  )

  function _switchAssistantBranch(delta) {
    if (branchOperationBusy.value) return
    const idx = props.message.currentAssistantIdx ?? 0
    const target = idx + delta
    const branches = props.message.assistantBranches || []
    if (target < 0 || target >= branches.length) return
    chat.selectBranch(props.message.turnIndex, branches[target], messageTab.value)
  }
  function goToPrevAssistantBranch() {
    if (hasPrevAssistantBranch.value) _switchAssistantBranch(-1)
  }
  function goToNextAssistantBranch() {
    if (hasNextAssistantBranch.value) _switchAssistantBranch(1)
  }

  return {
    editing,
    editText,
    editAttachments,
    editTextareaEl,
    editImageInputEl,
    editFileInputEl,
    editSaving,
    editError,
    branchOperation,
    branchOperationBusy,
    branchOperationError,
    copyMessage,
    copyAssistantText,
    startEdit,
    cancelEdit,
    onEditFileChange,
    removeEditAttachment,
    confirmEdit,
    regenerate,
    hasUserGroups,
    hasPrevUserGroup,
    hasNextUserGroup,
    goToPrevUserGroup,
    goToNextUserGroup,
    hasAssistantBranches,
    hasPrevAssistantBranch,
    hasNextAssistantBranch,
    goToPrevAssistantBranch,
    goToNextAssistantBranch,
  }
}
