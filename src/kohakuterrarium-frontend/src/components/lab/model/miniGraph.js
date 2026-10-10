/**
 * Thumbnail layout of one session's graph in the Flow view's idiom. A
 * session that fits gets the detailed layout: privileged nodes on the top
 * row, channel pills below them, workers in rows under the channels (ordered
 * by the channels they use), creature–channel links as elbows and output
 * wires. A session too big for a readable box each gets the grouped preview:
 * one column per channel with its workers folded into a group of status
 * dots. Fits a `width`×`height` box. Pure.
 */

const PAD = 10
const NODE_H = 20
const CHANNEL_H = 14
const NODE_MAX_W = 92
const NODE_MIN_W = 60
const CHANNEL_MAX_W = 64
const CHANNEL_MIN_W = 40
const NODE_GAP = 8
const CHANNEL_GAP = 10
const SOLO_W = 120
const MIN_TIER_STEP = 30
const COL_MIN_W = 64
const COL_GAP = 8
const PRIV_GROUP_W = 200
const PRIV_GAP = 18
const PILL_GAP = 12
const GROUP_PAD = 6
const GROUP_HEAD = 16
const DOT_D = 5
const DOT_STEP = 8
/** Approximate glyph advance (px) of the 9.5px node label and 9px mono channel label. */
const NODE_CHAR = 5.5
const CHANNEL_CHAR = 5.6
const NODE_LABEL_PAD = 8
const CHANNEL_LABEL_PAD = 6

/** `text` cut to fit `width` at `charW` per glyph ("" when not even 3 glyphs fit). */
export function fitLabel(text, width, charW) {
  const max = Math.floor(width / charW)
  if (max < 3) return ""
  return text.length > max ? `${text.slice(0, max - 1)}…` : text
}

function row(items, y, h, maxW, gap, width) {
  const n = items.length
  if (!n) return []
  const w = Math.min(maxW, (width - 2 * PAD - (n - 1) * gap) / n)
  let x = (width - (n * w + (n - 1) * gap)) / 2
  return items.map((item) => {
    const box = { item, x, y, w, h }
    x += w + gap
    return box
  })
}

function fitsRow(count, minW, gap, width) {
  return count * minW + (count - 1) * gap <= width - 2 * PAD
}

function workerOrder(workers, channelIndex) {
  const mean = (c) => {
    const idx = [...new Set([...c.send, ...c.listen])]
      .filter((n) => channelIndex.has(n))
      .map((n) => channelIndex.get(n))
    return idx.length ? idx.reduce((a, b) => a + b, 0) / idx.length : Infinity
  }
  return workers
    .map((c, i) => ({ c, key: mean(c), i }))
    .sort((a, b) => a.key - b.key || a.i - b.i)
    .map((x) => x.c)
}

function elbow(from, to) {
  const above = from.y < to.y
  const x1 = from.x + from.w / 2
  const y1 = above ? from.y + from.h : from.y
  const x2 = to.x + to.w / 2
  const y2 = above ? to.y : to.y + to.h
  const mid = (y1 + y2) / 2
  return `M${x1},${y1} V${mid} H${x2} V${y2}`
}

function nodeOut(b) {
  return {
    id: b.item.id,
    name: b.item.name,
    status: b.item.status,
    privileged: !!b.item.privileged,
    x: b.x,
    y: b.y,
    w: b.w,
    h: b.h,
    label: fitLabel(b.item.name, b.w - NODE_LABEL_PAD, NODE_CHAR),
  }
}

function pillOut(id, name, b) {
  return {
    id,
    name,
    x: b.x,
    y: b.y,
    w: b.w,
    h: b.h,
    label: fitLabel(name, b.w - CHANNEL_LABEL_PAD, CHANNEL_CHAR),
  }
}

/**
 * A folded group: one status dot per member that fits, `more` for the rest,
 * member `count`, and a box only as tall as its dots need (at most `maxH`).
 */
function groupOut(id, members, x, y, w, maxH) {
  const cols = Math.max(1, Math.floor((w - 2 * GROUP_PAD - DOT_D) / DOT_STEP) + 1)
  const fitRows = Math.max(0, Math.floor((maxH - GROUP_HEAD - GROUP_PAD - DOT_D) / DOT_STEP) + 1)
  const rows = Math.min(fitRows, Math.ceil(members.length / cols))
  const shown = members.slice(0, cols * rows)
  const h = Math.min(maxH, GROUP_HEAD + Math.max(0, rows - 1) * DOT_STEP + DOT_D + GROUP_PAD)
  return {
    id,
    x,
    y,
    w,
    h,
    count: members.length,
    more: members.length - shown.length,
    dots: shown.map((c, i) => ({
      id: c.id,
      name: c.name,
      status: c.status,
      x: x + GROUP_PAD + (i % cols) * DOT_STEP + DOT_D / 2,
      y: y + GROUP_HEAD + Math.floor(i / cols) * DOT_STEP + DOT_D / 2,
    })),
  }
}

function detailFits(privileged, channels, tiers, width, height) {
  if (privileged.length && !fitsRow(privileged.length, NODE_MIN_W, NODE_GAP, width)) return false
  if (channels.length && !fitsRow(channels.length, CHANNEL_MIN_W, CHANNEL_GAP, width)) return false
  return tiers < 2 || (height - 2 * PAD - NODE_H) / (tiers - 1) >= MIN_TIER_STEP
}

/**
 * Columns of the grouped preview: the channels used most (as many as fit),
 * then "+N" for the other channels, then the workers on no channel. Each
 * worker sits in the column of the first channel it uses that has one.
 */
function groupColumns(channels, workers, width) {
  const uses = (name) =>
    workers.filter((c) => c.send.includes(name) || c.listen.includes(name)).length
  const loose = workers.filter(
    (c) =>
      !c.send.some((n) => channels.some((ch) => ch.name === n)) &&
      !c.listen.some((n) => channels.some((ch) => ch.name === n)),
  )
  const fit = Math.max(1, Math.floor((width - 2 * PAD + COL_GAP) / (COL_MIN_W + COL_GAP)))
  const room = Math.max(1, fit - (loose.length ? 1 : 0))
  const keep = channels.length > room ? room - 1 : channels.length
  const kept = new Set(
    [...channels]
      .sort((a, b) => uses(b.name) - uses(a.name))
      .slice(0, keep)
      .map((ch) => ch.name),
  )
  const columns = channels
    .filter((ch) => kept.has(ch.name))
    .map((ch) => ({ key: ch.name, pill: { id: ch.id, name: ch.name }, members: [] }))
  const restCount = channels.length - kept.size
  const rest = restCount
    ? { key: "+rest", pill: { id: "ch:+rest", name: `+${restCount}` }, members: [] }
    : null
  const byName = new Map(columns.map((col) => [col.key, col]))
  const looseCol = loose.length ? { key: "+loose", pill: null, members: [] } : null
  for (const c of workers) {
    if (loose.includes(c)) {
      looseCol.members.push(c)
      continue
    }
    const first = [...c.send, ...c.listen].find((n) => byName.has(n))
    ;(first ? byName.get(first) : rest).members.push(c)
  }
  return [...columns, ...(rest ? [rest] : []), ...(looseCol ? [looseCol] : [])]
}

/** The grouped preview, laid out once to measure its height, then again centred in the box. */
function groupedLayout(privileged, channels, workers, width, height) {
  const first = groupedAt(PAD, privileged, channels, workers, width, height)
  const bottom = Math.max(
    ...[...first.nodes, ...first.channels, ...first.groups].map((b) => b.y + b.h),
  )
  const slack = height - PAD - bottom
  return slack > 1
    ? groupedAt(PAD + slack / 2, privileged, channels, workers, width, height)
    : first
}

function groupedAt(top, privileged, channels, workers, width, height) {
  const nodes = []
  const groups = []
  const pills = []
  const links = []
  const tops = []
  let y = top
  if (privileged.length) {
    if (fitsRow(privileged.length, NODE_MIN_W, NODE_GAP, width)) {
      for (const b of row(privileged, y, NODE_H, NODE_MAX_W, NODE_GAP, width)) {
        nodes.push(nodeOut(b))
        tops.push({ id: b.item.id, box: b })
      }
      y += NODE_H
    } else {
      const w = Math.min(width - 2 * PAD, PRIV_GROUP_W)
      const g = groupOut("group:+privileged", privileged, (width - w) / 2, y, w, NODE_H + 10)
      groups.push({ ...g, privileged: true })
      tops.push({ id: g.id, box: g })
      y += g.h
    }
    y += PRIV_GAP
  }
  const columns = groupColumns(channels, workers, width)
  const colW = (width - 2 * PAD - (columns.length - 1) * COL_GAP) / columns.length
  const boxes = row(columns, y, CHANNEL_H, colW, COL_GAP, width)
  const groupY = y + CHANNEL_H + PILL_GAP
  for (const b of boxes) {
    const col = b.item
    if (col.pill) {
      const pill = pillOut(col.pill.id, col.pill.name, b)
      pills.push(pill)
      for (const top of tops)
        links.push({
          id: `${top.id}->${pill.id}`,
          d: elbow(top.box, b),
          control: true,
          sends: false,
        })
    }
    if (!col.members.length) continue
    groups.push(groupOut(`group:${col.key}`, col.members, b.x, groupY, b.w, height - PAD - groupY))
    if (col.pill) {
      const cx = b.x + b.w / 2
      links.push({
        id: `${col.pill.id}->group`,
        d: `M${cx},${b.y + b.h} V${groupY}`,
        control: false,
        sends: true,
      })
    }
  }
  return { mode: "grouped", nodes, channels: pills, groups, links, wires: [] }
}

function detailedLayout(session, privileged, channels, workerRows, width, height) {
  const creatures = session.creatures || []
  const tiers = (privileged.length ? 1 : 0) + (channels.length ? 1 : 0) + workerRows.length
  const boxes = new Map()
  const channelBoxes = []
  if (creatures.length === 1 && !channels.length) {
    const w = Math.min(SOLO_W, width - 2 * PAD)
    boxes.set(creatures[0].id, {
      item: creatures[0],
      x: (width - w) / 2,
      y: (height - NODE_H) / 2,
      w,
      h: NODE_H,
    })
  } else {
    const step = tiers > 1 ? (height - 2 * PAD - NODE_H) / (tiers - 1) : 0
    let y = tiers > 1 ? PAD : (height - NODE_H) / 2
    if (privileged.length) {
      for (const b of row(privileged, y, NODE_H, NODE_MAX_W, NODE_GAP, width))
        boxes.set(b.item.id, b)
      y += step
    }
    if (channels.length) {
      channelBoxes.push(
        ...row(
          channels,
          y + (NODE_H - CHANNEL_H) / 2,
          CHANNEL_H,
          CHANNEL_MAX_W,
          CHANNEL_GAP,
          width,
        ),
      )
      y += step
    }
    for (const r of workerRows) {
      for (const b of row(r, y, NODE_H, NODE_MAX_W, NODE_GAP, width)) boxes.set(b.item.id, b)
      y += step
    }
  }

  const channelByName = new Map(channelBoxes.map((b) => [b.item.name, b]))
  const links = []
  const wires = []
  for (const e of session.edges || []) {
    if (e.kind === "channel") {
      const from = boxes.get(e.source)
      const to = channelByName.get(e.channelName)
      if (from && to)
        links.push({
          id: e.id,
          d: elbow(from, to),
          control: !!e.control,
          sends: e.mode !== "listen",
        })
    } else if (e.kind === "wire") {
      const a = boxes.get(e.source)
      const b = boxes.get(e.target)
      if (!a || !b) continue
      const y = Math.max(a.y + a.h, b.y + b.h) + 4
      wires.push({
        id: e.id,
        d: `M${a.x + a.w / 2},${a.y + a.h} V${y} H${b.x + b.w / 2} V${b.y + b.h}`,
      })
    }
  }
  return {
    mode: "detailed",
    nodes: [...boxes.values()].map(nodeOut),
    channels: channelBoxes.map((b) => pillOut(b.item.id, b.item.name, b)),
    groups: [],
    links,
    wires,
  }
}

/**
 * Layout of `session` (labSessions entry): `{mode, nodes, channels, groups,
 * links, wires}`, `mode` "detailed" or "grouped". Nodes and channels carry
 * their box and a fitted `label`; groups their box, member `count`, status
 * `dots` and `more`; links an elbow path `d`, `control` (a privileged link)
 * and `sends`.
 */
export function miniGraphLayout(session, width, height) {
  const creatures = session.creatures || []
  const channels = session.channels || []
  const privileged = creatures.filter((c) => c.privileged)
  const channelIndex = new Map(channels.map((ch, i) => [ch.name, i]))
  const workers = workerOrder(
    creatures.filter((c) => !c.privileged),
    channelIndex,
  )
  const perRow = Math.max(
    1,
    Math.min(workers.length, Math.floor((width - 2 * PAD + NODE_GAP) / (NODE_MIN_W + NODE_GAP))),
  )
  const workerRows = []
  for (let i = 0; i < workers.length; i += perRow) workerRows.push(workers.slice(i, i + perRow))
  const tiers = (privileged.length ? 1 : 0) + (channels.length ? 1 : 0) + workerRows.length
  return detailFits(privileged, channels, tiers, width, height)
    ? detailedLayout(session, privileged, channels, workerRows, width, height)
    : groupedLayout(privileged, channels, workers, width, height)
}
