/** Monaco language id for a file path, by extension (plaintext when unknown). */

const BY_EXT = {
  py: "python",
  js: "javascript",
  mjs: "javascript",
  cjs: "javascript",
  jsx: "javascript",
  ts: "typescript",
  tsx: "typescript",
  vue: "html",
  html: "html",
  css: "css",
  scss: "scss",
  md: "markdown",
  markdown: "markdown",
  json: "json",
  yaml: "yaml",
  yml: "yaml",
  toml: "ini",
  sh: "shell",
  bash: "shell",
  rs: "rust",
  go: "go",
  java: "java",
  c: "c",
  h: "c",
  cpp: "cpp",
  rb: "ruby",
  xml: "xml",
  sql: "sql",
}

export function languageOf(path) {
  const name = String(path || "")
    .replace(/\\/g, "/")
    .split("/")
    .pop()
    .toLowerCase()
  if (name === "dockerfile") return "dockerfile"
  const dot = name.lastIndexOf(".")
  return dot > 0 ? BY_EXT[name.slice(dot + 1)] || "plaintext" : "plaintext"
}

/** File name of a path (either separator). */
export function baseName(path) {
  return (
    String(path || "")
      .replace(/\\/g, "/")
      .split("/")
      .pop() || String(path || "")
  )
}
