---
name: web_search
description: Search the web and return results with titles, URLs, and snippets
category: builtin
tags: [web, search, network]
---

# web_search

Search the web through the creature's configured backend. DuckDuckGo is the
default; DeepSeek Responses web search is an explicit opt-in.

## Arguments

| Arg         | Type    | Description                           |
| ----------- | ------- | ------------------------------------- |
| query       | string  | Search query (required)               |
| max_results | integer | Max results to return (default: 10)   |
| region      | string  | Region code (optional, e.g., "us-en") |

## Behavior

- DuckDuckGo needs no API key. DeepSeek requires a configured `deepseek` key.
- DuckDuckGo returns result rows; DeepSeek returns a grounded synthesis plus
  provider-supplied sources when available.
- If the search backend is not available, returns an error. Tell the user.

The operator can switch the current creature at runtime:

```text
kt config key set deepseek
/module set web_search backend deepseek
```

`/module reset web_search` restores the creature configuration baseline.

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
- Results depend on the selected backend's index and ranking.

## TIPS

- Use `web_search` to find URLs, then `web_fetch` to read the full content.
- Be specific in your queries for better results.
- Use `max_results` to limit output when you only need a few results.
