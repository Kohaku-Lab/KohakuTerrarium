/** Distance and angle hysteresis for the sixteen V2 directional poses. */
export class PetGaze {
  constructor() {
    this.reset();
  }
  reset() {
    this.active = false;
    this.entered = null;
    this.left = null;
    this.direction = null;
  }
  tick(now, point) {
    if (
      !point?.valid ||
      point.pointerType !== "mouse" ||
      ![point.dx, point.dy, point.size].every(Number.isFinite) ||
      point.size <= 0
    ) {
      this.reset();
      return null;
    }
    const distance = Math.hypot(point.dx, point.dy);
    if (!this.active) {
      if (distance > point.size) this.entered = null;
      else {
        this.entered ??= now;
        if (now - this.entered >= 80) this.active = true;
      }
    } else if (distance > point.size * 1.4) {
      this.left ??= now;
      if (now - this.left >= 120) this.reset();
    } else this.left = null;
    if (!this.active) return null;
    if (distance >= Math.max(6, point.size * 0.08)) {
      const angle =
        ((Math.atan2(point.dx, -point.dy) * 180) / Math.PI + 360) % 360;
      const difference =
        this.direction === null
          ? 180
          : Math.abs(((angle - this.direction * 22.5 + 540) % 360) - 180);
      if (difference > 11.25 + 3)
        this.direction = Math.floor(angle / 22.5 + 0.5) % 16;
    }
    return this.direction === null
      ? null
      : { row: 9 + Math.floor(this.direction / 8), column: this.direction % 8 };
  }
}
