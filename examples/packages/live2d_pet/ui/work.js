import { WORK_LABELS } from "./behavior.js";

export function connectWork(
  behavior,
  interaction,
  model = "dragon",
  options = {},
) {
  const badge = document.querySelector("#work-status");
  const targetLabel = document.querySelector("#pet-target");
  const openTarget = document.querySelector("#pet-open-target");
  const channel = new URL(location.href).searchParams.get("channel");
  const origin = document.referrer ? new URL(document.referrer).origin : null;
  let sequence = 0;
  let debug = options.debug === true;
  let followWork = options.followWork !== false;
  let latest = null;
  let previewing = false;
  let started = 0;
  let completed = 0;
  const speech = options.speech;
  speech?.configure(options.talkingMouth !== false);
  const display = (work) => {
    behavior.setWork(work);
    if (behavior.working) interaction.activity();
    const text = !work
      ? "未绑定会话 · 先打开一个聊天"
      : `${work.label} · ${work.connected ? WORK_LABELS[behavior.status] : "连接中，状态暂不可用"}`;
    const state = work?.connected ? behavior.status : "idle";
    if (badge.textContent !== text) badge.textContent = text;
    if (badge.dataset.status !== state) badge.dataset.status = state;
  };
  const receive = (event) => {
    const data = event.data;
    if (
      !channel ||
      event.source !== parent ||
      event.origin !== origin ||
      data?.type !== "kt-live2d-work" ||
      data.channel !== channel ||
      !Number.isInteger(data.sequence) ||
      data.sequence <= sequence
    )
      return;
    sequence = data.sequence;
    if (targetLabel)
      targetLabel.textContent =
        typeof data.target?.text === "string"
          ? data.target.text
          : "尚未选择会话";
    if (openTarget) openTarget.hidden = !data.target?.target;
    latest = data.work;
    speech?.observe(latest, options.onSpeech || (() => interaction.activity()));
    behavior.waitingForInput = Boolean(
      latest?.connected && latest.status === "waiting",
    );
    if (!previewing) display(followWork ? latest : null);
  };
  window.addEventListener("message", receive);
  const open = () =>
    parent.postMessage({ type: "kt-live2d-open-target", channel }, origin);
  openTarget?.addEventListener("click", open);
  if (channel && origin && parent !== window)
    parent.postMessage(
      {
        type: "kt-live2d-ready",
        channel,
        model,
        ...(Number.isFinite(options.aspect) ? { aspect: options.aspect } : {}),
      },
      origin,
    );
  const dispose = () => {
    window.removeEventListener("message", receive);
    openTarget?.removeEventListener("click", open);
    speech?.reset();
  };
  dispose.configure = (options) => {
    speech?.configure(options.talkingMouth !== false);
    const nextFollow = options.followWork !== false;
    const stopPreview = previewing && !options.debug;
    debug = options.debug === true;
    if (nextFollow !== followWork || stopPreview) {
      followWork = nextFollow;
      previewing = false;
      behavior.setWork(null);
      display(followWork ? latest : null);
    }
  };
  dispose.preview = (state) => {
    if (state === "live") {
      previewing = false;
      behavior.setWork(null);
      display(followWork ? latest : null);
      return;
    }
    if (!debug || !["idle", "running", "waiting", "ready"].includes(state))
      return;
    if (!previewing) behavior.setWork(null);
    previewing = true;
    const work = {
      hostId: "preview",
      sessionId: "preview",
      label: "动作试演",
      connected: true,
      started: ++started,
      completed,
      busy: state === "running",
      status: state,
    };
    if (state === "ready") {
      if (!behavior.work) display({ ...work, status: "idle" });
      work.completed = ++completed;
    }
    display(work);
  };
  return dispose;
}
