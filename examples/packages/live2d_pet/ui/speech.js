// Local text rhythm, not audio lip-sync. Only fresh reply chunks enter this queue.
export class SpeechMouth {
  constructor(now = () => performance.now(), wallClock = () => Date.now()) {
    this.now = now;
    this.wallClock = wallClock;
    this.enabled = true;
    this.suspended = false;
    this.key = undefined;
    this.sequence = 0;
    this.serial = 0;
    this.reset();
  }

  reset() {
    this.queue = [];
    this.current = null;
    this.value = 0;
    this.lastSample = this.now();
    this.lastText = -Infinity;
    this.speaking = false;
  }

  configure(enabled) {
    if (this.enabled === enabled) return;
    this.enabled = enabled;
    this.reset();
  }

  suspend(value) {
    if (this.suspended === value) return;
    this.suspended = value;
    this.reset();
  }

  observe(work, onText) {
    const speech = work?.speech;
    const key = work?.connected
      ? JSON.stringify([
          work.hostId,
          work.sessionId,
          work.creatureId,
          speech?.stream,
        ])
      : null;
    // A new renderer / selection / connection establishes a baseline, never a replay.
    if (key !== this.key) {
      this.key = key;
      this.sequence = speech?.sequence || 0;
      this.reset();
      return;
    }
    if (!key || !speech || speech.sequence <= this.sequence) return;
    this.sequence = speech.sequence;
    if (!speech.active) {
      this.queue = [];
      this.current = null;
      this.speaking = false;
      return;
    }
    if (!this.enabled || this.wallClock() - speech.at > 1200) return;
    onText?.();
    if (this.suspended) return;
    const text = typeof speech.text === "string" ? speech.text.slice(-256) : "";
    const beats = [];
    for (const token of text.match(
      /[a-zA-Z]+|[\p{L}\p{N}]|[，,、；;：:。.!！?？…\n]/gu,
    ) || []) {
      const pause = /[。.!！?？…\n]/u.test(token)
        ? 300
        : /[，,、；;：:]/u.test(token)
          ? 170
          : 0;
      const count = pause
        ? 1
        : /^[a-zA-Z]+$/.test(token)
          ? Math.min(4, Math.ceil(token.length / 3))
          : 1;
      for (let i = 0; i < count; i++) {
        const variation = (++this.serial * 7) % 11;
        beats.push({
          duration: pause || 125 + variation * 7,
          peak: pause ? 0 : 0.42 + variation * 0.027,
        });
      }
    }
    if (!beats.length) return;
    this.lastText = this.now();
    this.speaking = true;
    this.queue.push(...beats);
    // Streams can be much faster than speech: retain recent rhythm, never recite a backlog.
    let duration = this.queue.reduce((total, beat) => total + beat.duration, 0);
    while (duration > 900 && this.queue.length > 1)
      duration -= this.queue.shift().duration;
  }

  sample() {
    const now = this.now();
    const elapsed = Math.max(0, Math.min(100, now - this.lastSample));
    this.lastSample = now;
    if (!this.enabled || this.suspended) return null;
    if (now - this.lastText > 1000) {
      this.current = null;
      this.queue = [];
      this.speaking = false;
    }
    if (this.current && now >= this.current.end) this.current = null;
    if (!this.current && this.queue.length) {
      const beat = this.queue.shift();
      this.current = { ...beat, start: now, end: now + beat.duration };
    }
    const beat = this.current;
    const phase = beat ? Math.min(1, (now - beat.start) / beat.duration) : 0;
    const target = beat ? beat.peak * Math.sin(Math.PI * phase) ** 1.4 : 0;
    this.value +=
      (target - this.value) *
      (1 - Math.exp(-elapsed / (target > this.value ? 28 : 42)));
    if (this.value < 0.002 && !target) this.value = 0;
    return !this.speaking && !this.value ? null : this.value;
  }
}
