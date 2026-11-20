import { movedBeyondClick } from "./interaction.js";

export function bindGestures(
  canvas,
  player,
  interaction,
  behavior,
  { mini, closeMenu, isMenuOpen, movePet = () => {} },
) {
  let drag;
  const listeners = [];
  const listen = (name, callback) => {
    canvas.addEventListener(name, callback);
    listeners.push([name, callback]);
  };
  listen("pointerdown", (event) => {
    if (player.dead || event.button !== 0) return;
    const dismissing = isMenuOpen();
    closeMenu();
    canvas.setPointerCapture(event.pointerId);
    drag = {
      id: event.pointerId,
      screenX: event.screenX,
      screenY: event.screenY,
      x: event.clientX,
      y: event.clientY,
      at: performance.now(),
      pan: { ...player.pan },
      moved: false,
      region: player.hitRegion(event.clientX, event.clientY),
      woke: interaction.activity(),
      dismissing,
    };
  });
  listen("pointermove", (event) => {
    if (player.dead) return;
    const woke = interaction.activity();
    const rect = canvas.getBoundingClientRect();
    player.gaze = {
      x: Math.max(
        -1,
        Math.min(1, ((event.clientX - rect.left) / rect.width) * 2 - 1),
      ),
      y: Math.max(
        -1,
        Math.min(1, 1 - ((event.clientY - rect.top) / rect.height) * 2),
      ),
    };
    if (!drag || drag.id !== event.pointerId) return;
    drag.woke ||= woke;
    drag.moved ||= movedBeyondClick(drag, {
      x: event.clientX,
      y: event.clientY,
    });
    if (mini && drag.moved && !drag.dismissing) {
      if (!drag.movingPet) {
        movePet({ phase: "start", dx: 0, dy: 0 });
        drag.movingPet = true;
      }
      movePet({
        phase: "move",
        dx: event.screenX - drag.screenX,
        dy: event.screenY - drag.screenY,
      });
    }
    if (!mini && !drag.region && drag.moved && !drag.dismissing)
      player.pan = {
        x: drag.pan.x + ((event.clientX - drag.x) / rect.width) * 2,
        y: drag.pan.y - ((event.clientY - drag.y) / rect.height) * 2,
      };
    if (
      !mini &&
      drag.region === "head" &&
      drag.moved &&
      !drag.woke &&
      !drag.dismissing &&
      !player.paused &&
      player.hitRegion(event.clientX, event.clientY) === "head"
    )
      behavior.touch("head");
  });
  listen("pointerup", (event) => {
    if (!drag || drag.id !== event.pointerId) return;
    if (drag.movingPet) movePet({ phase: "end", dx: 0, dy: 0 });
    const previous = drag;
    drag = null;
    if (
      player.dead ||
      player.paused ||
      previous.woke ||
      previous.dismissing ||
      previous.moved ||
      movedBeyondClick(previous, { x: event.clientX, y: event.clientY }) ||
      performance.now() - previous.at > 450
    )
      return;
    if (
      !previous.region ||
      previous.region !== player.hitRegion(event.clientX, event.clientY)
    )
      return;
    if (player.model.currentMotion === "Sleep") interaction.motion("Idle");
    else behavior.touch(previous.region);
  });
  for (const name of ["pointercancel", "lostpointercapture"])
    listen(name, () => {
      if (drag?.movingPet) movePet({ phase: "end", dx: 0, dy: 0 });
      drag = null;
    });
  listen("pointerleave", () => {
    player.gaze = { x: 0, y: 0 };
  });
  return () => {
    for (const [name, callback] of listeners)
      canvas.removeEventListener(name, callback);
  };
}
