# browser/session

`BrowserSession`: one lazily launched browser per agent, shared by the
browser tools and the agent's sub-agents. Import it as
`from kohakuterrarium.browser.session import BrowserSession`.

States: `new` → `launching` → `ready`; `ready` → `new` on idle close, crash or
`close_browser` (the next call relaunches and says state was lost); any →
`closed`, which is terminal.

## Layout

```
session/
├── __init__.py     BrowserSession, BrowserResult
├── assembly.py     class BrowserSession(<mixins>) + __init__ ordering
├── types.py        states, limits, BrowserResult, Stack, Op
├── sink.py         LaunchSink: driver events of the current launch
├── lifecycle.py    SessionLifecycleMixin   state, lock, launch, teardown, idle, close,
│                                           bounded / dialog-aware call runners
├── observing.py    SessionObserveMixin     observation, ref checks, result assembly
├── navigation.py   SessionNavigationMixin  navigate, snapshot
├── acting.py       SessionActMixin         act, ActContext, the post-batch delta
├── capture.py      SessionCaptureMixin     screenshot, evaluate
└── tab_ops.py      SessionTabsMixin        tab list / new / select / close, close_browser
```

## What a result may contain

Text outside a `page_content` wrapper is fixed wording, tab ids, refs,
numbers and URLs (redacted, then flattened and capped). Titles, element
names, dialog messages, file names, script results and driver error text go
inside the wrapper; raised errors keep them in `detail`. Failures the page
causes (stale ref, pending dialog, failed navigation) are `is_error`
results; a closed session, a failed launch, invalid arguments and URLs the
network policy refuses raise. A navigation is blocked only when the
session's proxy recorded a block during it for a host on its redirect chain.

## `__init__` order

`_init_observe` (secrets, token, per-tab observations) → `_init_lifecycle`
(driver, lock, books). Parts follow the pseudo-class rules of
`session/store/README.md`: each part owns the state its `_init_*` creates and
declares the attributes it reads from other parts.
