/** User-facing labels never replace the storage key used for session operations. */
export function savedSessionLabel(session) {
  const candidates = [
    session?.terrarium_name,
    session?.agents?.[0],
    session?.session_name,
    session?.name,
  ]
  return candidates.find((value) => typeof value === "string" && value.trim())?.trim() || ""
}
