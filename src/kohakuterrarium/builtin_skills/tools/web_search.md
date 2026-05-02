---
name: web_search
description: Search the web and return results with titles, URLs, and snippets
category: builtin
tags: [web, search, network]
---

# web_search

Search the web and return structured results with titles, URLs, and snippets.

## Arguments

| Arg         | Type    | Description                           |
| ----------- | ------- | ------------------------------------- |
| query       | string  | Search query (required)               |
| max_results | integer | Max results to return (default: 10)   |
| region      | string  | Region code (optional, e.g., "us-en") |

## Behavior

- No API key needed.
- Results include title, URL, and snippet for each match.
- If the search backend is not available, returns an error. Tell the user.

## WHEN TO USE

- Finding documentation or tutorials
- Looking up error messages or solutions
- Researching libraries or tools
- Finding relevant web pages before fetching them with `web_fetch`

## Output

Structured list with numbered results:

```
Search results for: python asyncio tutorial

## 1. Python asyncio Tutorial
URL: https://docs.python.org/3/library/asyncio.html
Official documentation for the asyncio module...

## 2. ...
```

## LIMITATIONS

- Search may not be available in all configurations. If you get an error, tell the user.
- May be rate-limited under heavy usage.
- Results depend on the search engine's index and ranking.

## TIPS

- Use `web_search` to find URLs, then `web_fetch` to read the full content.
- Be specific in your queries for better results.
- Use `max_results` to limit output when you only need a few results.
