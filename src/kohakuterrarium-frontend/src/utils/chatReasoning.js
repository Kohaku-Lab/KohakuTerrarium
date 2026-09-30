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

const normalizeForCompare = (text) =>
  String(text || "")
    .replace(/\s+/g, " ")
    .trim()

function collectForms(run) {
  const forms = new Map()
  for (const segment of run) {
    const id = `${segment.source || "reasoning"}\u0000${segment.key ?? ""}`
    let form = forms.get(id)
    if (!form) {
      form = { source: segment.source || "reasoning", text: "", signature: "" }
      forms.set(id, form)
    }
    form.text += segment.text || ""
    if (segment.signature) form.signature = segment.signature
  }
  return [...forms.values()]
}

function dropRedundantForms(forms) {
  const kept = []
  for (const form of forms) {
    const norm = normalizeForCompare(form.text)
    const covering = norm ? kept.find((k) => k.norm.includes(norm)) : null
    if (covering) {
      if (form.signature && !covering.form.signature) covering.form.signature = form.signature
      covering.sources.push(form.source)
      continue
    }
    const covered = norm ? kept.find((k) => k.norm && norm.includes(k.norm)) : null
    if (covered) {
      if (covered.form.signature && !form.signature) form.signature = covered.form.signature
      covered.sources.push(form.source)
      covered.form = form
      covered.norm = norm
      continue
    }
    kept.push({ form, norm, sources: [form.source] })
  }
  return kept
}

const sourceLabel = (entries) => [...new Set(entries.flatMap((k) => k.sources))].join(" + ")

function withSignature(form) {
  if (!form.signature) return form.text
  return form.text
    ? `${form.text}\n[signature: ${form.signature}]`
    : `[signature: ${form.signature}]`
}

function mergeReasoningRun(run) {
  if (run.length === 1) return run[0]
  const entries = dropRedundantForms(collectForms(run))
  if (entries.length === 1) {
    const [{ form }] = entries
    const merged = { type: "reasoning", source: sourceLabel(entries), text: form.text }
    if (form.signature) merged.signature = form.signature
    return merged
  }
  return {
    type: "reasoning",
    source: sourceLabel(entries),
    text: entries.map(({ form }) => `[${form.source}]\n${withSignature(form)}`).join("\n\n"),
  }
}

/**
 * Collapse each run of adjacent reasoning segments into one segment.
 *
 * Providers that stream the same thinking in two forms (plain and structured)
 * persist them as alternating fragments; this rebuilds each form, drops a form
 * whose text is contained in another, and never merges across text/tool segments.
 *
 * @param {object[]|null|undefined} segments
 * @returns {object[]|null|undefined}
 */
export function mergeReasoningSegments(segments) {
  if (!Array.isArray(segments)) return segments
  const out = []
  let run = []
  const flush = () => {
    if (run.length) out.push(mergeReasoningRun(run))
    run = []
  }
  for (const segment of segments) {
    if (!segment || typeof segment !== "object") continue
    if (segment.type === "reasoning") run.push(segment)
    else {
      flush()
      out.push(segment)
    }
  }
  flush()
  return out
}

export default extractReasoning
