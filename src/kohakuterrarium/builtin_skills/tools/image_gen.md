# image_gen

Produce or edit a raster image as part of your next response.

## How it works (for you)

This is **not a regular function tool** — do not emit a tool call
for it. When the user asks for an image (draw, sketch, picture,
render, edit this image, make a variation, …), simply proceed as
if you are about to create it. A dedicated image backend detects
the intent in context and returns a PNG that appears inline in the
assistant message alongside any text you write.

## When to use

- User asks for a new illustration, photo, sticker, diagram,
  mock-up, cover, icon, or any raster asset.
- User attaches an image and asks for an edit: object replacement,
  background change, style transfer, recolouring, re-composition,
  cleanup, re-crop, etc. For edits the target image must already
  be in the conversation (attached by the user earlier, or surfaced
  via `view_image`-style tools if available).
- User asks for several variants: make one image per variant —
  one "intent" per turn produces one image.

## When NOT to use

- The user wants code, SVG, HTML/CSS, or vector output — write
  those directly.
- The user wants you to explain an image, not modify it — answer
  in text.
- The task is not visual (summarisation, planning, coding).

## Prompting guidance

- Describe the image you intend to produce in one or two concise
  sentences _before_ or _after_ the image: scene / subject /
  style / composition / palette / constraints. This helps the
  backend and doubles as a caption for the user.
- For edits, state the invariants explicitly: _"change only the
  background; keep the subject, pose, and framing unchanged"_.
- Quote any text that must appear in the image verbatim, in
  quotes, and specify placement.
- If the user's request is vague, ask one clarifying question
  first; don't silently invent details.

## Typical shape of a turn

```
User: "Draw a red panda drinking boba tea, sticker style."

You: A sticker-style red panda grinning while sipping a pastel
boba tea, soft pastel palette, bold clean outlines, flat colours,
centred on a plain background.

[image appears inline]
```

No `##tool##` / function-call syntax needed.
