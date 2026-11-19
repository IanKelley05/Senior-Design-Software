// Shared geometry and drawing for the receiver dashboard and separate sender UI.
class RhitVest {
  constructor(canvas, grid) {
    this.canvas = canvas;
    this.grid = grid;
    this.ctx = canvas.getContext('2d');
    this.path = new Path2D(grid.outline);
    this.contacts = [];
    this.preview = null;
    this.pulses = [];
    this.frame = null;
    this.surface = document.createElement('canvas');
    this.surface.width = grid.width / 2;
    this.surface.height = grid.height / 2;
    canvas.width = grid.width * 2;
    canvas.height = grid.height * 2;
    this.reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.setHits([]);
  }
  point(event) {
    const rect = this.canvas.getBoundingClientRect();
    return { x: (event.clientX - rect.left) * this.grid.width / rect.width,
      y: (event.clientY - rect.top) * this.grid.height / rect.height };
  }
  contains(point) {
    const test = this.surface.getContext('2d');
    return test.isPointInPath(this.path, point.x, point.y);
  }
  footprint(point, size) {
    const g = this.grid, b = g.bounds;
    const column = Math.min(g.columns, Math.max(1, Math.floor((point.x - b.x) / b.width * g.columns) + 1));
    const row = Math.min(g.rows, Math.max(1, Math.floor((point.y - b.y) / b.height * g.rows) + 1));
    const range = (center, length, maximum) => {
      const start = Math.max(1, Math.min(maximum - length + 1, center - Math.floor((length - 1) / 2)));
      return Array.from({length}, (_, i) => start + i);
    };
    return {rows: range(row, size.rows, g.rows), columns: range(column, size.columns, g.columns)};
  }
  matches(contact) {
    return contact && contact.grid_rows === this.grid.rows && contact.grid_columns === this.grid.columns;
  }
  setHits(hits, fresh = []) {
    const g = this.grid;
    this.contacts = hits.filter(hit => this.matches(hit.contact)).map(hit => hit.contact);
    this.counts = Array.from({length: g.rows}, () => Array(g.columns).fill(0));
    for (const c of this.contacts) for (const row of c.rows) for (const col of c.columns) this.counts[row - 1][col - 1]++;
    this.peak = Math.max(0, ...this.counts.flat());
    this.canvas.dataset.spatialHits = this.contacts.length;
    this.canvas.dataset.peakCoverage = this.peak;
    this.canvas.setAttribute('aria-label', this.contacts.length + ' spatial hits. Peak estimated wire coverage: ' + this.peak + ' hits. Faint lines show ' + g.columns + ' columns and ' + g.rows + ' rows.');
    this.buildHeat();
    for (const hit of fresh) if (this.matches(hit.contact)) this.pulses.push({contact: hit.contact, at: performance.now()});
    this.draw();
  }
  clear() { this.pulses = []; this.setHits([]); }
  buildHeat() {
    const g = this.grid, b = g.bounds;
    const ctx = this.surface.getContext('2d');
    const width = this.surface.width, height = this.surface.height;
    const image = ctx.createImageData(width, height);
    const sample = (r,c) => this.counts[r]?.[c] || 0;
    if (this.peak) for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
      const gx = (x * 2 - b.x) / b.width * g.columns - .5;
      const gy = (y * 2 - b.y) / b.height * g.rows - .5;
      const c = Math.floor(gx), r = Math.floor(gy), fx = gx - c, fy = gy - r;
      const value = sample(r,c)*(1-fx)*(1-fy) + sample(r,c+1)*fx*(1-fy) + sample(r+1,c)*(1-fx)*fy + sample(r+1,c+1)*fx*fy;
      if (value <= .01) continue;
      const t = value / this.peak;
      const stops = [[38,139,168],[255,196,98],[255,91,70]];
      const i = t < .5 ? 0 : 1, mix = t < .5 ? t*2 : (t-.5)*2;
      const offset = (y * width + x) * 4;
      for (let k=0;k<3;k++) image.data[offset+k] = stops[i][k] + mix*(stops[i+1][k]-stops[i][k]);
      image.data[offset+3] = Math.min(1, value * 1.5) * 240;
    }
    ctx.putImageData(image,0,0);
  }
  drawContact(ctx, contact, alpha=1) {
    const g=this.grid,b=g.bounds;
    const left=b.x+(Math.min(...contact.columns)-1)*b.width/g.columns;
    const top=b.y+(Math.min(...contact.rows)-1)*b.height/g.rows;
    const width=(Math.max(...contact.columns)-Math.min(...contact.columns)+1)*b.width/g.columns;
    const height=(Math.max(...contact.rows)-Math.min(...contact.rows)+1)*b.height/g.rows;
    ctx.strokeStyle='rgba(255,255,255,'+alpha+')'; ctx.lineWidth=2;
    ctx.beginPath(); ctx.ellipse(left+width/2,top+height/2,width/2,height/2,0,0,Math.PI*2); ctx.stroke();
  }
  draw() {
    const ctx=this.ctx,g=this.grid,b=g.bounds,now=performance.now();
    ctx.setTransform(2,0,0,2,0,0); ctx.clearRect(0,0,g.width,g.height);
    // Dummy head and neck; only the vest accepts contacts.
    ctx.fillStyle='#293542'; ctx.strokeStyle='#536479'; ctx.lineWidth=1.2;
    ctx.beginPath(); ctx.roundRect(161,64,38,48,10); ctx.fill();
    ctx.beginPath(); ctx.ellipse(180,42,33,37,0,0,Math.PI*2); ctx.fill(); ctx.stroke();
    ctx.fillStyle='#25313e'; ctx.fill(this.path);
    ctx.save(); ctx.clip(this.path);
    ctx.imageSmoothingEnabled=true; ctx.drawImage(this.surface,0,0,g.width,g.height);
    ctx.strokeStyle='rgba(205,161,112,.22)'; ctx.lineWidth=.8;
    for(let col=0;col<g.columns;col++) {
      const x=b.x+(col+.5)*b.width/g.columns;
      ctx.beginPath();ctx.moveTo(x,b.y);ctx.lineTo(x,b.y+b.height);ctx.stroke();
    }
    for(let row=0;row<g.rows;row++) {
      const y=b.y+(row+.5)*b.height/g.rows;
      ctx.beginPath();ctx.moveTo(b.x,y);ctx.lineTo(b.x+b.width,y);ctx.stroke();
    }
    if(this.preview) this.drawContact(ctx,this.preview,.9);
    this.pulses=this.pulses.filter(p=>now-p.at<800);
    for(const pulse of this.pulses) this.drawContact(ctx,pulse.contact,this.reduced ? 1 : 1-(now-pulse.at)/800);
    ctx.restore();ctx.strokeStyle='#8191a4';ctx.lineWidth=1.5;ctx.stroke(this.path);
    if(this.pulses.length && !this.frame) this.frame=requestAnimationFrame(()=>{this.frame=null;this.draw();});
  }
}
window.RhitVest=RhitVest;
