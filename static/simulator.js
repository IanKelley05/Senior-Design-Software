const $ = id => document.getElementById(id);
const grid = JSON.parse($('vest-config').textContent);
const target = new RhitVest($('target-vest'), grid);
let size = grid.sizes.find(s => s.id === 'medium');
let aim = {x:180,y:280};
let ready = false, busy = false, lastPacket = null, sequence = 0;
const boot = crypto.randomUUID();
let statusTimer;
function preview() {
  target.preview = target.contains(aim) ? target.footprint(aim,size) : null;
  target.draw();
  if(target.preview) $('aim-label').textContent = 'Rows ' + target.preview.rows.join(', ') + ' · Columns ' + target.preview.columns.join(', ');
}
function controls() {
  $('target-vest').setAttribute('aria-disabled', String(!ready || busy));
  $('repeat-hit').disabled = !ready || busy || !lastPacket;
}
async function status() {
  try {
    const response = await fetch('/api/status', {cache:'no-store',signal:AbortSignal.timeout(6000)});
    const data = await response.json();
    ready = response.ok && data.ready;
    $('sender-status').textContent = data.message;
    $('target-session').textContent = data.active ? 'Recording into ' + data.active.name + ' (#' + data.active.id + ')' : 'No active session';
  } catch {ready=false;$('sender-status').textContent='Simulator connection unavailable. Check both terminals.';}
  controls();
  statusTimer=setTimeout(status,1500);
}
async function transmit(packet) {
  if(busy || !ready) return;
  busy=true;controls();
  $('send-message').textContent='Sending simulated impact…';
  try {
    const response=await fetch('/api/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(packet),signal:AbortSignal.timeout(7000)});
    const result=await response.json();
    if(!response.ok) throw new Error(result.error || 'Send failed');
    $('send-message').textContent='Sent by UDP. Waiting for the main receiver to confirm…';
    $('wire-message').textContent='Rows '+packet.rows.join(', ')+' · Columns '+packet.columns.join(', ')+' · '+packet.simulated_force_n+' N · One event';
    target.setHits([{contact:{rows:packet.rows,columns:packet.columns,grid_rows:grid.rows,grid_columns:grid.columns}}]);
    let confirmed=false;
    for(let i=0;i<12;i++){
      await new Promise(resolve=>setTimeout(resolve,150));
      const receipt=await fetch('/api/receipt/'+result.event_id,{cache:'no-store',signal:AbortSignal.timeout(4000)});
      if(!receipt.ok) break;
      const saved=await receipt.json();
      if(saved.found){
        confirmed=true;
        $('send-message').textContent='Confirmed in session #'+saved.hit.session_id+'. One saved hit; repeating this packet does not add another.';
        break;
      }
    }
    if(!confirmed) $('send-message').textContent='Sent, but not confirmed. Check the main session and receiver counters. Repeat the same packet to retry.';
  } catch(error){$('send-message').textContent=error.message;}
  finally {busy=false;controls();}
}
function hit() {
  if(!ready || busy || !target.contains(aim)) return;
  const contact=target.footprint(aim,size);
  lastPacket={protocol_version:2,device_id:'interactive-glove',boot_id:boot,sequence:sequence++,timestamp:new Date().toISOString(),rows:contact.rows,columns:contact.columns,simulated_force_n:Number($('force').value)};
  transmit(lastPacket);
}
$('target-vest').addEventListener('pointermove',event=>{aim=target.point(event);preview();});
$('target-vest').addEventListener('pointerleave',()=>{target.preview=null;target.draw();});
$('target-vest').addEventListener('click',event=>{aim=target.point(event);preview();hit();});
$('target-vest').addEventListener('keydown',event=>{
  const changes={ArrowLeft:[-12,0],ArrowRight:[12,0],ArrowUp:[0,-12],ArrowDown:[0,12]};
  if(changes[event.key]){event.preventDefault();const [dx,dy]=changes[event.key];aim={x:Math.max(0,Math.min(grid.width,aim.x+dx)),y:Math.max(0,Math.min(grid.height,aim.y+dy))};preview();}
  if(event.key==='Enter'||event.key===' '){event.preventDefault();preview();hit();}
});
function forceChanged(){ $('force-value').value=$('force').value;for(const button of document.querySelectorAll('[data-force]'))button.classList.toggle('selected',button.dataset.force===$('force').value);}
$('force').addEventListener('input',forceChanged);
for(const button of document.querySelectorAll('[data-force]')) button.addEventListener('click',()=>{$('force').value=button.dataset.force;forceChanged();});
for(const button of document.querySelectorAll('[data-size]')) button.addEventListener('click',()=>{
  size=grid.sizes.find(s=>s.id===button.dataset.size);
  for(const option of document.querySelectorAll('[data-size]'))option.classList.toggle('selected',option===button);
  preview();
});
$('repeat-hit').addEventListener('click',()=>transmit(lastPacket));
preview();status();
