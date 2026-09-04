---
name: video_gen
description: 'Generate a video from a prompt. Not for still images - use image_gen.'
category: builtin
tags: [media]
---

# video_gen

Submits a video generation job and returns the result when it completes.

## Arguments

| Arg | Type | Req | Description |
| --- | --- | --- | --- |
| prompt | string | yes | What to produce |
| duration | number | no | Seconds, where the provider supports it |

## Behavior

- Generation is asynchronous and can take minutes.
- In-flight work is not resumed after a restart.
