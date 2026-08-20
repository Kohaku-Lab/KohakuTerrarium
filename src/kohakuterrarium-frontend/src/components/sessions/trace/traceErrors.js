const ERROR_TYPES = new Set(["tool_error", "subagent_error", "processing_error"])
const FAILED_STATES = new Set(["error", "interrupted", "cancelled"])

export function isTraceErrorEvent(event) {
  if (!event) return false
  if (ERROR_TYPES.has(event.type)) return true
  if (
    event.success === false ||
    Boolean(event.error) ||
    Boolean(event.interrupted) ||
    Boolean(event.cancelled) ||
    FAILED_STATES.has(String(event.final_state || "").toLowerCase())
  ) {
    return true
  }
  if (event.type !== "tool_result" || event.exit_code == null || event.exit_code === "") {
    return false
  }
  const exitCode = Number(event.exit_code)
  return Number.isFinite(exitCode) && exitCode !== 0
}
