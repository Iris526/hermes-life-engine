let currentPeriod = 'today';
let currentDate = null;
let lastSnapshot = null;
let source = null;
const $ = (id) => document.getElementById(id);

function fmtTime(value){
  if(!value) return '--';
  const d = new Date(value);
  if(Number.isNaN(d.getTime())) return String(value).slice(11,16) || value;
  const mm = String(d.getMonth()+1).padStart(2,'0');
  const dd = String(d.getDate()).padStart(2,'0');
  const hh = String(d.getHours()).padStart(2,'0');
  const mi = String(d.getMinutes()).padStart(2,'0');
  return `${mm}-${dd} ${hh}:${mi}`;
}
function esc(s){return String(s ?? '').replace(/[&<>'"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));}
function statusText(s){
  const map={planned:'计划',scheduled:'已排期',ready:'就绪',in_progress:'进行中',partial:'部分完成',completed:'完成',postponed:'推迟',rescheduled:'改期',cancelled:'取消',missed:'错过',failed:'失败',released:'已释放',pending:'待处理'};
  return map[s] || s || '--';
}
function pct(v){ const n = Number(v || 0); return Math.max(0, Math.min(100, n)); }
function jsonBlock(obj){ return `<pre class="json">${esc(JSON.stringify(obj ?? {}, null, 2))}</pre>`; }
function kv(label,value){ return `<div class="kv"><span>${esc(label)}</span><b>${esc(value ?? '--')}</b></div>`; }
function setLive(status, text){
  const dot=$('liveDot'); const t=$('liveText');
  dot.className = `live-dot ${status}`; t.textContent = text;
}

async function api(path, opts={}){
  const r = await fetch(path, opts);
  if(!r.ok) throw new Error(await r.text());
  return r.json();
}

function streamUrl(){
  const q = new URLSearchParams({period: currentPeriod});
  if(currentDate) q.set('date', currentDate);
  return `/api/stream?${q}`;
}
function startStream(){
  if(source) source.close();
  if(!window.EventSource){ setLive('idle','polling'); return; }
  source = new EventSource(streamUrl());
  source.addEventListener('snapshot', ev=>{
    try{ render(JSON.parse(ev.data)); setLive('live','live'); }catch(e){ setLive('error','stream parse error'); }
  });
  source.addEventListener('heartbeat', ()=> setLive('live','live · no changes'));
  source.addEventListener('error', ()=>{ setLive('error','stream reconnecting'); });
}
async function refresh(){
  try{
    const q = new URLSearchParams({period: currentPeriod});
    if(currentDate) q.set('date', currentDate);
    const snap = await api(`/api/snapshot?${q}`);
    render(snap); setLive('live','refreshed');
  }catch(e){ $('avatarBubble').textContent = `连接失败：${e.message}`; setLive('error','error'); }
}

function render(snap){
  lastSnapshot = snap;
  const av = snap.avatar || {};
  const wrap = $('avatarWrap');
  wrap.className = `avatar-wrap ${av.sprite_state || 'idle'}`;
  $('scene').className = `scene scene-${av.scene || 'observatory'}`;
  $('avatarBubble').textContent = av.bubble || '观察生活流';
  $('stateLabel').textContent = av.label || '--';
  $('modeLabel').textContent = (snap.state && snap.state.mode) || '--';
  $('ownerLabel').textContent = `${snap.owner.owner_kind}/${snap.owner.owner_id}`;
  $('updatedAt').textContent = snap.updated_at ? `更新 ${fmtTime(snap.updated_at)}` : '';
  $('pathInput').placeholder = snap.meta.db_path || 'LifeEngine path';
  renderOwners(snap.owners || [], snap.owner || {});
  renderOverview(snap);
  renderSchedule(snap.schedule || {});
  renderReview(snap.review_items || []);
  renderEvents(snap.recent_events || []);
  renderResources(snap.resources || []);
  renderDreams(snap.dreams || []);
  renderMessages(snap.delayed_replies || [], snap.proactive || {});
  renderTrace(snap.trace || []);
}

function renderOwners(owners, selected){
  const sel=$('ownerSelect');
  const val=`${selected.owner_kind||'agent'}::${selected.owner_id||'default-agent'}`;
  const options=owners.map(o=>`<option value="${esc(o.owner_kind)}::${esc(o.owner_id)}" ${`${o.owner_kind}::${o.owner_id}`===val?'selected':''}>${esc(o.owner_kind)}/${esc(o.owner_id)}</option>`).join('');
  if(sel.dataset.last !== options){ sel.innerHTML=options; sel.dataset.last=options; }
}
function renderOverview(snap){
  const event = snap.current_event;
  $('currentEvent').innerHTML = event ?
    `<b>${esc(event.title)}</b><div class="badges"><span class="badge">${esc(event.event_category || event.event_type)}</span><span class="badge">${statusText(event.status)}</span><span class="badge">重要度 ${event.importance ?? '--'}</span></div>` :
    `<span class="subtle">当前没有 active event。</span>`;
  $('currentEvent').onclick = () => event && openEvent(event.id);
  const body = (snap.state && (snap.state.body_state || {})) || {};
  const mind = (snap.state && (snap.state.mind_state || {})) || {};
  const resources = Object.fromEntries((snap.resources||[]).map(r=>[r.resource_key, r.current_value]));
  const meters = [['energy','精力', resources.energy ?? body.energy],['fatigue','疲劳', resources.fatigue ?? body.fatigue],['focus','专注', resources.focus ?? mind.focus],['mood','心情', resources.mood ?? mind.mood]];
  $('meters').innerHTML = meters.map(([key,label,val])=>{
    const missing = val == null || val === '';
    return `<div class="meter ${missing ? 'meter-missing' : ''}"><div>${label} <b>${missing ? '未记录' : esc(val)}</b></div><div class="bar"><div class="fill" style="width:${missing ? 0 : pct(val)}%"></div></div></div>`;
  }).join('');
  const sd = snap.sleep_day_state || {};
  $('sleepDebt').textContent = sd.cumulative_sleep_debt_minutes != null ? `${sd.cumulative_sleep_debt_minutes}min` : '--';
  $('recoveryPressure').textContent = sd.recovery_pressure ?? '--';
  $('delayedCount').textContent = (snap.delayed_replies || []).filter(x=>x.status !== 'released').length;
  $('dreamCount').textContent = (snap.dreams || []).length;
}

function renderSchedule(schedule){
  $('scheduleLabel').textContent = schedule.label || '';
  const items = schedule.items || [];
  if(!items.length){$('timeline').innerHTML = '<div class="empty">这段时间没有日程。</div>';return;}
  $('timeline').innerHTML = items.map(it=>{
    const title = it.event_title || it.title || it.block_type || '未命名日程';
    const time = `${fmtTime(it.start)} - ${fmtTime(it.end)}`;
    const actual = it.actual_start || it.actual_end ? `<span class="badge">实际 ${fmtTime(it.actual_start)} - ${fmtTime(it.actual_end)}</span>` : '';
    const loc = it.location && it.location.name ? `<span class="badge">${esc(it.location.name)}</span>` : '';
    const intr = it.interruptibility && it.interruptibility.level ? `<span class="badge">${esc(it.interruptibility.level)}</span>` : '';
    return `<div class="timeline-item" data-event="${esc(it.event_id||'')}"><div class="time">${esc(time)}</div><div><div class="item-title">${esc(title)}</div><div class="badges"><span class="badge">${esc(it.block_type)}</span><span class="badge">${statusText(it.status)}</span><span class="badge">${esc(it.event_category || it.event_type || 'event')}</span>${actual}${loc}${intr}</div></div></div>`;
  }).join('');
  document.querySelectorAll('.timeline-item').forEach(el=>{ el.onclick=()=>{ const id=el.dataset.event; if(id) openEvent(id); }; });
}

function renderReview(items){
  if(!items.length){$('reviewList').innerHTML='<div class="empty">没有需要人类处理的项目。Agent 会按策略自行处理低风险维护项。</div>';return;}
  $('reviewList').innerHTML = items.map(x=>`<div class="feed-item severity-${esc(x.severity || 'info')}" data-detail="review:${esc(x.id)}"><b>${esc(x.title)}</b><div>${esc(x.message)}</div><div class="badges"><span class="badge">${esc(x.item_type)}</span><span class="badge">${esc(x.severity)}</span>${x.action_hint && x.action_hint.tool ? `<span class="badge">${esc(x.action_hint.tool)}:${esc(x.action_hint.action)}</span>`:''}</div></div>`).join('');
}
function renderEvents(items){
  if(!items.length){$('eventList').innerHTML='<div class="empty">暂无近期事件。</div>';return;}
  $('eventList').innerHTML = items.slice(0,18).map(e=>`<div class="feed-item" data-event="${esc(e.id)}"><b>${esc(e.title || e.id)}</b><div class="badges"><span class="badge">${esc(e.event_category || e.event_type || 'event')}</span><span class="badge">${statusText(e.status)}</span><span class="badge">${fmtTime(e.updated_at)}</span></div></div>`).join('');
  document.querySelectorAll('#eventList .feed-item').forEach(el=>el.onclick=()=>openEvent(el.dataset.event));
}
function renderResources(items){
  if(!items.length){$('resources').innerHTML='<div class="empty">暂无资源。</div>';return;}
  $('resources').innerHTML = items.slice(0,14).map(r=>`<div class="res-item"><span>${esc(r.display_name || r.resource_key)}</span><b>${esc(r.current_value)} ${esc(r.unit || '')}</b><small>${esc(r.resource_class || '')}</small></div>`).join('');
}
function renderDreams(items){
  if(!items.length){$('dreams').innerHTML='<div class="empty">还没有梦境记录。</div>';return;}
  $('dreams').innerHTML = items.map(d=>`<div class="dream-item" data-dream="${esc(d.id)}"><b>${esc(d.title || '梦境')}</b><div>${esc(d.summary || d.share_text || d.content || '').slice(0,180)}</div><div class="badges"><span class="badge">${esc(d.truth_layer || 'dream_symbolic')}</span><span class="badge">${esc(d.emotional_tone || '')}</span></div></div>`).join('');
  document.querySelectorAll('#dreams .dream-item').forEach(el=>el.onclick=()=>openDream(el.dataset.dream));
}
function renderMessages(delayed, pro){
  const pending = (delayed||[]).filter(x=>x.status !== 'released').map(x=>`<div class="feed-item"><b>延迟回复</b><div>${esc(x.message_text || '').slice(0,160)}</div><div class="badges"><span class="badge">${statusText(x.status)}</span><span class="badge">${fmtTime(x.created_at)}</span></div></div>`);
  const intents = ((pro&&pro.intents)||[]).slice(0,6).map(x=>`<div class="feed-item"><b>主动意图</b><div>${esc(x.summary || '').slice(0,160)}</div><div class="badges"><span class="badge">${statusText(x.status)}</span><span class="badge">${fmtTime(x.created_at)}</span></div></div>`);
  $('messages').innerHTML = pending.concat(intents).join('') || '<div class="empty">暂无延迟回复或主动意图。</div>';
}
function renderTrace(items){
  if(!items.length){$('trace').innerHTML='<div class="empty">暂无流水。</div>';return;}
  $('trace').innerHTML = items.map(t=>`<div class="trace-item" data-trace="${esc(t.transaction_id || t.id)}"><b>${esc(t.entry_type)}</b> · ${esc(t.created_at)}<br/><span class="subtle">${esc(t.owner_kind)}/${esc(t.owner_id)} · ${esc(t.source || '')}</span></div>`).join('');
  document.querySelectorAll('#trace .trace-item').forEach(el=>el.onclick=()=>openTrace(el.dataset.trace));
}

function openDrawer(kind,title,html){
  $('drawerKind').textContent=kind; $('drawerTitle').textContent=title; $('drawerBody').innerHTML=html;
  $('drawer').classList.add('open'); $('drawer').setAttribute('aria-hidden','false');
}
function closeDrawer(){ $('drawer').classList.remove('open'); $('drawer').setAttribute('aria-hidden','true'); }
function rows(items, render){ if(!items||!items.length) return '<div class="empty">无记录。</div>'; return `<div class="table-lite">${items.map(render).join('')}</div>`; }
function detailEventHtml(d){
  if(!d.found) return '<div class="empty">找不到这个 Event。</div>';
  const e=d.event;
  return `<div class="detail-section"><h3>事件</h3>${kv('标题',e.title)}${kv('状态',statusText(e.status))}${kv('分类',`${e.event_category||''} / ${e.event_type||''} / ${e.activity_domain||''}`)}${kv('计划',`${fmtTime(e.planned_start)} - ${fmtTime(e.planned_end)}`)}${kv('实际',`${fmtTime(e.actual_start)} - ${fmtTime(e.actual_end)}`)}${kv('重要度',e.importance)}${kv('地点',e.location&&e.location.name?e.location.name:JSON.stringify(e.location||{}))}</div>
  <div class="detail-section"><h3>状态流转</h3>${rows(d.transitions, t=>`<div class="table-row"><b>${statusText(t.from_status)} → ${statusText(t.to_status)}</b><br><span class="subtle">${fmtTime(t.occurred_at)} · ${esc(t.reason||t.source||'')}</span></div>`)}</div>
  <div class="detail-section"><h3>日程块</h3>${rows(d.schedule_blocks, s=>`<div class="table-row"><b>${fmtTime(s.start)} - ${fmtTime(s.end)}</b> · ${statusText(s.status)}<br><span class="subtle">实际 ${fmtTime(s.actual_start)} - ${fmtTime(s.actual_end)} · ${esc(s.block_type)}</span></div>`)}</div>
  <div class="detail-section"><h3>结果 / 资源</h3>${rows((d.results||[]).concat(d.resource_ledger||[]), r=>`<div class="table-row"><b>${esc(r.summary || r.resource_key || r.result_type || r.operation)}</b><br><span class="subtle">${esc(r.delta!=null?('delta '+r.delta):'')} ${esc(r.reason||'')} ${fmtTime(r.created_at)}</span></div>`)}</div>
  <div class="detail-section"><h3>睡眠/执行调整</h3>${rows(d.execution_sleep_adjustments, a=>`<div class="table-row"><b>${esc(a.adjustment_type)}</b> · ${esc(a.severity)}<br><span class="subtle">${esc(a.reason||'')}</span></div>`)}</div>
  <div class="detail-section"><h3>Journal 引用</h3>${rows(d.journal, j=>`<div class="table-row"><b>${esc(j.entry_type)}</b><br><span class="subtle">${esc(j.id)} · ${fmtTime(j.created_at)}</span></div>`)}</div>`;
}
function detailDreamHtml(d){
  if(!d.found) return '<div class="empty">找不到这个梦。</div>';
  const x=d.dream;
  return `<div class="detail-section"><h3>梦</h3>${kv('标题',x.title)}${kv('情绪',x.emotional_tone)}${kv('Truth Layer',x.truth_layer)}${kv('创建时间',fmtTime(x.created_at))}<p>${esc(x.content||x.summary||'')}</p></div>
  <div class="detail-section"><h3>醒来分享文本</h3><p>${esc(x.share_text||'')}</p></div>
  <div class="detail-section"><h3>Dream Runs</h3>${rows(d.runs, r=>`<div class="table-row"><b>${esc(r.status)} / ${esc(r.run_type)}</b><br><span class="subtle">${fmtTime(r.started_at)} - ${fmtTime(r.completed_at)}</span></div>`)}</div>
  <div class="detail-section"><h3>Audit Findings</h3>${rows(d.findings, f=>`<div class="table-row"><b>${esc(f.finding_type||f.type)}</b> · ${esc(f.status||'')}<br><span class="subtle">${esc(f.message||f.reason||'')}</span></div>`)}</div>`;
}
function detailTraceHtml(d){
  if(!d.found) return `<div class="detail-section"><h3>未找到</h3>${jsonBlock(d)}</div>`;
  const sections=[];
  for(const [k,v] of Object.entries(d)){
    if(['kind','id','found'].includes(k)) continue;
    sections.push(`<div class="detail-section"><h3>${esc(k)}</h3>${jsonBlock(v)}</div>`);
  }
  return sections.join('');
}
async function openEvent(id){ if(!id) return; const d=await api(`/api/event/${encodeURIComponent(id)}`); openDrawer('event', d.event?.title || id, detailEventHtml(d)); }
async function openDream(id){ if(!id) return; const d=await api(`/api/dream/${encodeURIComponent(id)}`); openDrawer('dream', d.dream?.title || id, detailDreamHtml(d)); }
async function openTrace(id){ if(!id) return; const d=await api(`/api/trace/explain/${encodeURIComponent(id)}`); openDrawer(d.kind||'trace', id, detailTraceHtml(d)); }

async function selectPath(){
  const path = $('pathInput').value.trim(); if(!path) return;
  const out = await api('/api/select',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path})});
  if(out.owners && out.owners.length){ await api('/api/owner',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(out.selected_owner)}); }
  startStream(); await refresh();
}
async function selectOwner(){
  const [owner_kind, owner_id] = $('ownerSelect').value.split('::');
  await api('/api/owner',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({owner_kind, owner_id})});
  startStream(); await refresh();
}
async function action(name,payload={}){
  const out = await api('/api/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:name,payload})});
  if(!out.ok && out.message) alert(out.message);
  if(out.error) alert(out.error);
  await refresh();
}

document.addEventListener('DOMContentLoaded',()=>{
  $('selectBtn').onclick=selectPath;
  $('ownerSelect').onchange=selectOwner;
  $('refreshBtn').onclick=refresh;
  $('drawerClose').onclick=closeDrawer;
  $('callBtn').onclick=()=>action('call',{message_text:'WebUI call'});
  $('tickBtn').onclick=()=>action('tick',{});
  $('recoveryBtn').onclick=()=>action('sleep_recovery_plan',{});
  $('applySafeBtn').onclick=()=>action('review_apply_all',{limit:5});
  document.querySelectorAll('.tabbar button').forEach(btn=>btn.onclick=()=>{
    document.querySelectorAll('.tabbar button').forEach(b=>b.classList.remove('active'));
    btn.classList.add('active'); currentPeriod=btn.dataset.period; currentDate=null; startStream(); refresh();
  });
  $('dateInput').onchange=(e)=>{currentPeriod='day'; currentDate=e.target.value; document.querySelectorAll('.tabbar button').forEach(b=>b.classList.remove('active')); startStream(); refresh();};
  startStream(); refresh();
});
