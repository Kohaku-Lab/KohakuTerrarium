---
name: web_fetch
description: Read a web page and return its content in clean markdown format
category: builtin
tags: [web, network, fetch]
---

# web_fetch

Fetch a web page and return its content in clean, readable markdown format.

## Arguments

| Arg | Type   | Description             |
| --- | ------ | ----------------------- |
| url | string | URL to fetch (required) |

## Behavior

- Multiple extraction backends are tried automatically in order of quality.
- Falls back gracefully if the best backend is unavailable.
- URLs without a scheme are auto-prefixed with `https://`.
- Output is truncated to 100,000 characters if the page is very large.
- Timeout is 30 seconds per backend attempt.
- If one backend fails, the next is tried automatically.

## WHEN TO USE

- Reading documentation pages
- Fetching API docs or tutorials
- Checking web page content
- Extracting text from web articles

## Output

Returns the page content as clean markdown text.

## LIMITATIONS

- Content cap: 100,000 characters (truncated with notice if exceeded)
- 30-second timeout per backend
- JS-heavy or single-page-app sites may not render correctly
- Some sites may block automated access

## TIPS

- Use `web_search` first to find URLs, then `web_fetch` to read them.
- If a page returns empty or garbled content, the site may require JS rendering
  that is not available. Try a different URL or source.
