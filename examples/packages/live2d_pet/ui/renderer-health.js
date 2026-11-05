export function connectRendererHealth(getPlayer) {
  const channel = new URL(location.href).searchParams.get("channel");
  const origin = document.referrer ? new URL(document.referrer).origin : null;
  let failed = false;
  const send = (error = "") => {
    if (channel && origin && parent !== window)
      parent.postMessage({ type: "kt-live2d-health", channel, error }, origin);
  };
  const fail = (error) => {
    if (failed) return;
    failed = true;
    clearInterval(timer);
    send(error || "渲染器已停止，请重试。");
  };
  const timer = setInterval(() => {
    const player = getPlayer();
    if (player?.dead) fail();
    else if (player?.model && !failed) send();
  }, 3000);
  return { fail, dispose: () => clearInterval(timer) };
}
