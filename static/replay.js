// Pure replay clock. Each saved hit owns an equal slice of a 30-second timeline.
// count is the number of revealed hits; zero is the blank starting frame.
class RhitReplay {
  constructor(total = 0) { this.reset(total); }
  reset(total) {
    this.total = total;
    this.duration = 30000;
    this.elapsed = 0;
    this.count = 0;
    this.active = false;
    this.playing = false;
    this.lastTime = null;
  }
  play(now) {
    if (!this.total || this.playing) return;
    if (this.elapsed >= this.duration) this.seek(0);
    this.active = true;
    this.playing = true;
    this.count = Math.max(1, this.count);
    this.lastTime = now;
  }
  tick(now) {
    if (!this.playing) return;
    this.elapsed = Math.min(this.duration, this.elapsed + Math.max(0, now - this.lastTime));
    this.lastTime = now;
    this.count = Math.min(this.total, Math.floor(this.elapsed / this.duration * this.total) + 1);
    if (this.elapsed >= this.duration) { this.playing = false; this.lastTime = null; }
  }
  pause(now) { this.tick(now); this.playing = false; this.lastTime = null; }
  seek(count) {
    this.active = true;
    this.playing = false;
    this.lastTime = null;
    this.count = Math.max(0, Math.min(this.total, Math.trunc(count)));
    this.elapsed = this.total ? Math.max(0, this.count - 1) / this.total * this.duration : 0;
  }
  step(direction) {
    const count = this.active ? this.count : (direction < 0 ? this.total : 0);
    this.seek(count + direction);
  }
  overview() { this.reset(this.total); }
}
globalThis.RhitReplay = RhitReplay;
