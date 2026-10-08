/**
 * Composer state and actions of the v2 chat column: the draft (persisted
 * per conversation under the same key as the v1 chat panel), attachments,
 * the slash-command menu, and sending — slash commands, inline command
 * results, compact, clear and confirm flows — through the chat store.
 *
 * `chat` is the scoped chat store; `tabKey` and `instance` are refs;
 * `attachments` is a ref the caller may share across columns; `onSent()`
 * runs once a message leaves (the column follows the reply).
 */

import { ElMessage, ElMessageBox } from "element-plus"
import { computed, nextTick, ref, watch } from "vue"

import { useV2T } from "@/components/session-v2/model/v2Strings"
import { useSlashCommandCompletion } from "@/composables/useSlashCommandCompletion"
import { _parseSlashCommand } from "@/stores/chat"
import { terrariumAPI } from "@/utils/api"
import { buildMessageParts, formatBytes } from "@/utils/chatAttachments"
import { readLocalPref, writeLocalPref } from "@/utils/uiPrefs"
import { useI18n } from "@/utils/i18n"

/** The draft key shared with the v1 chat panel, or "" without a session or conversation. */
export function draftKeyFor(instanceId, tab) {
  return instanceId && tab ? `kt.chat.draft.${instanceId}.${tab}` : ""
}

const errorText = (err) => err?.response?.data?.detail || err?.message || String(err)

export function useComposerSend({ chat, tabKey, instance, attachments = ref([]), onSent }) {
  const { t } = useI18n()
  const v2t = useV2T()
  const inputText = ref("")
  const composerRevision = ref(0)
  const submitInFlight = ref(false)
  const composerEl = ref(null)

  const slash = useSlashCommandCompletion({ chat, inputText, activeTabKey: tabKey })

  const instanceKey = () => instance.value?.id || chat._instanceId || ""
  const draftKey = () => draftKeyFor(instanceKey(), tabKey.value)
  function restoreDraft() {
    const key = draftKey()
    inputText.value = key ? readLocalPref(key) || "" : ""
    nextTick(() => composerEl.value?.resize?.())
  }
  function persistDraft() {
    const key = draftKey()
    if (key) writeLocalPref(key, inputText.value || null)
  }
  watch(() => draftKey(), restoreDraft, { immediate: true })
  watch(inputText, persistDraft)

  const processing = computed(() => (tabKey.value ? !!chat.processingByTab[tabKey.value] : false))
  const placeholder = computed(() => {
    const tab = tabKey.value
    if (!tab) return t("chat.selectTab")
    if (tab.startsWith("ch:")) return t("chat.sendToChannel", { channel: tab.slice(3) })
    return t("chat.messagePlaceholder")
  })
  const labels = computed(() => ({
    attachFile: t("chat.attachFile"),
    attachImage: t("chat.attachImage"),
    clear: t("chat.clearContext"),
    compact: t("chat.compactContext"),
    message: placeholder.value,
    moreActions: t("chat.moreActions"),
    removeAttachment: v2t("composer.remove"),
    send: t("chat.sendMessage"),
    stop: t("chat.stopGeneration"),
  }))

  function onInputChanged() {
    composerRevision.value += 1
    chat.markSlashTarget(tabKey.value, null)
  }

  function onAttachmentError(error) {
    if (error.code === "too-large")
      ElMessage.error(
        t("chat.attachmentTooLarge", {
          name: error.name,
          size: formatBytes(error.size),
          limit: formatBytes(error.limit),
        }),
      )
    else if (error.code === "not-image")
      ElMessage.error(t("chat.attachmentNotImage", { name: error.name }))
    else ElMessage.error(error.error?.message || String(error.error || error.code))
  }

  // Pasted clipboard blobs arrive as "image.png" / "blob"; give them a timestamped name.
  function transformAttachment(file, kind, source) {
    if (source !== "paste" || (file.name && file.name !== "image.png" && file.name !== "blob"))
      return file
    const k = kind || ((file.type || "").startsWith("image/") ? "image" : "file")
    const ts = new Date().toISOString().replace(/[:.]/g, "-").replace(/T/, "_").replace(/Z$/, "")
    const ext = (file.type.split("/")[1] || (k === "image" ? "png" : "bin")).split("+")[0]
    try {
      return new File([file], `${k === "image" ? "pasted-image" : "pasted-file"}-${ts}.${ext}`, {
        type: file.type,
        lastModified: file.lastModified,
      })
    } catch {
      return file
    }
  }

  const sessionTarget = () => chat._instanceGraphId || chat._instanceId

  async function surfaceCommandResult(response, target = null) {
    if (!response) return
    if (target?.inline) {
      chat.addCommandResult(target.tabKey, target.commandText, response, target.resultContext)
      return
    }
    if (response.error) {
      ElMessage.error(response.error)
      return
    }
    const payload = response.data
    if (payload?.type === "notify" && payload.message) {
      ;(ElMessage[payload.level || "info"] || ElMessage.info)(payload.message)
      return
    }
    if (payload?.type === "confirm" && payload.message && payload.action) {
      try {
        await ElMessageBox.confirm(payload.message, response.output || payload.action, {
          type: "warning",
          confirmButtonText: t("common.confirm"),
          cancelButtonText: t("common.cancel"),
        })
      } catch {
        return
      }
      const sid = target?.sessionId || sessionTarget()
      const tab = target?.creatureId || tabKey.value || "root"
      const confirmed = await terrariumAPI.executeCreatureCommand(
        sid,
        tab,
        payload.action,
        payload.action_args || "",
      )
      await surfaceCommandResult(confirmed, { sessionId: sid, creatureId: tab })
      return
    }
    if (response.output) ElMessage({ message: response.output, type: "info" })
  }

  async function send() {
    if (submitInFlight.value || (!inputText.value.trim() && attachments.value.length === 0)) return
    submitInFlight.value = true
    try {
      const sendTab = tabKey.value
      if (!sendTab) {
        ElMessage.error(v2t("composer.noTab"))
        return
      }
      if (slash.open.value && slash.entries.value.length) {
        slash.choose(slash.entries.value[slash.selectedIndex.value] || slash.entries.value[0])
        return
      }
      const sendText = inputText.value
      const sendAttachments = [...attachments.value]
      const revision = composerRevision.value
      const generation = chat._instanceGeneration
      const instId = chat._instanceId
      const graphId = chat._instanceGraphId
      const sameSession = () =>
        chat._instanceGeneration === generation &&
        chat._instanceId === instId &&
        chat._instanceGraphId === graphId
      const sameComposer = () =>
        sameSession() &&
        chat.activeTab === sendTab &&
        tabKey.value === sendTab &&
        inputText.value === sendText &&
        attachments.value.length === sendAttachments.length &&
        attachments.value.every((a, i) => a === sendAttachments[i])
      let ownedSlashTarget = chat._slashTargetByTab?.[sendTab]
      const clearOwnedSlashTarget = () => {
        if (chat._slashTargetByTab?.[sendTab] === ownedSlashTarget)
          chat.markSlashTarget(sendTab, null)
      }

      let slashTarget = null
      try {
        slashTarget = await chat.prepareSlashSend(
          {
            key: sendTab,
            creature: sendTab,
            type: sendTab.startsWith("ch:") ? "channel" : "creature",
          },
          sendText,
        )
      } catch (err) {
        console.warn("Slash inventory lookup failed; using command fallback:", err)
      }
      if (!sameComposer()) return clearOwnedSlashTarget()
      chat.markSlashTarget(sendTab, slashTarget)
      ownedSlashTarget = chat._slashTargetByTab?.[sendTab]

      let parts
      try {
        parts = await buildMessageParts(sendText, sendAttachments)
      } catch (err) {
        clearOwnedSlashTarget()
        throw err
      }
      if (!sameComposer()) return clearOwnedSlashTarget()

      const parsed = _parseSlashCommand(parts)
      const inline =
        parsed?.command === "goal" &&
        (!slashTarget ||
          (slashTarget.type === "command" && slashTarget.name.toLowerCase() === "goal"))
      if (!parsed && slashTarget) clearOwnedSlashTarget()
      const resultContext = inline ? chat.registerCommandResultContext(sendTab) : null
      const target = {
        sessionId: graphId || instId,
        creatureId: sendTab || "root",
        tabKey: sendTab,
        commandText: sendText,
        inline,
        resultContext,
      }
      const viaHttp = sendTab.startsWith("ch:") || inline
      if (!viaHttp && chat._ws?.readyState !== WebSocket.OPEN) {
        if (inline) chat.releaseCommandResultContext(sendTab, resultContext)
        clearOwnedSlashTarget()
        ElMessage.error(v2t("composer.notConnected"))
        return
      }

      try {
        const pending = chat.send(parts)
        let outcome = viaHttp ? null : await pending
        if (composerRevision.value === revision && sameComposer()) {
          inputText.value = ""
          attachments.value = []
          persistDraft()
          nextTick(() => composerEl.value?.resetHeight?.())
        }
        onSent?.()
        if (viaHttp) outcome = await pending
        if (outcome?.handled === "command") {
          if (!sameSession()) chat.releaseCommandResultContext(target.tabKey, target.resultContext)
          else await surfaceCommandResult(outcome.result, target)
        } else if (target.inline) {
          chat.releaseCommandResultContext(target.tabKey, target.resultContext)
        }
      } catch (err) {
        console.error("Command failed:", err)
        if (!sameSession()) {
          chat.releaseCommandResultContext(target.tabKey, target.resultContext)
          return
        }
        if (target.inline)
          chat.addCommandResult(
            target.tabKey,
            target.commandText,
            { error: errorText(err) },
            target.resultContext,
          )
        else ElMessage.error(v2t("composer.commandFailed", { error: errorText(err) }))
      }
    } finally {
      submitInFlight.value = false
    }
  }

  async function compact() {
    try {
      const response = await terrariumAPI.executeCreatureCommand(
        sessionTarget(),
        tabKey.value || "root",
        "compact",
      )
      await surfaceCommandResult(response)
    } catch (err) {
      ElMessage.error(v2t("composer.compactFailed", { error: errorText(err) }))
    }
  }

  async function clear() {
    try {
      await ElMessageBox.confirm(t("chat.clearConfirm"), t("chat.clearContext"), {
        type: "warning",
        confirmButtonText: t("common.clear"),
        cancelButtonText: t("common.cancel"),
      })
    } catch {
      return
    }
    try {
      const response = await terrariumAPI.executeCreatureCommand(
        sessionTarget(),
        tabKey.value || "root",
        "clear",
        "--force",
      )
      await surfaceCommandResult(response)
    } catch (err) {
      ElMessage.error(v2t("composer.clearFailed", { error: errorText(err) }))
    }
  }

  return {
    inputText,
    attachments,
    composerEl,
    processing,
    placeholder,
    labels,
    slash,
    send,
    compact,
    clear,
    interrupt: () => chat.interrupt(tabKey.value),
    onInputChanged,
    onAttachmentsChanged: () => (composerRevision.value += 1),
    onAttachmentError,
    transformAttachment,
  }
}
