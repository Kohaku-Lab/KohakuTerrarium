/**
 * Built-in tool block bodies, used when no UI extension registered a
 * renderer for the tool; a package renderer for the same name wins.
 */

import { markRaw } from "vue"

import FilePreviewBody from "./FilePreviewBody.vue"

const FILE_PREVIEW = Object.freeze({ key: "builtin", component: markRaw(FilePreviewBody) })

export const BUILTIN_TOOL_BODIES = Object.freeze({
  write: FILE_PREVIEW,
  edit: FILE_PREVIEW,
  multi_edit: FILE_PREVIEW,
})

/** The built-in body for a tool call, only when it carries a file preview. */
export function builtinToolBody(call) {
  const body = BUILTIN_TOOL_BODIES[call?.name]
  const preview = call?.resultMeta?.canvas_preview
  return body && preview && preview.kind !== "image" ? body : null
}
