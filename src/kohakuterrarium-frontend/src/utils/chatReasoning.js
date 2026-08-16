/**
 * Extract provider-owned chain-of-thought fields from a raw conversation
 * message dict (OpenAI-format snapshot).
 *
 * @param {object|null} message
 * @returns {{label: string, text: string}[]}
 */
export function extractReasoning(message) {
  if (!message || message.role !== "assistant") return []

  const entries = []
  const push = (label, text) => {
    if (typeof text === "string" && text) entries.push({ label, text })
  }

  // Legacy serialized snapshots may keep provider fields in ``extra_fields``.
  const fields = { ...(message.extra_fields || {}), ...message }

  push("reasoning_content", fields.reasoning_content)
  push("reasoning", fields.reasoning)
  push("reasoning_summary", fields.reasoning_summary)

  for (const [index, block] of (fields.reasoning_details || []).entries()) {
    if (!block || typeof block !== "object") continue
    let text = block.text || block.thinking || block.data || ""
    const signature = block.signature || ""
    if (signature) text = `${text}\n[signature: ${signature}]`
    if (text) entries.push({ label: `reasoning_details[${index}]:${block.type || "?"}`, text })
  }

  for (const [index, block] of (fields._kt_anthropic_content || []).entries()) {
    if (!block || !["thinking", "redacted_thinking"].includes(block.type)) continue
    let text = block.thinking || block.data || ""
    const signature = block.signature || ""
    if (signature) text = `${text}\n[signature: ${signature}]`
    if (text) entries.push({ label: `anthropic:${block.type}[${index}]`, text })
  }

  return entries
}

export default extractReasoning
