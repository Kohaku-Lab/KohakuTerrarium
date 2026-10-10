/**
 * How a recent saved session reads in the lab: what it was about (its
 * summary) first; a name only where the user gave one. Takes a `historyRow`
 * and is pure.
 */

/** The row's headline: the summary, else the session's label. */
export function recentHeadline(row) {
  return row.summary || row.label
}

/** The title the user gave the session, when the headline is its summary; else "". */
export function recentTitle(row) {
  return row.summary && row.labelFrom === "title" ? row.label : ""
}
