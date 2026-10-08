/**
 * Turn a raw sub-agent conversation (OpenAI-shaped messages) into render
 * blocks for the chat-ui ConversationMessage: system prompts, user turns,
 * and assistant turns with their tool calls and results joined. Pure.
 */

import { extractReasoning, mergeReasoningSegments } from "@/utils/chatReasoning"

function messageText(message) {
  if (typeof message?.content === "string") return message.content
  if (!Array.isArray(message?.content)) return ""
  return message.content
    .filter((part) => part?.type === "text")
    .map((part) => part.text || "")
    .join("\n")
}

function contentParts(message) {
  return Array.isArray(message?.content) ? message.content : []
}

function parseArgs(raw) {
  if (!raw) return {}
  if (typeof raw !== "string") return raw
  try {
    return JSON.parse(raw)
  } catch {
    return { raw }
  }
}

function assistantMessage(message, resultById, index) {
  const callById = new Map()
  const tools = (message?.tool_calls || []).map((call, callIndex) => {
    const tool = {
      type: "tool",
      id: call.id || `sa_${index}_${callIndex}`,
      name: call.function?.name || "tool",
      kind: "tool",
      args: parseArgs(call.function?.arguments),
      status: "done",
      result: call.id != null ? resultById[call.id] || "" : "",
      children: [],
    }
    if (call.id != null) callById.set(call.id, tool)
    return tool
  })
  const segments = Array.isArray(message?._kt_assistant_segments)
    ? mergeReasoningSegments(message._kt_assistant_segments)
    : []
  const content = messageText(message)
  const parts = []
  const placed = new Set()
  for (const segment of segments) {
    if (!segment || typeof segment !== "object") continue
    if (segment.type === "reasoning")
      parts.push({
        type: "reasoning",
        id: `sa_${index}_r${parts.length}`,
        source: segment.source || "reasoning",
        text: segment.text || "",
      })
    else if (segment.type === "text" && segment.text)
      parts.push({ type: "text", id: `sa_${index}_c${parts.length}`, content: segment.text })
    else if (segment.type === "tool_call_ref" && callById.has(segment.call_id)) {
      const tool = callById.get(segment.call_id)
      placed.add(tool.id)
      parts.push(tool)
    }
  }
  if (!segments.length) {
    for (const entry of extractReasoning(message))
      parts.push({
        type: "reasoning",
        id: `sa_${index}_r${parts.length}`,
        source: entry.label,
        text: entry.text,
      })
    if (content) parts.push({ type: "text", id: `sa_${index}_c${parts.length}`, content })
  }
  for (const tool of tools) if (!placed.has(tool.id)) parts.push(tool)
  for (const part of contentParts(message))
    if (part.type === "image_url")
      parts.push({
        type: "image_url",
        id: `sa_${index}_img${parts.length}`,
        image_url: part.image_url,
        meta: part.meta,
      })
  return { role: "assistant", content, parts }
}

/** Blocks: {kind: "system", text} | {kind: "user" | "assistant", message}. Tool messages fold into their call. */
export function subagentBlocks(messages = []) {
  const resultById = {}
  for (const message of messages)
    if (message?.role === "tool" && message.tool_call_id != null)
      resultById[message.tool_call_id] = messageText(message)
  const blocks = []
  messages.forEach((message, index) => {
    if (message?.role === "tool") return
    if (message?.role === "system") blocks.push({ kind: "system", text: messageText(message) })
    else if (message?.role === "user") {
      const parts = contentParts(message)
      blocks.push({
        kind: "user",
        message: {
          role: "user",
          content: messageText(message),
          contentParts: parts.length ? parts : undefined,
        },
      })
    } else blocks.push({ kind: "assistant", message: assistantMessage(message, resultById, index) })
  })
  return blocks
}
