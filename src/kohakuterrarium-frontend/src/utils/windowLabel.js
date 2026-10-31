const LABEL_BODY_MAX = 48
const HASH_LENGTH = 8

function hash(text) {
  let h = 2166136261
  for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 16777619)
  return (h >>> 0).toString(36).padStart(HASH_LENGTH, "0").slice(0, HASH_LENGTH)
}

/** Native window label for ``key``: ``prefix``, a readable slug and a hash, within the shell's length limit. */
export function makeWindowLabel(prefix, key) {
  const tag = hash(String(key))
  const slug = String(key)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, LABEL_BODY_MAX - tag.length - 1)
  return `${prefix}${slug ? `${slug}-` : ""}${tag}`
}
