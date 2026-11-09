/** Pointer intent for one fixed sprite cell; host movement starts only after intent. */
export class PetGestures {
  constructor(player, refresh, dragStart, dragMove, native = () => false) {
    this.native = native;
    this.player = player;
    this.refresh = refresh;
    this.dragStart = dragStart;
    this.dragMove = dragMove;
    this.pointer = null;
    this.move = (event) => this.onMove(event);
    this.up = (event) => this.finish(event, false);
    this.cancel = (event) => this.finish(event, true);
    this.blur = () => this.finish(null, true);
  }
  enter(event) {
    if (event.pointerType === "mouse" && !this.pointer) {
      this.player().cue("jumping", performance.now());
      this.refresh();
    }
  }
  down(event) {
    if (this.pointer || event.button !== 0 || event.isPrimary === false) return;
    event.preventDefault();
    this.pointer = {
      id: event.pointerId,
      x: this.native() ? 0 : event.clientX,
      y: this.native() ? 0 : event.clientY,
      screenX: event.screenX,
      screenY: event.screenY,
      target: event.currentTarget,
      extremeX: this.native() ? 0 : event.clientX,
      direction: 0,
      dragging: false,
    };
    event.currentTarget?.setPointerCapture?.(event.pointerId);
    event.currentTarget?.addEventListener?.("lostpointercapture", this.cancel);
    this.player().press(performance.now());
    window.addEventListener("pointermove", this.move);
    window.addEventListener("pointerup", this.up);
    window.addEventListener("pointercancel", this.cancel);
    window.addEventListener("blur", this.blur);
    this.refresh();
  }
  nativeMove(point) {
    if (!this.pointer || !this.native()) return;
    this.onMove({
      pointerId: this.pointer.id,
      buttons: 1,
      native: true,
      clientX: point.dragX,
      clientY: point.dragY,
    });
  }
  onMove(event) {
    const p = this.pointer;
    if (!p || event.pointerId !== p.id) return;
    if (event.buttons === 0) {
      this.finish(event, true);
      return;
    }
    if (this.native() && !event.native) return;
    if (![event.clientX, event.clientY].every(Number.isFinite)) return;
    const now = performance.now();
    if (
      !p.dragging &&
      Math.hypot(event.clientX - p.x, event.clientY - p.y) >= 6
    ) {
      p.dragging = true;
      this.player().beginDrag(now);
      this.dragStart(event);
    }
    if (p.dragging)
      this.dragMove({ x: event.clientX - p.x, y: event.clientY - p.y });
    if (p.dragging) {
      const delta = event.clientX - p.extremeX;
      if (p.direction * delta > 0) p.extremeX = event.clientX;
      else if (Math.abs(delta) >= 6) {
        p.direction = Math.sign(delta);
        p.extremeX = event.clientX;
        this.player().direction(
          p.direction > 0 ? "running-right" : "running-left",
          now,
        );
      }
    }
    this.refresh();
  }
  finish(event, cancelled) {
    const p = this.pointer;
    if (!p || (event && event.pointerId !== p.id)) return;
    this.pointer = null;
    p.target?.removeEventListener?.("lostpointercapture", this.cancel);
    if (p.target?.hasPointerCapture?.(p.id))
      p.target.releasePointerCapture(p.id);
    window.removeEventListener("pointermove", this.move);
    window.removeEventListener("pointerup", this.up);
    window.removeEventListener("pointercancel", this.cancel);
    window.removeEventListener("blur", this.blur);
    const rect = p.target?.getBoundingClientRect?.();
    const inside =
      rect &&
      event &&
      event.clientX >= rect.left &&
      event.clientX < rect.left + rect.width &&
      event.clientY >= rect.top &&
      event.clientY < rect.top + rect.height;
    const click =
      !cancelled &&
      !p.dragging &&
      inside &&
      (this.native()
        ? Math.hypot(event.screenX - p.screenX, event.screenY - p.screenY)
        : Math.hypot(event.clientX - p.x, event.clientY - p.y)) < 6;
    this.player().release(performance.now(), click);
    this.refresh();
  }
  dispose() {
    this.finish(null, true);
  }
}
