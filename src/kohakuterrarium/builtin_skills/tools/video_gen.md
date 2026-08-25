# video_gen

Generate a video through xAI's asynchronous Videos API.

- `model` is a video model such as `grok-imagine-video-1.5`.
- Text-to-video needs `prompt`; image-to-video also accepts `input_image`.
- `input_image` accepts a public URL, readable local path, or KT artifact URL.
  Use `latest` for the most recent image attached by the user. Do not construct
  a data URI with shell or Python; the tool handles image encoding internally.
- Local and attached images are encoded inside the tool and never printed into
  the conversation. KT rejects inputs larger than 20 MB before submission.
- `duration` is 1–15 seconds; `resolution` is `480p`, `720p`, or `1080p`.
- The tool runs in the background, polls the request, and stores the MP4 locally.
- Local polling stops after 10 minutes; xAI keeps ownership of any already-submitted job.
- Cancellation stops local polling. In-flight work is not resumed after KT restarts.
