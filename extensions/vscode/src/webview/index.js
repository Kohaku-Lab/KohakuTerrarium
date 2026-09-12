import {
  buildMessageParts,
  ChatComposer,
  ChatTranscriptSection,
  MarkdownRenderer,
  MessageRow,
  ModelSwitcher,
  provideMessageActions,
} from '@kohakuterrarium/chat-ui'
import 'virtual:uno.css'
import { computed, h, nextTick, onBeforeUnmount, ref, watch } from 'vue'

import { useChatStore } from '@/stores/chat'

import { bootWebview } from './boot.mjs'
import { BridgeWebSocket } from './bridge.js'
import { renderCarbonIcon } from './carbonIcons.mjs'
import { bindComposerBuffer } from './composerBuffer.mjs'
import { useComposerSlash } from './composerSlash.mjs'
import { installGoalBridge } from './goalBridge.mjs'
import { installHostFacades } from './hostFacades.mjs'
import { installHostMediaResolver } from './mediaHostBridge.mjs'
import { installExtensionModelSwitcher } from './modelSwitcherHost.mjs'
import { applyContextCommandOutcome } from './contextCommandResult.mjs'
import { createHostAcceptedChat, createObservedWebSocket } from './hostAcceptedChat.mjs'
import { createConversationScrollController, isNearBottom } from './conversationScroll.mjs'
import {
  createConversationAttachments,
  createConversationDrafts,
  createConversationOwnership,
  isConversationSuperseded,
} from './conversationOwnership.mjs'
import { createReadyCoordinator } from './readyCoordinator.mjs'
import { createRequestLifecycle } from './requestLifecycle.mjs'
import { createSelectionVersionOwner } from './selectionVersion.mjs'
import { createSessionShell } from './sessionShell.js'
import { createSessionActions, createTargetSelector } from './sessionActions.mjs'
import { createSubmitGate, isComposerSubmitDisabled } from './submitGate.mjs'
import { applyTopologySelection } from './topologySelection.mjs'
import { createTranscriptBindings } from './transcriptWindow.mjs'
import { useTranscriptPaging } from './transcriptPaging.mjs'
import { createViewRenderers } from './viewRenderers.mjs'
import './style.css'
import { installNotificationSurface } from './notifications.mjs'
import './notifications.css'
import QueuedMessages from './QueuedMessages.vue'
import { composerLabels } from './composerLabels.mjs'

const vscode = acquireVsCodeApi()
// The request lifecycle is a small real module; only branch-mutation requests
// pass ``timeoutMs: 0`` (a long rerun POST is never abandoned by a client timer).
const {
  request,
  rejectAll: rejectPending,
  settle: settleRequest,
} = createRequestLifecycle({
  postMessage: (message) => vscode.postMessage(message),
})

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
    // Bumped on every backend/ready ownership change (new connection or reset);
    // the shared model picker keys its directory cache and drawer invalidation
    // on it, so a new host never leaks the previous host's models.
    const hostEpoch = ref(0)
    // Reactive ready epoch: the fetch fence AND the media generation a leaf re-resolves against.
    const latestReadyRequestId = ref(null)
    const composerOwner = () => {
      // ``admittedReadyId`` is the epoch whose selection is actually armed. The
      // requested ``readyId`` advances before ``ready.result``; a model request
      // must not dispatch on it while a new reconcile is still outstanding.
      const admitted = available.value && activeSelectionReadyId !== null && activeSelectionReadyId === latestReadyRequestId.value
      return {
        readyId: latestReadyRequestId.value,
        admittedReadyId: admitted ? activeSelectionReadyId : null,
        runtimeId: currentSession.value?.session?.runtimeId,
        creatureId: currentSession.value?.targetCreatureId,
      }
    }
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
    onBeforeUnmount(() => (BridgeWebSocket.getReadyId = () => null))

    const currentConversationOwnership = () => ({ ...composerOwner(), name: currentSession.value?.target })
    const conversationOwnership = createConversationOwnership(currentConversationOwnership)
    const submitGate = createSubmitGate()
    const submitRevision = ref(0)
    const submitBusy = computed(() => (submitRevision.value, attachmentRevision.value, submitGate.busy(currentConversationOwnership())))
    const attachmentTransform = conversationOwnership.transform((file) => file)
    const getReadFence = () =>
      activeSelectionReadyId === latestReadyRequestId.value && available.value
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
      if (activeSelectionReadyId !== latestReadyRequestId.value) throw Error('Wait for Session refresh')
      const result = await request(type, { ...data, readyId: activeSelectionReadyId })
      if (result.readyId !== latestReadyRequestId.value) return result
      selectionVersions.acceptResult(result.readyId, result.selectionVersion, result.readyId === latestReadyRequestId.value)
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
    const selectTarget = createTargetSelector({
      shell,
      currentSession,
      error,
      getReadyId: () => activeSelectionReadyId,
      getOperationEpoch: () => selectionOperationEpoch,
    })
    installExtensionModelSwitcher({
      chat,
      getSession: () => currentSession.value,
      selectTarget,
      getHostEpoch: () => hostEpoch.value,
    })
    const tab = computed(() => currentSession.value?.target || '')
    const slash = useComposerSlash({ chat, draft, tab })
    const messages = computed(() => chat.messagesByTab[tab.value] || [])
    const scrollIdentity = computed(() =>
      JSON.stringify([currentSession.value?.session?.runtimeId, currentSession.value?.targetCreatureId]),
    )
    watch(scrollIdentity, () => {
      attachmentRevision.value += 1
      draftRevision.value += 1
    })
    const scroll = createConversationScrollController({ schedule: nextTick })
    let transcriptViewport = null
    const paging = useTranscriptPaging({
      chat,
      tab,
      messages,
      getIdentity: () => scrollIdentity.value,
      getViewport: () => transcriptViewport,
      isNearBottom: () => isNearBottom(transcriptViewport),
    })

    watch([scrollIdentity, () => messages.value.length], ([identity, count]) => scroll.setIdentity(identity, { hasMessages: count > 0 }), {
      immediate: true,
    })
    watch(
      () => messages.value.length,
      (count) => scroll.onMessagesUpdated({ hasMessages: count > 0 }),
      { flush: 'post' },
    )
    watch(
      () => chat.processingByTab[tab.value],
      (processing) => {
        if (processing) scroll.onMessagesUpdated({ hasMessages: messages.value.length > 0 })
      },
    )
    watch(
      () => [scrollIdentity.value, chat._instanceGeneration, chat.historyPageByTab?.[tab.value]?.historyId],
      () => {
        if (!chat.historyPageByTab?.[tab.value]?.historyId) paging.leaveHistory()
        else nextTick(() => paging.start())
      },
      { flush: 'post' },
    )
    onBeforeUnmount(() => {
      scroll.dispose()
      paging.dispose()
    })
    for (const uninstall of installHostFacades({ request, getOwner: composerOwner })) onBeforeUnmount(uninstall)

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
    let composerConnectionId = null
    let selectionOperationEpoch = 0
    let notificationReadyId = null
    // Shared media leaves resolve through the Host (media.prepare -> spooled URI).
    installHostMediaResolver({ request, getFence: getReadFence, getOwner: composerOwner, error })
    function clearComposerBuckets() {
      notifications.clear()
      draftBuckets.clearAll()
      attachmentBuckets.clearAll()
      draftRevision.value += 1
      attachmentRevision.value += 1
    }
    function acceptComposerConnection(connectionId) {
      if (composerConnectionId !== connectionId) {
        clearComposerBuckets()
        hostEpoch.value += 1
      }
      composerConnectionId = connectionId
    }
    const readyCoordinator = createReadyCoordinator({
      requestReady: () =>
        request('ready', {}, (id) => {
          latestReadyRequestId.value = id
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
          hostEpoch.value += 1
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
        hostEpoch.value += 1
        available.value = false
        chat.unbindFromInstance()
        currentSession.value = null
        sessions.value = []
        error.value = cause.message
      },
    })

    const reconcileSessions = () => readyCoordinator.reconcile()

    const { createSession, resumeSession, stopSession, openSession } = createSessionActions({
      shell,
      currentSession,
      busy,
      error,
      reloadSessions,
    })

    async function send({ text = draft.value, attachments: submittedAttachments = attachments.value } = {}) {
      if ((!text.trim() && submittedAttachments.length === 0) || !currentSession.value?.target) return
      if (slash.chooseAtSubmit()) return
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
          return slash.send(submittedText, assertCurrent, () => hostAcceptedChat.send(content))
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

    // Message actions use Host clipboard, accepted replies and current ready identity.
    provideMessageActions({
      markdownOrigin: null,
      writeClipboard: (text) => request('platform.writeClipboard', { text }),
      submitReply: (message, actionId, values) => submitReply(message, actionId, values),
      getViewOwner: () => `${activeSelectionReadyId ?? ''}:${latestReadyRequestId.value ?? ''}`,
    })

    const transcriptBindings = createTranscriptBindings({
      onViewportReady: (viewport, identity) => {
        transcriptViewport = viewport
        scroll.onViewportReady(viewport, identity)
        paging.start()
      },
      onScroll: (event, identity) => {
        scroll.onScroll(event, identity)
        paging.onScroll()
      },
      onWheel: (event) => paging.onWheel(event),
      onKeydown: (event) => paging.onKeydown(event),
      onTouchStart: (event) => paging.onTouchStart(event),
      onTouchMove: (event) => paging.onTouchMove(event),
      onReply: ({ message, actionId, values }) => submitReply(message, actionId, values),
    })
    const transcriptCallbacks = computed(() => transcriptBindings.forIdentity(scrollIdentity.value))

    const { actionButton, icon, renderSession, renderSharedText, renderTranscriptMessage } = createViewRenderers({
      MessageRow,
      MarkdownRenderer,
      available,
      busy,
      currentSession,
      openSession,
      resumeSession,
      historyDetail: { pending: paging.detailPending, load: paging.loadDetail },
      request,
      getReadyId: () => BridgeWebSocket.getReadyId(),
    })

    const receiveHostMessage = ({ data: message }) => {
      BridgeWebSocket.receive(message)
      if (message?.type === 'configuration.changed') {
        clearComposerBuckets()
        hostEpoch.value += 1
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
        const pendingRuntime = eventReadyId === latestReadyRequestId.value
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
      settleRequest(message)
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
          available.value && currentSession.value?.target ? h(ModelSwitcher, { class: 'header-model' }) : null,
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
            ...paging.view.value,
            earlierLabel: paging.view.value.earlierCount
              ? `Load ${Math.min(paging.view.value.earlierCount, 400)} earlier messages`
              : 'Load earlier messages',
            emptyTitle: 'No messages yet',
            emptySubtitle: 'Choose a Session and send a message',
            processing: chat.processingByTab[tab.value],
            processingLabel: 'Kohaku is working',
            reconnecting: chat.wsStatus === 'reconnecting',
            reconnectLabel: 'Reconnecting',
            partial: paging.partial.value,
            partialLabel: 'Loaded history / partial statistics (including loaded sub-agent usage)',
            hasNewer: paging.hasNewer.value,
            newerLabel: 'Newer messages pending — Reload',
            resetRequired: paging.resetRequired.value,
            resetLabel: 'Reload history',
            historyBlocked: paging.historyBlocked.value,
            historyBlockedLabel: 'History is loading — finish generation to load earlier messages',
            canLoadEarlier: paging.canLoadEarlier.value,
            renderMessage: renderTranscriptMessage,
            onLoadEarlier: () => paging.loadEarlier(),
            onReload: () => paging.reload(),
            ...transcriptCallbacks.value,
          }),
        ]),
        currentSession.value?.target
          ? h(QueuedMessages, {
              key: JSON.stringify(composerOwner()),
              items: chat.queuedMessagesByTab[tab.value] || [],
              connected: chat.wsStatus === 'open' && available.value && activeSelectionReadyId === latestReadyRequestId.value,
              edit: (item, parts) => hostAcceptedChat.queued.edit(tab.value, item, parts),
              cancel: (item) => hostAcceptedChat.queued.cancel(tab.value, item),
            })
          : null,
        h('section', { class: 'composer-region', 'aria-label': 'Message composer' }, [
          currentSession.value?.target
            ? h(
                ChatComposer,
                {
                  ...slash.props.value,
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
                  suggestions: slash.suggestions,
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

bootWebview(App)
