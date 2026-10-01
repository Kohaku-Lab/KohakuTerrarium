/** Attach-tab metadata for a session the server just resumed.
 *
 * The resume response names the session in `session_name` and reports
 * `type` as "terrarium" or "agent"; tabs know "terrarium" and "creature".
 */
export function resumedTabMeta(result, fallbackName) {
  return {
    config_name: result?.session_name || result?.session?.name || fallbackName,
    type: result?.type === "terrarium" ? "terrarium" : "creature",
  }
}
