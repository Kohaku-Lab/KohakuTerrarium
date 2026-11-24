---
name: browser_tabs
description: 'List, open, select or close browser tabs, or close the browser. Use it to manage pages a site opened or to start over with a fresh browser.'
category: builtin
tags: [browser, web]
---

# browser_tabs

Lists and changes the browser's tabs. Every result lists the open tabs with
their ids, the active one marked `*`.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| action | string | no | `list` (default), `new`, `select`, `close` or `close_browser` |
| tab | string | no | Tab id for `select` and `close` |
| url | string | no | URL to load in a `new` tab |

## Behavior

- `new` opens a tab and makes it active; with `url` it loads the page and
  returns it like `browser_navigate`.
- `close` without `tab` closes the active tab.
- `close_browser` ends the browser now; the next browser call starts a fresh
  one with no tabs, cookies or logins.
- `list` never starts the browser.

## Limits

- At most 20 tabs; tabs a page opens beyond that are closed and reported.

## Reference

Tab titles are page text and appear inside a `page_content` element. A tab a
click opened becomes the active tab, and the action result names it.
