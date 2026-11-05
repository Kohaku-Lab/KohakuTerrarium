export class FrameStats {
  constructor() {
    this.reset();
  }
  reset() {
    this.samples = [];
  }
  add(time, cost) {
    this.samples.push({ time, cost });
    while (this.samples.length && this.samples[0].time < time - 10000)
      this.samples.shift();
  }
  summary() {
    const samples = this.samples;
    const costs = samples.map((s) => s.cost).sort((a, b) => a - b);
    const duration =
      samples.length > 1 ? samples.at(-1).time - samples[0].time : 0;
    return {
      seconds: duration / 1000,
      frames: samples.length,
      fps: duration ? ((samples.length - 1) * 1000) / duration : 0,
      cpuMean: costs.length
        ? costs.reduce((a, b) => a + b, 0) / costs.length
        : 0,
      cpuP95: costs[Math.max(0, Math.ceil(costs.length * 0.95) - 1)] || 0,
      stalls: samples.slice(1).filter((s, i) => s.time - samples[i].time > 100)
        .length,
    };
  }
}
