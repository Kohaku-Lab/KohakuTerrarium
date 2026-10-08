/**
 * The session's token usage as reactive figures, shared by the Status
 * Usage card, the Usage widget and its dock item. Totals cover the whole
 * session (conversations plus sub-agents); `rows` are the conversations,
 * `subagentTokens` the remainder; context fill is per creature, since each
 * creature has its own conversation and model window.
 */

import { computed } from "vue"

import { contextRows, usageRows } from "./statusModel"

export function useUsage(ctx) {
  const chat = ctx.chat
  const totals = computed(
    () => chat.sessionTokenTotals || { prompt: 0, completion: 0, cached: 0, total: 0 },
  )
  const totalTokens = computed(
    () => totals.value.total || totals.value.prompt + totals.value.completion,
  )
  const rows = computed(() => usageRows(chat.tokenUsage || {}))
  const rowsTotal = computed(() => rows.value.reduce((sum, r) => sum + r.total, 0))
  const subagentTokens = computed(() => Math.max(0, totalTokens.value - rowsTotal.value))
  const contexts = computed(() =>
    contextRows(ctx.instance.value, {
      tokenUsage: chat.tokenUsage || {},
      modelByTab: chat.modelByTab || {},
      rootName: chat._rootSourceName,
      fallback: {
        maxContext: ctx.instance.value?.max_context || 0,
        compactThreshold: ctx.instance.value?.compact_threshold || 0,
      },
    }),
  )
  const activeContext = computed(() => contexts.value.find((r) => r.key === chat.activeTab) || null)
  return { totals, totalTokens, rows, rowsTotal, subagentTokens, contexts, activeContext }
}
