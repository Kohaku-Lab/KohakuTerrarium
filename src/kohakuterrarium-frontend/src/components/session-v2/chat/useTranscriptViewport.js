/**
 * The transcript viewport of the v2 chat column: which messages are
 * mounted (render window), paging older history in, and keeping the view
 * pinned to the tail while it streams. Built on the shared chat modules:
 * chatRenderWindow (tail budget, semantic history anchors),
 * chatHistoryExpand (one expansion transaction for manual and scroll
 * paging), chatScrollScheduler (one rAF-coalesced scroll per commit).
 *
 * `chat` is the scoped chat store, `tabKey` the conversation shown,
 * `instanceId` the session handle (both computed refs); `positions` holds
 * each conversation's scroll offset and may outlive the column.
 */

import {
  computed,
  nextTick,
  onActivated,
  onBeforeUnmount,
  onDeactivated,
  onMounted,
  onUnmounted,
  ref,
  watch,
} from "vue"

import {
  captureSemanticAnchor,
  CHAT_AUTO_EXPAND_TOP_PX,
  createChatHistoryExpander,
} from "@/components/chat/chatHistoryExpand"
import {
  CHAT_RENDER_EXPAND_MESSAGE_LIMIT,
  CHAT_RENDER_EXPAND_UNIT_BUDGET,
  CHAT_RENDER_MESSAGE_LIMIT,
  CHAT_RENDER_UNIT_BUDGET,
  isTailRenderBudgetFull,
  useChatRenderWindow,
} from "@/components/chat/chatRenderWindow"
import { createChatScrollScheduler } from "@/components/chat/chatScrollScheduler"

const NEAR_BOTTOM_PX = 80

export function useTranscriptViewport({ chat, tabKey, instanceId, positions = new Map() }) {
  const viewportEl = ref(null)
  const isNearBottom = ref(true)
  const forceScrollOnNextUpdate = ref(true)
  const scrollPositions = positions
  const scope = computed(() => ({ instanceId: instanceId.value, tab: tabKey.value }))
  const messages = computed(() => (tabKey.value ? chat.messagesByTab[tabKey.value] || [] : []))
  const processing = computed(() => (tabKey.value ? !!chat.processingByTab[tabKey.value] : false))

  const scrollKey = (inst = instanceId.value, tab = tabKey.value) =>
    inst && tab ? `${inst}:${tab}` : ""

  // Bumped whenever the reading intent changes; a pending page fetch from an older epoch is dropped.
  let readingEpoch = 0
  let disposed = false
  const {
    enterHistoryAt,
    expandHistory,
    isHistoryMode,
    leaveHistory: leaveWindowHistory,
    restoreHistory,
    windowMessages,
    windowStart,
  } = useChatRenderWindow(messages, () => scrollKey())

  const hasOlderHistory = computed(() =>
    tabKey.value ? !!chat.historyPageByTab?.[tabKey.value]?.hasOlder : false,
  )
  const historyBlocked = computed(
    () => processing.value && windowStart.value === 0 && hasOlderHistory.value,
  )

  let lastScrollTop = 0
  const historyExpander = createChatHistoryExpander({
    initialFill: {
      owner: () => chat,
      generation: () => chat._instanceGeneration,
      key: () => tabKey.value,
      ready: () => !!viewportEl.value && !!chat.historyPageByTab?.[tabKey.value]?.historyId,
      atTail: () => isNearBottom.value && !isHistoryMode.value,
      needsMore: () => {
        const state = chat.historyPageByTab?.[tabKey.value]
        return (
          state?.hasOlder &&
          !state.pending &&
          !state.hasNewer &&
          chat._controllerForTab(tabKey.value)?.isCurrent() &&
          !isTailRenderBudgetFull(messages.value)
        )
      },
      prefetch: () => chat.prefetchOlderHistory(tabKey.value),
      materialize: () => chat.materializeOlderHistory(tabKey.value),
      scroll: () => scrollToBottom(),
    },
    onCompensated: () => {
      lastScrollTop = viewportEl.value?.scrollTop || 0
    },
    canExpand: () => isHistoryMode.value && (windowStart.value > 0 || hasOlderHistory.value),
    expand: async (step, { idle = false } = {}) => {
      const tab = tabKey.value
      if (!tab) return false
      if (windowStart.value > 0) {
        expandHistory(step)
        return true
      }
      if (!hasOlderHistory.value || processing.value) return false
      const context = scrollKey()
      const epoch = readingEpoch
      const prefetched = await chat.prefetchOlderHistory(tab)
      if (
        disposed ||
        readingEpoch !== epoch ||
        scrollKey() !== context ||
        prefetched?.discarded ||
        idle
      )
        return false
      const anchor = captureSemanticAnchor(
        () => viewportEl.value,
        () => messages.value,
      )
      const applied = chat.materializeOlderHistory(tab, () => enterHistoryAt(windowStart.value))
      if (!applied?.applied) return false
      expandHistory(step)
      return anchor || true
    },
    getViewportEl: () => viewportEl.value,
    getMessages: () => messages.value,
    getContext: () => `${scrollKey()}:${readingEpoch}`,
    autoStep: {
      unitBudget: CHAT_RENDER_EXPAND_UNIT_BUDGET,
      messageLimit: CHAT_RENDER_EXPAND_MESSAGE_LIMIT,
    },
    manualStep: { unitBudget: CHAT_RENDER_UNIT_BUDGET, messageLimit: CHAT_RENDER_MESSAGE_LIMIT },
  })

  function leaveHistory() {
    readingEpoch += 1
    historyExpander.cancelIdleExpand()
    leaveWindowHistory()
  }

  // False while a cached column is hidden: its detached element measures zero.
  let shown = true

  function updateNearBottom() {
    const el = viewportEl.value
    if (!el || !shown) return
    isNearBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < NEAR_BOTTOM_PX
    lastScrollTop = el.scrollTop
  }

  function savePosition(inst = instanceId.value, tab = tabKey.value) {
    const el = viewportEl.value
    const key = scrollKey(inst, tab)
    if (el && key && shown) scrollPositions.set(key, el.scrollTop)
  }

  function restorePosition(inst = instanceId.value, tab = tabKey.value) {
    const el = viewportEl.value
    const key = scrollKey(inst, tab)
    if (!el || !key) return false
    const saved = scrollPositions.get(key)
    if (saved == null) {
      el.scrollTop = el.scrollHeight
      updateNearBottom()
      return false
    }
    el.scrollTop = Math.max(0, Math.min(saved, el.scrollHeight - el.clientHeight))
    updateNearBottom()
    return true
  }

  function scrollToBottom() {
    leaveHistory()
    const el = viewportEl.value
    if (!el || !shown) return
    scrollScheduler.resume()
    el.scrollTop = el.scrollHeight
    updateNearBottom()
    savePosition()
  }

  const scrollScheduler = createChatScrollScheduler({
    afterDomCommit: nextTick,
    requestFrame: (callback) => requestAnimationFrame(callback),
    cancelFrame: (id) => cancelAnimationFrame(id),
    shouldScroll: () => isNearBottom.value,
    scroll: scrollToBottom,
  })
  const scheduleScrollToBottom = (force = false) => scrollScheduler.schedule(force, scope.value)

  let scrollFrame = null
  let scrolledUp = false
  let touchY = null

  function nudgeHistoryAtTop() {
    const el = viewportEl.value
    if (!el || !isHistoryMode.value || el.scrollTop > CHAT_AUTO_EXPAND_TOP_PX) return
    historyExpander.maybeExpandAtTop(el.scrollTop)
  }

  const handlers = {
    onScroll() {
      const el = viewportEl.value
      if (el && el.scrollTop < lastScrollTop) {
        scrolledUp = true
        historyExpander.cancelInitialFill(true)
        if (!isHistoryMode.value) enterHistoryAt(windowStart.value)
        isNearBottom.value = false
        scrollScheduler.suppress()
      }
      if (el) lastScrollTop = el.scrollTop
      if (scrollFrame !== null) return
      scrollFrame = requestAnimationFrame(() => {
        scrollFrame = null
        updateNearBottom()
        if (isNearBottom.value) {
          leaveHistory()
          scrollScheduler.resume()
        } else if (el && scrolledUp) {
          historyExpander.maybeExpandAtTop(el.scrollTop)
        }
        scrolledUp = false
        savePosition()
      })
    },
    onWheel(event) {
      if (event.deltaY < 0) {
        historyExpander.cancelInitialFill(true)
        nudgeHistoryAtTop()
      }
    },
    onKeydown(event) {
      if (["ArrowUp", "PageUp", "Home"].includes(event.key)) {
        historyExpander.cancelInitialFill(true)
        nudgeHistoryAtTop()
      }
    },
    onTouchStart(event) {
      touchY = event.touches[0]?.clientY ?? null
    },
    onTouchMove(event) {
      const y = event.touches[0]?.clientY
      if (touchY != null && y > touchY) {
        historyExpander.cancelInitialFill(true)
        nudgeHistoryAtTop()
      }
      touchY = y
    },
    onViewportReady(viewport) {
      viewportEl.value = viewport
      viewport.classList.add("chat-messages-viewport")
      nextTick(() => {
        if (viewportEl.value !== viewport) return
        forceScrollOnNextUpdate.value = !restorePosition()
      })
    },
  }

  // The transcript element unmounts while the conversation is empty; keep its offset, drop the handle.
  function detach() {
    if (!viewportEl.value) return
    savePosition()
    viewportEl.value = null
    if (scrollFrame !== null) {
      cancelAnimationFrame(scrollFrame)
      scrollFrame = null
    }
  }

  // A cheap signature of the tail drives autoscroll; the full list is never compared.
  const tailSignature = computed(() => {
    const list = messages.value
    const last = list[list.length - 1]
    if (!last) return "0"
    const contentLen =
      typeof last.content === "string"
        ? last.content.length
        : Array.isArray(last.content)
          ? last.content.length
          : 0
    const parts = Array.isArray(last.parts)
      ? last.parts
          .map((part) =>
            part.type === "text"
              ? `t:${part.content?.length || 0}`
              : `o:${part.status || ""}:${part.result?.length || 0}:${part.children?.length || 0}`,
          )
          .join("|")
      : ""
    return `${list.length}:${last.id}:${last.role}:${contentLen}:${parts}`
  })

  watch(
    () => [scope.value, tailSignature.value],
    ([s, next], previous) => {
      const [prevScope, prev] = previous || []
      if (s !== prevScope || !prev || next === prev) return
      const force = forceScrollOnNextUpdate.value
      forceScrollOnNextUpdate.value = false
      scheduleScrollToBottom(force)
    },
  )

  watch(
    () => [scope.value, processing.value],
    ([s, on], previous) => {
      if (s === previous?.[0] && on) scheduleScrollToBottom()
    },
  )

  watch(
    scope,
    (next, previous) => {
      readingEpoch += 1
      historyExpander.cancelInitialFill()
      scrolledUp = false
      scrollScheduler.invalidate()
      scrollScheduler.resume()
      historyExpander.cancelIdleExpand()
      if (scrollFrame !== null) {
        cancelAnimationFrame(scrollFrame)
        scrollFrame = null
      }
      if (previous?.instanceId && previous.tab) savePosition(previous.instanceId, previous.tab)
      restoreHistory(scrollKey(next.instanceId, next.tab))
      nextTick(() => {
        if (next !== scope.value) return
        forceScrollOnNextUpdate.value = !restorePosition(next.instanceId, next.tab)
      })
    },
    { immediate: true },
  )

  // A refused older-page fetch resumes once the turn ends, so a reader parked at the top needs no new gesture.
  watch(processing, (on) => {
    if (on || !isHistoryMode.value) return
    const el = viewportEl.value
    if (el && el.scrollTop <= CHAT_AUTO_EXPAND_TOP_PX)
      historyExpander.maybeExpandAtTop(el.scrollTop)
  })

  watch(
    () => [scope.value, chat._instanceGeneration, chat.historyPageByTab?.[tabKey.value]?.historyId],
    () => {
      if (!chat.historyPageByTab?.[tabKey.value]?.historyId) historyExpander.cancelInitialFill()
      else nextTick(() => historyExpander.startInitialFill())
    },
    { flush: "post" },
  )

  // After the user sends: return to the tail and follow the reply.
  function followAfterSend() {
    isNearBottom.value = true
    leaveHistory()
    scrollScheduler.resume()
    scheduleScrollToBottom(true)
  }

  /** Bring message `id` into view, widening the render window when it lies above it. */
  async function scrollToMessage(id) {
    const index = messages.value.findIndex((m) => m.id === id)
    if (index < 0) return
    scrollScheduler.suppress()
    isNearBottom.value = false
    if (index < windowStart.value) {
      enterHistoryAt(index)
      await nextTick()
    }
    const el = viewportEl.value
    const node = el?.querySelector(`[data-message-id="${CSS.escape(String(id))}"]`)
    if (node?.scrollIntoView) node.scrollIntoView({ behavior: "smooth", block: "center" })
  }

  async function reloadHistory() {
    historyExpander.cancelInitialFill(true)
    if (chat.historyPageByTab[tabKey.value]?.hasNewer) await chat.refreshHistoryHead(tabKey.value)
    else await chat.initHistoryPage(tabKey.value)
  }

  onMounted(() => nextTick(() => historyExpander.startInitialFill()))
  // A cached column's element is re-inserted on activation, which resets its scroll offset:
  // a reader who was following the tail returns to it, anyone else to their saved offset.
  let followingWhenHidden = true
  onDeactivated(() => {
    followingWhenHidden = isNearBottom.value && !isHistoryMode.value
    shown = false
  })
  onActivated(() => {
    if (shown) return
    shown = true
    nextTick(() => {
      if (!viewportEl.value) return
      if (followingWhenHidden) scrollToBottom()
      else restorePosition()
    })
  })
  onBeforeUnmount(() => savePosition())
  onUnmounted(() => {
    disposed = true
    readingEpoch += 1
    scrollScheduler.dispose()
    historyExpander.dispose()
    if (scrollFrame !== null) cancelAnimationFrame(scrollFrame)
  })

  return {
    viewportEl,
    messages,
    processing,
    windowMessages,
    windowStart,
    hasOlderHistory,
    historyBlocked,
    isHistoryMode,
    enterHistoryAt,
    handlers,
    detach,
    scrollToBottom,
    scheduleScrollToBottom,
    followAfterSend,
    scrollToMessage,
    loadEarlier: () => historyExpander.expandManual(),
    reloadHistory,
  }
}
