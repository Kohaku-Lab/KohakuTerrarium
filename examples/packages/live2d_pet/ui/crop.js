// Rectangular app-overlay cropping. No texture reads or GPU readbacks.
export function modelRectangle(player) {
  if (!player?.bounds || player.dead) return null;
  const rect = player.canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;
  const b = player.bounds;
  const visible = {
    left: Infinity,
    right: -Infinity,
    bottom: Infinity,
    top: -Infinity,
  };
  const model = player.model.getModel();
  for (let i = 0; i < model.getDrawableCount(); i++) {
    if (model.getDrawableOpacity(i) < 0.05) continue;
    const vertices = model.getDrawableVertices(i);
    for (let j = 0; j < vertices.length; j += 2) {
      visible.left = Math.min(visible.left, vertices[j]);
      visible.right = Math.max(visible.right, vertices[j]);
      visible.bottom = Math.min(visible.bottom, vertices[j + 1]);
      visible.top = Math.max(visible.top, vertices[j + 1]);
    }
  }
  const aspect = player.canvas.width / player.canvas.height;
  const scale =
    Math.min(1.8 / (b.top - b.bottom), (1.8 * aspect) / (b.right - b.left)) *
    player.zoom;
  const x = (value) =>
    rect.left +
    ((((value - (b.left + b.right) / 2) * scale) / aspect + player.pan.x + 1) /
      2) *
      rect.width;
  const y = (value) =>
    rect.top +
    ((1 - (value - (b.top + b.bottom) / 2) * scale - player.pan.y) / 2) *
      rect.height;
  const project = (value) => ({
    left: x(value.left) - 8,
    right: x(value.right) + 8,
    top: y(value.top) - 8,
    bottom: y(value.bottom) + 8,
  });
  const current = Number.isFinite(visible.left) ? project(visible) : null;
  return current ? { ...current, visible: current } : null;
}

export function normalizedRect(rect, width, height) {
  if (!rect || !(width > 0 && height > 0)) return null;
  const { left, right, top, bottom } = rect;
  if (![left, right, top, bottom].every(Number.isFinite)) return null;
  const x = Math.max(0, Math.floor(left)) / width;
  const y = Math.max(0, Math.floor(top)) / height;
  const endX = Math.min(width, Math.ceil(right)) / width;
  const endY = Math.min(height, Math.ceil(bottom)) / height;
  return endX > x && endY > y
    ? { x, y, width: endX - x, height: endY - y }
    : null;
}

export function validRegions(regions) {
  return (
    Array.isArray(regions) &&
    regions.length <= 8 &&
    regions.every(
      (r) =>
        r &&
        [r.x, r.y, r.width, r.height].every(Number.isFinite) &&
        r.x >= 0 &&
        r.y >= 0 &&
        r.width > 0 &&
        r.height > 0 &&
        r.x + r.width <= 1.000001 &&
        r.y + r.height <= 1.000001,
    )
  );
}

export function regionsClip(regions) {
  if (!regions.length) return "inset(100%)";
  const point = (x, y) => `${x * 100}% ${y * 100}%`;
  const points = [point(0, 0)];
  for (const { x, y, width, height } of regions) {
    points.push(
      point(x, y),
      point(x + width, y),
      point(x + width, y + height),
      point(x, y + height),
      point(x, y),
      point(0, 0),
    );
  }
  return `polygon(nonzero, ${points.join(", ")})`;
}

export function cropInterval(player) {
  const actions = player?.model?.workActions;
  return actions?.transitioning ||
    player?.model?.actionPose?.transitioning ||
    actions?.state === "running" ||
    actions?.idleStyle === "wings"
    ? 0
    : 200;
}

export class CropHistory {
  constructor() {
    this.samples = [];
  }
  update(rect, now) {
    this.samples = this.samples.filter((s) => now - s.time < 200).slice(-7);
    if (!rect) {
      this.samples = [];
      return null;
    }
    this.samples.push({ ...rect, time: now });
    return {
      left: Math.min(...this.samples.map((s) => s.left)),
      top: Math.min(...this.samples.map((s) => s.top)),
      right: Math.max(...this.samples.map((s) => s.right)),
      bottom: Math.max(...this.samples.map((s) => s.bottom)),
    };
  }
}

export function connectCrop(getPlayer) {
  const channel = new URL(location.href).searchParams.get("channel");
  const origin = document.referrer ? new URL(document.referrer).origin : null;
  if (!channel || !origin || parent === window)
    return { update() {}, dispose() {} };
  const history = new CropHistory();
  let last = -Infinity,
    previous = "",
    sequence = 0,
    cachedModel = null;
  function update(force = false, uiOnly = false) {
    const now = performance.now();
    if (document.hidden) return;
    const player = getPlayer();
    if (!uiOnly) {
      if (!force && now - last < cropInterval(player)) return;
      last = now;
      cachedModel = modelRectangle(player);
    }
    const model = cachedModel;
    const visual = model?.visible;
    let headerOffset = 0;
    if (visual && document.body.classList.contains("debug")) {
      // Quantize to avoid chrome vibrating with small idle/physics changes.
      const top = Math.max(0, Math.floor((visual.top - 28) / 8) * 8);
      const bottom = Math.min(
        innerHeight - 44,
        Math.ceil(visual.bottom / 8) * 8,
      );
      headerOffset = top;
      for (const [id, value] of [
        ["work-status", top],
        ["pet-menu-button", top],
        ["mini-hint", bottom],
        ["mini-metrics", bottom + 12],
      ]) {
        const element = document.getElementById(id);
        if (element && element.style.top !== `${value}px`) {
          element.style.top = `${value}px`;
          element.style.bottom = "auto";
        }
      }
    }
    const rectangles = [history.update(model, now)];
    for (const id of [
      "pet-menu-button",
      "pet-menu",
      "work-status",
      "mini-hint",
      "mini-metrics",
      "status",
    ]) {
      const element = document.getElementById(id);
      if (element && element.getClientRects().length)
        rectangles.push(element.getBoundingClientRect());
    }
    const regions = rectangles
      .map((rect) => normalizedRect(rect, innerWidth, innerHeight))
      .filter(Boolean);
    const key = JSON.stringify([regions, headerOffset]);
    if (key === previous) return;
    previous = key;
    parent.postMessage(
      {
        type: "kt-live2d-crop",
        channel,
        sequence: ++sequence,
        regions,
        headerOffset,
      },
      origin,
    );
  }
  const refresh = () => {
    history.samples = [];
    update(true);
  };
  let uiRefresh = null;
  const observer = new MutationObserver(() => {
    if (uiRefresh !== null) return;
    uiRefresh = requestAnimationFrame(() => {
      uiRefresh = null;
      update(false, true);
    });
  });
  for (const id of ["pet-menu", "work-status", "mini-hint", "status"]) {
    const element = document.getElementById(id);
    if (element)
      observer.observe(element, {
        attributes: true,
        childList: true,
        subtree: true,
        characterData: true,
      });
  }
  observer.observe(document.body, {
    attributes: true,
    attributeFilter: ["class"],
  });
  window.addEventListener("resize", refresh);
  update(true);
  return {
    update,
    dispose() {
      observer.disconnect();
      if (uiRefresh !== null) cancelAnimationFrame(uiRefresh);
      window.removeEventListener("resize", refresh);
    },
  };
}
