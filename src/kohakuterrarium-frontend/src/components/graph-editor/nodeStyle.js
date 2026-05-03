// Layout + style constants shared by the runtime canvas components.
// Pulled out of the original PoC mock-data module so the runtime
// snapshot path doesn't import anything named "mock" (it only ever
// used these constants — never the seed data).

export const NODE_WIDTH = 148
export const NODE_HEIGHT = 64
export const GROUP_PADDING_TOP = 56
export const GROUP_PADDING_X = 36
export const GROUP_PADDING_BOTTOM = 36

export const STATUS_GLYPH = {
  running: "⚙",
  waiting: "◐",
  done: "✓",
  error: "✕",
  idle: "○",
}

export const STATUS_COLOR = {
  running: "aquamarine",
  waiting: "amber",
  done: "sage",
  error: "coral",
  idle: "warm-400",
}

export const KIND_ACCENT = {
  creature: "iolite",
  session: "taaffeite",
  terrarium: "amber",
  channel: "aquamarine",
}
