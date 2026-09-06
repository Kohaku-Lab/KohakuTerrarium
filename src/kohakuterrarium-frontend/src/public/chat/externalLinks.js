/** Whether `href` points at an http(s) resource outside `origin`. */
export function isExternalUrl(href, origin = null) {
  if (!href) return false
  let url
  try {
    url = new URL(href, origin || "http://localhost/")
  } catch {
    return false
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return false
  if (!origin && !/^(?:https?:)?\/\//i.test(href)) return false
  return origin === null || url.origin !== origin
}

/** Teach a markdown-it instance to render external links as `_blank`. */
export function applyExternalLinkRule(md, origin = null) {
  const fallback =
    md.renderer.rules.link_open ||
    ((tokens, idx, options, env, self) => self.renderToken(tokens, idx, options, env, self))
  md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
    const token = tokens[idx]
    if (isExternalUrl(token.attrGet("href"), origin)) {
      token.attrSet("target", "_blank")
      token.attrSet("rel", "noopener noreferrer")
    }
    return fallback(tokens, idx, options, env, self)
  }
  return md
}
