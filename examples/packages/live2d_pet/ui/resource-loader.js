import { randomId } from "./id.js";
// The trusted host fetches private resources using its existing credentials.
// The renderer receives bytes only, bound to its exact navigation channel.
export function importedResources(base, channel, origin, host = window.parent) {
  const pending = new Map();
  function receive(event) {
    const data = event.data;
    if (
      event.source !== host ||
      event.origin !== origin ||
      data?.type !== "kt-live2d-resource-result" ||
      data.channel !== channel
    )
      return;
    const entry = pending.get(data.id);
    if (!entry) return;
    if (data.error) entry.finish(new Error(data.error));
    else if (data.bytes instanceof ArrayBuffer) entry.finish(null, data.bytes);
  }
  window.addEventListener("message", receive);
  return {
    bytes(url, signal) {
      const target = new URL(url);
      if (!target.href.startsWith(base) || target.search || target.hash)
        return Promise.reject(new Error("资源路径不合法。"));
      return new Promise((resolve, reject) => {
        const id = randomId();
        const abort = () => finish(new Error("资源加载已取消。"));
        const timer = setTimeout(
          () => finish(new Error("资源读取超时，请重试。")),
          30000,
        );
        function finish(error, bytes) {
          clearTimeout(timer);
          signal?.removeEventListener("abort", abort);
          pending.delete(id);
          if (error) reject(error);
          else resolve(bytes);
        }
        pending.set(id, { finish });
        if (signal?.aborted) {
          abort();
          return;
        }
        signal?.addEventListener("abort", abort, { once: true });
        host.postMessage(
          {
            type: "kt-live2d-resource",
            channel,
            id,
            path: decodeURIComponent(target.href.slice(base.length)),
          },
          origin,
        );
      });
    },
    dispose() {
      window.removeEventListener("message", receive);
      for (const { finish } of pending.values())
        finish(new Error("资源加载已取消。"));
    },
  };
}

export async function serveResource(kt, event, context) {
  const { channel, model, version, signal } = context;
  const data = event.data;
  if (
    data?.channel !== channel ||
    !version ||
    typeof data.id !== "string" ||
    data.id.length > 64 ||
    typeof data.path !== "string" ||
    data.path.length > 512
  )
    return;
  const reply = { type: "kt-live2d-resource-result", channel, id: data.id };
  try {
    if (
      /[:\\?#%]/.test(data.path) ||
      data.path.split("/").some((p) => !p || p === "." || p === "..")
    )
      throw new Error("资源路径不合法。");
    const path = data.path.split("/").map(encodeURIComponent).join("/");
    const { data: bytes } = await kt.api.get(
      `/models/${model}/files/${version}/${path}`,
      { responseType: "arraybuffer", signal },
    );
    if (signal.aborted) return;
    event.source.postMessage({ ...reply, bytes }, event.origin, [bytes]);
  } catch {
    if (!signal.aborted)
      event.source.postMessage(
        { ...reply, error: "模型资源读取失败，请重新导入或重试。" },
        event.origin,
      );
  }
}
