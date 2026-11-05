const MARGIN = 6;

export function intersectViewport(rect, width, height) {
  if (!Array.isArray(rect) || rect.length !== 4 || !rect.every(Number.isFinite))
    return [0, 0, width, height];
  const left = Math.max(0, Math.min(width, rect[0]));
  const top = Math.max(0, Math.min(height, rect[1]));
  return [
    left,
    top,
    Math.max(0, Math.min(width, rect[0] + rect[2]) - left),
    Math.max(0, Math.min(height, rect[1] + rect[3]) - top),
  ];
}

export function placeMenu(menu, x, y, viewport) {
  const [left, top, width, height] = viewport;
  const margin = Math.min(MARGIN, width / 2, height / 2);
  menu.style.width = `${Math.min(300, Math.max(0, width - margin * 2))}px`;
  menu.style.maxHeight = `${Math.max(0, height - margin * 2)}px`;
  menu.style.left = `${Math.max(left + margin, Math.min(x, left + width - menu.offsetWidth - margin))}px`;
  menu.style.top = `${Math.max(top + margin, Math.min(y, top + height - menu.offsetHeight - margin))}px`;
}

// The native overlay can extend beyond a monitor even though the iframe does not.
export function connectMenuPosition(menu) {
  const params = new URL(location.href).searchParams;
  const channel = params.has("mini") ? params.get("channel") : null;
  const origin = document.referrer ? new URL(document.referrer).origin : null;
  let sequence = 0;
  let pending;
  let anchor = [6, 6];
  const cancel = () => {
    sequence++;
    pending?.finish(null);
  };
  function receive(event) {
    const data = event.data;
    if (
      pending &&
      event.source === parent &&
      event.origin === origin &&
      data?.type === "kt-live2d-menu-viewport" &&
      data.channel === channel &&
      data.request === pending.request
    )
      pending.finish(data.viewport);
  }
  function viewport(request) {
    if (!channel || !origin || parent === window) return Promise.resolve(null);
    return new Promise((resolve) => {
      const timer = setTimeout(() => pending?.finish(null), 500);
      pending = {
        request,
        finish(rect) {
          clearTimeout(timer);
          pending = null;
          resolve(rect);
        },
      };
      parent.postMessage(
        { type: "kt-live2d-menu-viewport", channel, request, point: anchor },
        origin,
      );
    });
  }
  async function position(x = anchor[0], y = anchor[1]) {
    cancel();
    if (menu.hidden) return;
    anchor = [x, y];
    const request = sequence;
    const rect = await viewport(request);
    if (request !== sequence || menu.hidden) return;
    placeMenu(menu, x, y, intersectViewport(rect, innerWidth, innerHeight));
    menu.style.visibility = "";
  }
  const resized = () => void position();
  window.addEventListener("message", receive);
  window.addEventListener("resize", resized);
  return {
    position,
    cancel,
    dispose() {
      cancel();
      window.removeEventListener("message", receive);
      window.removeEventListener("resize", resized);
    },
  };
}
