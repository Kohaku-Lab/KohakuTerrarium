import { buildMessageParts, ChatComposer, ChatTranscriptSection, ConversationMessage, MarkdownRenderer } from '@kohakuterrarium/chat-ui'
import { createPinia } from 'pinia'
import { computed, createApp, h, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import { useChatStore } from '@/stores/chat'

import { BridgeWebSocket } from './bridge.js'
import { renderCarbonIcon } from './carbonIcons.mjs'
import { bindComposerBuffer } from './composerBuffer.mjs'
import { installGoalBridge } from './goalBridge.mjs'
import { applyContextCommandOutcome } from './contextCommandResult.mjs'
import { createHostAcceptedChat, createObservedWebSocket } from './hostAcceptedChat.mjs'
import { createConversationMessageOrchestrator, createConversationScrollController } from './conversationScroll.mjs'
import {
  createConversationAttachments,
  createConversationDrafts,
  createConversationOwnership,
  isConversationSuperseded,
} from './conversationOwnership.mjs'
import { createReadyCoordinator } from './readyCoordinator.mjs'
import { settleRequestMessage } from './requestDemux.mjs'
import { createSelectionVersionOwner } from './selectionVersion.mjs'
import { createSessionShell } from './sessionShell.js'
import { createSubmitGate, isComposerSubmitDisabled } from './submitGate.mjs'
import { applyTopologySelection } from './topologySelection.mjs'
import {
  createMessageSequence,
  createMessageTailSignature,
  createTranscriptBindings,
  createTranscriptWindow,
  messageSequenceKey,
} from './transcriptWindow.mjs'
import { createViewRenderers } from './viewRenderers.mjs'
import './style.css'
import { installNotificationSurface } from './notifications.mjs'
import './notifications.css'
import QueuedMessages from './QueuedMessages.vue'
import { composerLabels } from './composerLabels.mjs'
import ArtifactScope from './ArtifactScope.vue'
import { useArtifactImages } from './useArtifactImages.mjs'

const vscode = acquireVsCodeApi()
const pending = new Map()
let nextRequestId = 1000

function rejectPending(error) {
  for (const request of pending.values()) {
    clearTimeout(request.timer)
    request.reject(error)
  }
  pending.clear()
}

function request(type, data = {}, onSend = () => {}) {
  return new Promise((resolve, reject) => {
    const id = nextRequestId++
    onSend(id)
    const timer = setTimeout(() => {
      pending.delete(id)
      reject(Error('KohakuTerrarium request timed out'))
    }, 30000)
    pending.set(id, { resolve, reject, timer, type })
    vscode.postMessage({ type, requestId: id, ...data })
  })
}

BridgeWebSocket.post = (message) => vscode.postMessage(message)

const App = {
  setup() {
    const notifications = installNotificationSurface(document)
    onBeforeUnmount(notifications.dispose)
    const chat = useChatStore()
    const hostAcceptedChat = createHostAcceptedChat({ BridgeWebSocket, chat })
    onBeforeUnmount(hostAcceptedChat.queued.dispose)
    globalThis.WebSocket = createObservedWebSocket(BridgeWebSocket, hostAcceptedChat.observe)
    const available = ref(false)
    const automatic = ref(true)
    const sessions = ref([])
    const currentSession = ref(null)
    const composerOwner = () => ({
      readyId: latestReadyRequestId,
      runtimeId: currentSession.value?.session?.runtimeId,
      creatureId: currentSession.value?.targetCreatureId,
    })
    const draftBuckets = createConversationDrafts(composerOwner)
    const { model: draft, revision: draftRevision } = bindComposerBuffer(draftBuckets)
    const attachmentBuckets = createConversationAttachments(composerOwner)
    const { model: attachments, revision: attachmentRevision } = bindComposerBuffer(attachmentBuckets)
    const error = ref('')
    const status = ref('')
    const busy = ref(false)
    const contextBusy = ref(false)
    const sessionsExpanded = ref(false)
    const brandUri = document.querySelector('#app')?.dataset.brandUri || ''
    let reconciliation = Promise.resolve()
    let pendingReconciliations = 0
    let contextOperation = 0
    const selectionVersions = createSelectionVersionOwner()
    let activeSelectionReadyId = null
    BridgeWebSocket.getReadyId = () => activeSelectionReadyId
    onBeforeUnmount(() => {
      BridgeWebSocket.getReadyId = () => null
    })
    const currentConversationOwnership = () => ({ ...composerOwner(), name: currentSession.value?.target })
    const conversationOwnership = createConversationOwnership(currentConversationOwnership)
    const submitGate = createSubmitGate()
    const submitRevision = ref(0)
    const submitBusy = computed(() => (submitRevision.value, attachmentRevision.value, submitGate.busy(currentConversationOwnership())))
    const attachmentTransform = conversationOwnership.transform((file) => file)
    const getReadFence = () =>
      activeSelectionReadyId === latestReadyRequestId && available.value
        ? { readyId: activeSelectionReadyId, selectionVersion: selectionVersions.highest() }
        : null
    onBeforeUnmount(
      installGoalBridge({
        chat,
        request,
        ownership: conversationOwnership,
        getTarget: currentConversationOwnership,
        getFence: getReadFence,
        isReconciling: () => pendingReconciliations > 0,
      }),
    )
    const selectionRequest = async (type, data) => {
      if (activeSelectionReadyId !== latestReadyRequestId) throw Error('Wait for Session refresh')
      const result = await request(type, { ...data, readyId: activeSelectionReadyId })
      if (result.readyId !== latestReadyRequestId) return result
      selectionVersions.acceptResult(result.readyId, result.selectionVersion, result.readyId === latestReadyRequestId)
      activeSelectionReadyId = result.readyId
      return result
    }

    const api = {
      list: () => request('session.list'),
      create: () => request('session.create'),
      resume: (savedName) => request('session.resume', { savedName }),
      stop: ({ session, creatureId }) => selectionRequest('session.stop', { session, creatureId }),
      clearSelection: () => selectionRequest('session.clearSelection'),
      reconcile: () => selectionRequest('session.reconcile'),
      select: ({ session, creatureId }) => selectionRequest('session.select', { session, creatureId }),
    }
    const shell = createSessionShell({ api, chat })
    const tab = computed(() => currentSession.value?.target || '')
    const messages = computed(() => chat.messagesByTab[tab.value] || [])
    const scrollIdentity = computed(() =>
      JSON.stringify([currentSession.value?.session?.runtimeId, currentSession.value?.targetCreatureId]),
    )
    watch(scrollIdentity, () => {
      attachmentRevision.value += 1
      draftRevision.value += 1
    })
    const scroll = createConversationScrollController({ schedule: nextTick })
    const messageChanges = createConversationMessageOrchestrator(scroll)
    const transcriptWindow = createTranscriptWindow()
    const transcriptRevision = ref(0)

    const messageSequence = computed(() => createMessageSequence(messages.value))
    const messageTail = computed(() => createMessageTailSignature(messages.value))
    const messageStructure = computed(() => messageSequenceKey(messageSequence.value))
    const transcriptView = computed(() => {
      transcriptRevision.value
      return transcriptWindow.view(messages.value, scrollIdentity.value, messageSequence.value)
    })

    watch([scrollIdentity, () => messages.value.length], ([identity, count]) => scroll.setIdentity(identity, { hasMessages: count > 0 }), {
      immediate: true,
    })
    watch(
      () => ({
        identity: scrollIdentity.value,
        sequence: messageSequence.value,
      }),
      (current, previous) => {
        if (!previous || (current.sequence.length === 0 && previous.sequence.length === 0)) return
        messageChanges.beforeMessagesChange(previous.identity, previous.sequence, current.identity, current.sequence)
      },
      { flush: 'sync' },
    )
    watch(
      () => ({
        identity: scrollIdentity.value,
        structure: messageStructure.value,
        tail: messageTail.value,
      }),
      (current, previous) => {
        if (
          previous &&
          (current.structure !== previous.structure || current.tail !== previous.tail || current.identity !== previous.identity)
        )
          messageChanges.afterMessagesChange(current.identity, messageSequence.value)
      },
      { flush: 'post' },
    )
    watch(
      () => chat.processingByTab[tab.value],
      (processing) => {
        if (processing) scroll.onMessagesUpdated({ hasMessages: messages.value.length > 0 })
      },
    )
    onBeforeUnmount(() => scroll.dispose())

    globalThis.__ktVsCodeHistory = (session, creature) => request('http.history', { session, creature })
    globalThis.__ktVsCodeInterrupt = (session, creature) => request('http.interrupt', { session, creature })

    const reloadSessions = async () => (sessions.value = await shell.list())

    const applySelection = (selection, changed = true, isCurrent = () => true) =>
      applyTopologySelection({
        selection,
        changed,
        shell,
        chat,
        getCurrentSession: () => currentSession.value,
        setCurrentSession: (value) => (currentSession.value = value),
        setSessions: (value) => (sessions.value = value),
        isCurrent,
      })

    let latestReadyRequestId = null
    let composerConnectionId = null
    let selectionOperationEpoch = 0
    let notificationReadyId = null
    const artifactLoader = useArtifactImages(request, getReadFence, () => JSON.stringify(composerOwner()))
    function clearComposerBuckets() {
      artifactLoader.reset()
      notifications.clear()
      draftBuckets.clearAll()
      attachmentBuckets.clearAll()
      draftRevision.value += 1
      attachmentRevision.value += 1
    }
    function acceptComposerConnection(connectionId) {
      if (composerConnectionId !== connectionId) clearComposerBuckets()
      composerConnectionId = connectionId
    }
    const readyCoordinator = createReadyCoordinator({
      requestReady: () =>
        request('ready', {}, (id) => {
          artifactLoader.reset()
          latestReadyRequestId = id
          notificationReadyId = id
          selectionOperationEpoch++
          attachmentRevision.value += 1
          draftRevision.value += 1
        }),
      async applyReady(result, isCurrent) {
        if (result.superseded) return
        if (result.available === true) {
          acceptComposerConnection(result.connectionId)
          activeSelectionReadyId = result.readyId
          notificationReadyId = result.readyId
          const versioned = selectionVersions.acceptBaseline(activeSelectionReadyId, result.selectionVersion)
          const readyVersion = result.selectionVersion
          await applySelection(result.selection, true, () => isCurrent() && (!versioned || selectionVersions.highest() === readyVersion))
        }
        if (!isCurrent()) return
        available.value = result.available === true
        automatic.value = result.automatic !== false
        if (available.value) {
          error.value = ''
        } else {
          selectionOperationEpoch++
          activeSelectionReadyId = null
          notificationReadyId = null
          chat.unbindFromInstance()
          currentSession.value = null
          sessions.value = []
          error.value = 'No local KohakuTerrarium service found. Run “kt serve start”, then press Refresh.'
        }
      },
      async applyFailure(cause, isCurrent) {
        if (!isCurrent()) return
        selectionOperationEpoch++
        activeSelectionReadyId = null
        notificationReadyId = null
        available.value = false
        chat.unbindFromInstance()
        currentSession.value = null
        sessions.value = []
        error.value = cause.message
      },
    })

    const reconcileSessions = () => readyCoordinator.reconcile()

    async function createSession() {
      busy.value = true
      error.value = ''
      try {
        currentSession.value = await shell.create()
        await reloadSessions()
      } catch (cause) {
        error.value = cause.message
      } finally {
        busy.value = false
      }
    }

    async function resumeSession(session) {
      busy.value = true
      error.value = ''
      try {
        currentSession.value = await shell.resume(session.savedName)
        await reloadSessions()
      } catch (cause) {
        error.value = cause.message
      } finally {
        busy.value = false
      }
    }

    async function stopSession() {
      if (!currentSession.value?.targetCreatureId) return
      busy.value = true
      error.value = ''
      try {
        await shell.stop(currentSession.value)
        currentSession.value = null
        await reloadSessions()
      } catch (cause) {
        error.value = cause.message
      } finally {
        busy.value = false
      }
    }

    async function openSession(session, creatureId) {
      busy.value = true
      error.value = ''
      try {
        currentSession.value = await shell.open(session, creatureId)
      } catch (cause) {
        error.value = cause.message
      } finally {
        busy.value = false
      }
    }

    async function send({ text = draft.value, attachments: submittedAttachments = attachments.value } = {}) {
      if ((!text.trim() && submittedAttachments.length === 0) || !currentSession.value?.target) return
      const submitToken = submitGate.acquire(currentConversationOwnership())
      if (!submitToken) return
      submitRevision.value += 1
      const submittedText = text
      const submitted = [...submittedAttachments]
      const submittedOwner = attachmentBuckets.capture()
      const submittedDraft = draftBuckets.capture()
      try {
        await conversationOwnership.dispatch(async (assertCurrent) => {
          const content = submitted.length ? await buildMessageParts(submittedText, submitted) : submittedText
          assertCurrent()
          return hostAcceptedChat.send(content)
        })
        if (draftBuckets.clearSubmitted(submittedText, submittedDraft)) draftRevision.value += 1
        attachmentBuckets.removeSubmitted(submitted, submittedOwner)
        attachmentRevision.value += 1
        if (conversationOwnership.isCurrent(submittedOwner)) {
          scroll.forceFollow()
          error.value = ''
        }
      } catch (cause) {
        if (!isConversationSuperseded(cause) && conversationOwnership.isCurrent(submittedOwner))
          error.value = cause?.message || String(cause)
      } finally {
        submitGate.release(submitToken)
        submitRevision.value += 1
      }
    }

    async function manageContext(type) {
      if (!available.value || !currentSession.value?.target || busy.value || contextBusy.value) return
      const ownedReadyId = activeSelectionReadyId
      const ownedTarget = currentSession.value.targetCreatureId
      const operation = ++contextOperation
      const isCurrent = () =>
        operation === contextOperation && ownedReadyId === activeSelectionReadyId && ownedTarget === currentSession.value?.targetCreatureId
      contextBusy.value = true
      error.value = ''
      status.value = ''
      try {
        const response = await request(type)
        applyContextCommandOutcome(response, isCurrent(), (kind, text) => {
          if (kind === 'error') error.value = text
          else status.value = text
        })
      } catch (cause) {
        if (isCurrent()) error.value = cause?.message || String(cause)
      } finally {
        if (operation === contextOperation) contextBusy.value = false
      }
    }

    function onComposerError(problem) {
      error.value =
        problem?.error?.message ||
        (problem?.code === 'too-large' ? `${problem.name} is too large to attach` : `Could not attach ${problem?.name || 'file'}`)
    }

    function submitReply(message, actionId, values) {
      if (chat.wsStatus !== 'open') {
        error.value = 'Chat is disconnected. Press Refresh Sessions and try again.'
        return
      }
      hostAcceptedChat
        .submitUIReply(tab.value, message.eventId, actionId, values)
        .catch((cause) => (error.value = cause?.message || String(cause)))
    }

    const transcriptBindings = createTranscriptBindings({
      onViewportReady: (viewport, identity) => scroll.onViewportReady(viewport, identity),
      onScroll: (event, identity) => scroll.onScroll(event, identity),
      onReply: ({ message, actionId, values }) => submitReply(message, actionId, values),
    })
    const transcriptCallbacks = computed(() => transcriptBindings.forIdentity(scrollIdentity.value))
    function loadEarlierMessages() {
      const complete = scroll.beforePrepend()
      if (!transcriptWindow.expandEarlier(messages.value, scrollIdentity.value, messageSequence.value)) return
      transcriptRevision.value += 1
      nextTick(complete)
      messageChanges.afterMessagesChange(scrollIdentity.value, messageSequence.value)
    }

    const { actionButton, icon, renderSession, renderSharedText, renderTranscriptMessage } = createViewRenderers({
      ConversationMessage,
      MarkdownRenderer,
      ArtifactScope,
      available,
      busy,
      currentSession,
      openSession,
      resumeSession,
    })

    const receiveHostMessage = ({ data: message }) => {
      BridgeWebSocket.receive(message)
      if (message?.type === 'configuration.changed') {
        clearComposerBuckets()
        BridgeWebSocket.disposeAll(Error('KohakuTerrarium configuration changed'))
        rejectPending(Error('KohakuTerrarium configuration changed'))
        chat.unbindFromInstance()
        currentSession.value = null
        available.value = false
        automatic.value = false
        readyCoordinator.reconcile()
        return
      }
      if (message?.type === 'selection.changed') {
        const eventReadyId = message.readyId ?? activeSelectionReadyId
        const pendingRuntime = eventReadyId === latestReadyRequestId
        if (notificationReadyId === null || eventReadyId !== notificationReadyId) return
        const notification = selectionVersions.beginNotification(eventReadyId, message.data.selectionVersion, pendingRuntime)
        if (!notification) return
        if (message.connectionId !== undefined) acceptComposerConnection(message.connectionId)
        const ownedOperation = selectionOperationEpoch
        const isCurrent = () =>
          ownedOperation === selectionOperationEpoch && eventReadyId === notificationReadyId && notification.isCurrent()
        pendingReconciliations++
        reconciliation = reconciliation
          .then(() => applySelection(message.data.selection, message.data.changed, isCurrent))
          .catch((cause) => {
            if (isCurrent()) error.value = cause.message
          })
          .finally(() => pendingReconciliations--)
        return
      }
      settleRequestMessage(pending, message)
    }
    window.addEventListener('message', receiveHostMessage)
    onBeforeUnmount(() => {
      conversationOwnership.dispose()
      draftBuckets.dispose()
      attachmentBuckets.dispose()
      window.removeEventListener('message', receiveHostMessage)
      readyCoordinator.invalidate()
      BridgeWebSocket.disposeAll(Error('KohakuTerrarium webview disposed'))
      rejectPending(Error('KohakuTerrarium webview disposed'))
    })

    readyCoordinator.reconcile()

    return () => {
      const currentSummary = currentSession.value
        ? `${currentSession.value.session.title} · ${currentSession.value.target || 'Choose a Creature'}`
        : sessions.value.length
          ? `${sessions.value.length} Session${sessions.value.length === 1 ? '' : 's'} · No Session selected`
          : 'No Sessions'
      return h('main', { class: 'kt-conversation-host' }, [
        h('header', { class: 'app-header' }, [
          brandUri ? h('img', { class: 'brand-mark', src: brandUri, alt: '' }) : null,
          h('div', { class: 'header-copy' }, [
            h('h1', 'KohakuTerrarium'),
            h('p', available.value ? (automatic.value ? 'Connected locally' : 'Connected by override') : 'Waiting for local KT'),
          ]),
        ]),
        h('section', { class: 'session-region', 'aria-label': 'Sessions' }, [
          h('div', { class: 'session-toolbar' }, [
            h(
              'button',
              {
                type: 'button',
                class: 'session-disclosure',
                'aria-expanded': sessionsExpanded.value,
                'aria-controls': 'session-list',
                onClick: () => (sessionsExpanded.value = !sessionsExpanded.value),
              },
              [icon('chevron'), h('span', { class: 'session-summary' }, currentSummary)],
            ),
            h('div', { class: 'session-actions' }, [
              actionButton('New Session', 'add', { disabled: busy.value || !available.value, onClick: createSession }),
              actionButton('Refresh Sessions', 'refresh', {
                disabled: busy.value,
                onClick: () => {
                  error.value = ''
                  reconcileSessions().catch((cause) => (error.value = cause.message))
                },
              }),
            ]),
          ]),
          sessionsExpanded.value
            ? h('div', { id: 'session-list', class: 'session-list' }, [
                ...sessions.value.map(renderSession),
                currentSession.value?.targetCreatureId
                  ? actionButton('Stop Session', 'stop', {
                      class: 'stop-session',
                      disabled: busy.value,
                      onClick: stopSession,
                      text: 'Stop Session',
                    })
                  : null,
              ])
            : null,
        ]),
        error.value ? h('p', { class: 'status is-error', role: 'alert' }, error.value) : null,
        status.value ? h('p', { class: 'status', role: 'status', 'aria-live': 'polite' }, status.value) : null,
        h('section', { class: 'chat-region' }, [
          h(ChatTranscriptSection, {
            ...transcriptView.value,
            earlierLabel: `Load ${Math.min(transcriptView.value.earlierCount, 400)} earlier messages`,
            emptyTitle: 'No messages yet',
            emptySubtitle: 'Choose a Session and send a message',
            processing: chat.processingByTab[tab.value],
            processingLabel: 'Kohaku is working',
            reconnecting: chat.wsStatus === 'reconnecting',
            reconnectLabel: 'Reconnecting',
            renderMessage: renderTranscriptMessage,
            onLoadEarlier: loadEarlierMessages,
            ...transcriptCallbacks.value,
          }),
        ]),
        currentSession.value?.target
          ? h(QueuedMessages, {
              key: JSON.stringify(composerOwner()),
              items: chat.queuedMessagesByTab[tab.value] || [],
              connected: chat.wsStatus === 'open' && available.value && activeSelectionReadyId === latestReadyRequestId,
              edit: (item, parts) => hostAcceptedChat.queued.edit(tab.value, item, parts),
              cancel: (item) => hostAcceptedChat.queued.cancel(tab.value, item),
            })
          : null,
        h('section', { class: 'composer-region', 'aria-label': 'Message composer' }, [
          currentSession.value?.target
            ? h(
                ChatComposer,
                {
                  modelValue: draft.value,
                  attachments: attachments.value,
                  processing: !!chat.processingByTab[tab.value],
                  disabled:
                    !currentSession.value?.target ||
                    chat.wsStatus !== 'open' ||
                    isComposerSubmitDisabled(submitBusy.value, !!chat.processingByTab[tab.value]),
                  contextActionsDisabled: busy.value || contextBusy.value || !available.value || !!chat.processingByTab[tab.value],
                  managedSubmit: true,
                  maxAttachmentBytes: 10 * 1024 * 1024,
                  maxImageBytes: 5 * 1024 * 1024,
                  attachmentTransform,
                  showContextActions: true,
                  placeholder: 'Send to selected Creature',
                  labels: composerLabels,
                  'onUpdate:modelValue': (value) => (draft.value = value),
                  'onUpdate:attachments': (value) => (attachments.value = value),
                  onSubmit: send,
                  onInterrupt: () => chat.interrupt(tab.value),
                  onCompact: () => manageContext('context.compact'),
                  onClear: () => manageContext('context.clear'),
                  onError: onComposerError,
                },
                {
                  'compact-icon': () => renderCarbonIcon('collapse-all'),
                  'clear-icon': () => renderCarbonIcon('clean'),
                },
              )
            : h('p', { class: 'composer-placeholder' }, 'Select a Session to start chatting'),
        ]),
      ])
    }
  },
}

createApp(App).use(createPinia()).mount('#app')
