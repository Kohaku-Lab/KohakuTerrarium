import { computed, ref } from 'vue'

import {
  CHAT_AUTO_EXPAND_TOP_PX,
  captureSemanticAnchor,
  createChatHistoryExpander,
  isTailRenderBudgetFull,
  useChatRenderWindow,
} from '@kohakuterrarium/chat-ui'

// One shared transcript viewport for the VS Code webview.
//
// Every moving part comes from the shared `@kohakuterrarium/chat-ui`
// package: the render window (`useChatRenderWindow`) owns windowStart /
// windowMessages / enterHistoryAt, `createChatHistoryExpander` owns the
// manual+scroll+idle expansion transaction, and `captureSemanticAnchor`
// owns the physical `_history_key` compensation. The extension only
// supplies the per-tab store actions, so there is never a second page
// cache or a forked anchor implementation.
export function useTranscriptPaging({ chat, tab, messages, getIdentity, getViewport, isNearBottom = () => true }) {
  const readingEpoch = ref(0)
  const scrolledUp = ref(false)
  let lastScrollTop = 0
  let scrollFrame = null
  // The matching canceller is recorded next to the handle so a fallback
  // timer is never cancelled with cancelAnimationFrame (or vice versa)
  // when only one of the two scheduling globals is present.
  let cancelScrollFrame = null

  const renderWindow = useChatRenderWindow(messages, getIdentity)
  const { enterHistoryAt, expandHistory, isHistoryMode, leaveHistory: clearWindow, windowMessages, windowStart } = renderWindow

  const view = computed(() => {
    const list = messages.value
    const start = windowStart.value
    return {
      messages: windowMessages.value,
      messageOffset: start,
      earlierCount: start,
      totalCount: list.length,
      previousMessage: start > 0 ? list[start - 1] : null,
    }
  })

  const pageState = computed(() => (tab.value ? chat.historyPageByTab?.[tab.value] : null))
  const hasOlder = computed(() => !!pageState.value?.hasOlder)
  const hasNewer = computed(() => !!pageState.value?.hasNewer)
  const resetRequired = computed(() => !!pageState.value?.resetRequired)
  const partial = computed(() => (tab.value ? !!chat.tokenUsage?.[tab.value]?.partial : false))
  const historyBlocked = computed(() => chat.processingByTab?.[tab.value] === true && windowStart.value === 0 && hasOlder.value)
  const canLoadEarlier = computed(() => !historyBlocked.value && (windowStart.value > 0 || hasOlder.value))

  const getContext = () => `${getIdentity()}:${readingEpoch.value}`

  const expander = createChatHistoryExpander({
    initialFill: {
      owner: () => chat,
      generation: () => chat._instanceGeneration,
      key: () => tab.value,
      ready: () => !!getViewport() && !!pageState.value?.historyId,
      atTail: () => isNearBottom() && !isHistoryMode.value,
      needsMore: () => {
        const state = pageState.value
        return !!(
          state?.hasOlder &&
          !state.pending &&
          !state.hasNewer &&
          chat._controllerForTab(tab.value)?.isCurrent() &&
          !isTailRenderBudgetFull(messages.value)
        )
      },
      prefetch: () => chat.prefetchOlderHistory(tab.value),
      materialize: () => chat.materializeOlderHistory(tab.value),
      scroll: () => {
        const element = getViewport()
        if (element) element.scrollTop = element.scrollHeight
      },
    },
    canExpand: () => isHistoryMode.value && (windowStart.value > 0 || hasOlder.value),
    expand: async (step, { idle = false } = {}) => {
      const current = tab.value
      if (!current) return false
      if (windowStart.value > 0) {
        expandHistory(step)
        return true
      }
      if (!hasOlder.value || chat.processingByTab?.[current] === true) return false
      const context = getContext()
      const epoch = readingEpoch.value
      const prefetched = await chat.prefetchOlderHistory(current)
      // A scope switch, return-to-tail, or unmount bumps the reading epoch
      // and must discard this continuation even if the fetch succeeded.
      if (readingEpoch.value !== epoch || getContext() !== context || prefetched?.discarded || idle) return false
      const anchor = captureSemanticAnchor(getViewport, () => messages.value)
      const applied = chat.materializeOlderHistory(current, () => enterHistoryAt(windowStart.value))
      if (!applied?.applied) return false
      expandHistory(step)
      return anchor || true
    },
    getViewportEl: getViewport,
    getMessages: () => messages.value,
    getContext,
  })

  function scheduleScrollFrame(callback) {
    if (typeof requestAnimationFrame === 'function' && typeof cancelAnimationFrame === 'function') {
      cancelScrollFrame = cancelAnimationFrame
      return requestAnimationFrame(callback)
    }
    cancelScrollFrame = clearTimeout
    return setTimeout(callback, 0)
  }

  function cancelScheduledScrollFrame() {
    if (scrollFrame !== null && cancelScrollFrame) cancelScrollFrame(scrollFrame)
    scrollFrame = null
    cancelScrollFrame = null
  }

  function leaveHistory() {
    readingEpoch.value += 1
    expander.cancelInitialFill()
    clearWindow()
  }

  function atTop() {
    const element = getViewport()
    if (!element || !isHistoryMode.value || element.scrollTop > CHAT_AUTO_EXPAND_TOP_PX) return
    expander.maybeExpandAtTop(element.scrollTop)
  }

  function onScroll() {
    const element = getViewport()
    if (!element) return
    if (element.scrollTop < lastScrollTop) {
      scrolledUp.value = true
      expander.cancelInitialFill(true)
      if (!isHistoryMode.value) enterHistoryAt(windowStart.value)
    }
    lastScrollTop = element.scrollTop
    if (scrollFrame !== null) return
    scrollFrame = scheduleScrollFrame(() => {
      scrollFrame = null
      cancelScrollFrame = null
      const near = element.scrollHeight - element.scrollTop - element.clientHeight < 80
      if (near) leaveHistory()
      else if (scrolledUp.value) expander.maybeExpandAtTop(element.scrollTop)
      scrolledUp.value = false
    })
  }

  function onWheel(event) {
    if (event?.deltaY < 0) {
      expander.cancelInitialFill(true)
      atTop()
    }
  }

  function onKeydown(event) {
    if (['ArrowUp', 'PageUp', 'Home'].includes(event?.key)) {
      expander.cancelInitialFill(true)
      atTop()
    }
  }

  let touchY = null
  function onTouchStart(event) {
    touchY = event?.touches?.[0]?.clientY ?? null
  }

  function onTouchMove(event) {
    const y = event?.touches?.[0]?.clientY
    if (touchY != null && y > touchY) {
      expander.cancelInitialFill(true)
      atTop()
    }
    touchY = y
  }

  async function loadEarlier() {
    await expander.expandManual()
  }

  async function reload() {
    expander.cancelInitialFill(true)
    const current = tab.value
    if (!current) return
    if (hasNewer.value) await chat.refreshHistoryHead(current)
    else await chat.initHistoryPage(current)
  }

  const detailPending = ref(null)
  const detailError = ref('')
  async function loadDetail(key) {
    if (detailPending.value !== null || !tab.value) return
    const context = getContext()
    detailPending.value = key
    detailError.value = ''
    try {
      const result = await chat.loadHistoryRecord(tab.value, key)
      if (!result?.applied && context === getContext()) detailError.value = 'History changed — reload'
    } catch (error) {
      if (context === getContext()) detailError.value = error?.message || 'Could not load message'
    } finally {
      detailPending.value = null
    }
  }

  function start() {
    expander.startInitialFill()
  }

  function dispose() {
    readingEpoch.value += 1
    cancelScheduledScrollFrame()
    expander.dispose()
  }

  return {
    view,
    hasOlder,
    hasNewer,
    resetRequired,
    partial,
    historyBlocked,
    canLoadEarlier,
    detailPending,
    detailError,
    isHistoryMode,
    leaveHistory,
    loadEarlier,
    reload,
    loadDetail,
    onScroll,
    onWheel,
    onKeydown,
    onTouchStart,
    onTouchMove,
    start,
    dispose,
  }
}
