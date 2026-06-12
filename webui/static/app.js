const $ = (id) => document.getElementById(id);
let snapshot = null;
let currentPeriod = 'today';
let currentDate = null;
let eventSource = null;

function esc(v){return String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function num(v){const n=Number(v);return Number.isFinite(n)?n:null;}
function pct(v,max=100){const n=num(v);return n==null?0:Math.max(0,Math.min(100,Math.round(n/max*100)));}
function fmtDateTime(v){if(!v)return '--';try{const d=new Date(v);if(!isNaN(d))return d.toLocaleString([], {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit'});}catch(e){}return String(v).replace('T',' ').slice(0,16);}
function fmtTime(v){if(!v)return '--';try{const d=new Date(v);if(!isNaN(d))return d.toLocaleTimeString([], {hour:'2-digit',minute:'2-digit'});}catch(e){}const s=String(v);return s.includes('T')?s.split('T')[1].slice(0,5):s.slice(0,5);}
function fmtRange(a,b){return `${fmtTime(a)} - ${fmtTime(b)}`;}
function statusText(s){return ({planned:'计划中',scheduled:'已排期',ready:'就绪',in_progress:'进行中',partial:'部分完成',completed:'已完成',postponed:'已推迟',rescheduled:'已改期',cancelled:'已取消',failed:'失败',missed:'错过',released:'已释放',pending:'待处理',open:'待处理',queued:'排队中',sent:'已发送'}[s]||s||'--');}
function kindText(s){return ({sleep:'睡眠',work:'工作',study:'学习',meal:'用餐',purchase:'购买',travel:'出行',health:'健康',fitness:'运动',dream:'梦',reflection:'复盘',leisure:'休闲',social:'社交',maintenance:'维护',creative:'创作',serendipity:'小插曲'}[s]||s||'事件');}
function severityText(s){return ({info:'提示',warning:'提醒',error:'错误',danger:'危险',action:'待操作'}[s]||s||'提示');}
function jsonBlock(v){return `<details class="raw"><summary>查看原始 JSON</summary><pre class="json">${esc(JSON.stringify(v,null,2))}</pre></details>`;}
function kv(k,v){return `<div class="kv"><span>${esc(k)}</span><b>${esc(v ?? '--')}</b></div>`;}

async function api(path, opts={}){
  const res = await fetch(path, opts);
  if(!res.ok) throw new Error(await res.text());
  return await res.json();
}

function humanSprite(sprite){return ({sleep:'睡觉',dream:'做梦',reply:'回消息',work:'忙碌',battle:'不可打断',walk:'行动',eat:'吃饭',tired:'疲惫',recover:'恢复',idle:'待机'}[sprite]||sprite||'待机');}
function spriteFile(sprite){
  const allowed = new Set(['idle','walk','work','battle','sleep','dream','reply','eat','tired','recover']);
  const s = allowed.has(sprite) ? sprite : 'idle';
  return `/static/assets/sprite-${s}.png`;
}

async function refresh(){
  try{
    const q = new URLSearchParams({period: currentPeriod});
    if(currentDate) q.set('date', currentDate);
    const data = await api(`/api/snapshot?${q}`);
    renderSnapshot(data);
    setLive('live','已连接');
  }catch(err){
    setLive('error', '读取失败');
    console.error(err);
  }
}
function setLive(cls,text){$('liveDot').className=`live-dot ${cls}`;$('liveText').textContent=text;}

function startStream(){
  if(eventSource) eventSource.close();
  const q = new URLSearchParams({period: currentPeriod});
  if(currentDate) q.set('date', currentDate);
  eventSource = new EventSource(`/api/stream?${q}`);
  eventSource.addEventListener('snapshot', e=>{try{renderSnapshot(JSON.parse(e.data)); setLive('live','实时同步');}catch(err){console.error(err);}});
  eventSource.addEventListener('heartbeat', ()=>setLive('live','实时 · 无变化'));
  eventSource.addEventListener('error', ()=>setLive('error','重连中'));
}

function renderSnapshot(data){
  snapshot = data;
  renderOwners(data.owners || [], data.owner || {});
  $('schemaBadge').textContent = `schema ${data.meta?.schema_version ?? '--'}`;
  $('updatedAt').textContent = `更新：${fmtDateTime(data.updated_at)}`;
  $('dbInfo').innerHTML = `<b>DB</b><br>${esc(data.meta?.db_path || '--')}<br><br><b>目录</b><br>${esc(data.meta?.life_dir || '--')}<br><br><b>大小</b> ${(Number(data.meta?.size_bytes||0)/1024/1024).toFixed(2)} MB`;
  const av = data.avatar || {}; const st=data.state||{};
  $('ownerLabel').textContent = `${data.owner?.owner_kind || '--'} / ${data.owner?.owner_id || '--'}`;
  $('stateLabel').textContent = av.label || st.mode || '--';
  $('modeLabel').textContent = st.mode || '--';
  $('stateBadge').textContent = humanSprite(av.sprite_state);
  $('avatarBubble').textContent = av.bubble || '观察生活流';
  const sprite = av.sprite_state || 'idle';
  const img = $('avatarSprite');
  img.src = spriteFile(sprite); img.className = `pixel-agent ${sprite}`;
  renderCurrent(data.current_event, st);
  renderMeters(data.resources || [], st, data.sleep_day_state || {});
  renderSchedule(data.schedule || {});
  renderReview(data.review_items || []);
  renderPreviewLists(data.schedule?.items || [], data.review_items || []);
  renderEvents(data.recent_events || []);
  renderResources(data.resources || []);
  renderDreams(data.dreams || []);
  renderMessages(data.delayed_replies || [], data.proactive || {});
  renderCollections(data.collections || {});
  renderTrace(data.trace || []);
}
function renderOwners(owners, current){
  const sel=$('ownerSelect'); const cur=`${current.owner_kind}::${current.owner_id}`;
  sel.innerHTML = owners.map(o=>`<option value="${esc(o.owner_kind)}::${esc(o.owner_id)}" ${`${o.owner_kind}::${o.owner_id}`===cur?'selected':''}>${esc(o.owner_kind)} / ${esc(o.owner_id)}</option>`).join('');
}
function renderCurrent(ev, state){
  if(ev){
    $('currentEvent').innerHTML = `<div class="title">现在：${esc(ev.title || ev.id)}</div><div class="desc">${kindText(ev.event_category || ev.event_type)} · ${statusText(ev.status)} · ${esc(ev.activity_domain || '')}</div><div class="badges"><span class="badge">${esc(ev.event_category || ev.event_type || 'event')}</span><span class="badge status-${esc(ev.status)}">${statusText(ev.status)}</span>${ev.location?.name?`<span class="badge">${esc(ev.location.name)}</span>`:''}</div>`;
    $('currentEvent').onclick=()=>openEvent(ev.id);
  }else{
    $('currentEvent').innerHTML = `<div class="title">当前没有 active event</div><div class="desc">实时模式：${esc(state.mode || 'unknown')}。Agent 可能处于待机、恢复、或等待下一次 heartbeat。</div>`;
    $('currentEvent').onclick=null;
  }
}
function renderMeters(resources, state, sleepDay){
  const body = state.body_state || {};
  const byKey = Object.fromEntries((resources||[]).map(r=>[r.resource_key,r]));
  const entries = [
    ['energy','精力', byKey.energy?.current_value ?? body.energy, 100],
    ['focus','专注', byKey.focus?.current_value ?? (state.mind_state||{}).focus, 100],
    ['mood','心情', byKey.mood?.current_value ?? (state.mind_state||{}).mood, 100],
    ['fatigue','疲劳', byKey.fatigue?.current_value ?? body.fatigue ?? sleepDay.fatigue_delta, 100],
  ];
  $('meters').innerHTML = entries.map(([key,label,value,max])=>{
    const n = num(value); const width = key==='mood' ? pct((n??0)+100,200) : pct(n,max);
    return `<div class="metric"><span>${label}</span><b>${n==null?'--':Math.round(n)}</b><div class="meter-bar"><i style="width:${width}%"></i></div></div>`;
  }).join('');
  $('sleepDebt').textContent = `${sleepDay.cumulative_sleep_debt_minutes ?? body.sleep_debt_minutes ?? 0} min`;
  $('recoveryPressure').textContent = `${sleepDay.recovery_pressure ?? body.recovery_pressure ?? 0}`;
  $('delayedCount').textContent = String((snapshot?.delayed_replies || []).filter(x=>x.status!=='released').length);
  $('dreamCount').textContent = String((snapshot?.dreams || []).length);
}
function renderSchedule(schedule){
  $('scheduleLabel').textContent = schedule.label || '日程';
  const items = schedule.items || [];
  if(!items.length){$('timeline').innerHTML='<div class="empty">这个时间范围没有日程。Agent 可能处于空闲、未规划，或 heartbeat 尚未生成计划。</div>';return;}
  $('timeline').innerHTML = items.map((it,idx)=>{
    const title = it.event_title || it.title || it.block_type || '未命名日程';
    const type = it.event_category || it.event_type || it.block_type;
    const actual = (it.actual_start || it.actual_end) ? `实际 ${fmtRange(it.actual_start,it.actual_end)}` : '';
    const loc = it.location?.name || '';
    const intr = it.interruptibility?.level || it.event_interruptibility?.level || '';
    return `<div class="timeline-item" data-event="${esc(it.event_id||'')}"><div class="time-col">${fmtTime(it.start)}<span>${fmtTime(it.end)}</span></div><div class="timeline-content"><div class="item-title">${idx+1}. ${esc(title)}</div><div class="item-desc">${esc(kindText(type))}${actual?` · ${esc(actual)}`:''}${loc?` · ${esc(loc)}`:''}</div><div class="badges"><span class="badge">${esc(it.block_type||'block')}</span><span class="badge status-${esc(it.status)}">${statusText(it.status)}</span><span class="badge status-${esc(it.event_status)}">事件：${statusText(it.event_status)}</span>${intr?`<span class="badge">中断：${esc(intr)}</span>`:''}</div></div></div>`;
  }).join('');
  document.querySelectorAll('.timeline-item').forEach(el=>el.onclick=()=>{const id=el.dataset.event;if(id)openEvent(id);});
}
function renderPreviewLists(scheduleItems, reviewItems){
  const next = scheduleItems.slice(0,5);
  $('todayPreview').innerHTML = next.length ? next.map(x=>`<div class="mini-item"><b>${fmtRange(x.start,x.end)}</b><br>${esc(x.event_title||x.block_type||'日程')}<div class="badges"><span class="badge status-${esc(x.status)}">${statusText(x.status)}</span></div></div>`).join('') : '<div class="empty">暂无日程。</div>';
  $('reviewPreview').innerHTML = reviewItems.length ? reviewItems.slice(0,5).map(x=>`<div class="mini-item"><b>${esc(x.title)}</b><br><span class="subtle">${severityText(x.severity)} · ${esc(x.message||'')}</span></div>`).join('') : '<div class="empty">没有需要人类处理的项目。</div>';
}
function renderReview(items){
  if(!items.length){$('reviewList').innerHTML='<div class="empty">没有需要人类处理的项目。Agent 会按当前策略自行处理低风险维护项。</div>';return;}
  $('reviewList').innerHTML = items.map((x,i)=>`<div class="feed-item severity-${esc(x.severity || 'info')}"><b>${i+1}. ${esc(x.title||'Review item')}</b><div>${esc(x.message||'')}</div><div class="badges"><span class="badge">${severityText(x.severity)}</span><span class="badge">${esc(x.item_type||'item')}</span>${x.action_hint?.tool?`<span class="badge">建议：${esc(x.action_hint.tool)}.${esc(x.action_hint.action)}</span>`:''}</div></div>`).join('');
}
function renderCollections(collections){
  const board = collections.board || [];
  const presets = collections.outfit_presets || [];
  const el = $('collectionBoard');
  if(!board.length){el.innerHTML='<div class="empty">还没有集合。可以先初始化衣橱/鞋柜/袜子/配饰/梳妆台，或创建自定义集合。</div>';return;}
  const cards = board.map(b=>{
    const c=b.collection||{}; const its=b.items||[];
    const preview = its.slice(0,6).map(i=>`<div class="collection-item"><b>${esc(i.name||i.id)}</b><span>${esc(i.availability_state||'')} · ${esc(i.cleanliness_state||'')} · assets ${esc((i.asset_counts||{}).available||0)}/${esc((i.asset_counts||{}).total||0)}</span>${(i.aliases||[]).length?`<small>别名：${esc((i.aliases||[]).join('、'))}</small>`:''}</div>`).join('');
    return `<section class="collection-card"><div class="collection-head"><h3>${esc(c.name||c.collection_type)}</h3><span>${esc(c.collection_type||'custom')}</span></div><div class="collection-stats"><b>${b.item_count||0}</b> 件 · 可用 ${b.available_count||0} · 待补资产 ${b.needs_asset_count||0}</div>${preview||'<div class="empty">暂无 item。</div>'}</section>`;
  }).join('');
  const presetHtml = presets.length ? `<section class="collection-card presets"><div class="collection-head"><h3>穿搭预设</h3><span>outfit presets</span></div>${presets.map(p=>`<div class="collection-item"><b>${esc(p.name)}</b><span>${esc(p.occasion||'daily')} · alias ${(p.aliases||[]).map(esc).join('、')}</span></div>`).join('')}</section>` : '';
  el.innerHTML = cards + presetHtml;
}

function renderEvents(items){
  if(!items.length){$('eventList').innerHTML='<div class="empty">暂无近期事件。</div>';return;}
  $('eventList').innerHTML = items.slice(0,36).map((e,i)=>`<div class="feed-item clickable" data-event="${esc(e.id)}"><b>${i+1}. ${esc(e.title || e.id)}</b><div>${kindText(e.event_category || e.event_type)} · ${statusText(e.status)} · ${fmtDateTime(e.updated_at)}</div><div class="badges"><span class="badge">${esc(e.event_category || e.event_type || 'event')}</span><span class="badge status-${esc(e.status)}">${statusText(e.status)}</span></div></div>`).join('');
  document.querySelectorAll('#eventList .feed-item').forEach(el=>el.onclick=()=>openEvent(el.dataset.event));
}
function renderResources(items){
  if(!items.length){$('resources').innerHTML='<div class="empty">暂无资源。</div>';return;}
  $('resources').innerHTML = items.slice(0,18).map(r=>`<div class="res-item"><span>${esc(r.display_name || r.resource_key)}</span><b>${esc(r.current_value)} ${esc(r.unit || '')}</b><small>${esc(r.resource_class || '')}${r.capacity?` · cap ${esc(r.capacity)}`:''}</small></div>`).join('');
}
function renderDreams(items){
  const el=$('dreamsList');
  if(!items.length){el.innerHTML='<div class="empty">还没有梦境记录。</div>';return;}
  el.innerHTML = items.map(d=>`<div class="dream-card" data-dream="${esc(d.id)}"><b>${esc(d.title || '梦境')}</b><p>${esc(d.summary || d.share_text || d.content || '').slice(0,220)}</p><div class="badges"><span class="badge">${esc(d.truth_layer || 'dream_symbolic')}</span><span class="badge">${esc(d.emotional_tone || '')}</span></div></div>`).join('');
  document.querySelectorAll('.dream-card').forEach(el=>el.onclick=()=>openDream(el.dataset.dream));
}
function renderMessages(delayed, pro){
  const pending=(delayed||[]).filter(x=>x.status!=='released').map(x=>`<div class="feed-item"><b>延迟回复</b><div>${esc(x.message_text||'').slice(0,160)}</div><div class="badges"><span class="badge status-${esc(x.status)}">${statusText(x.status)}</span><span class="badge">${fmtDateTime(x.created_at)}</span></div></div>`);
  const intents=((pro&&pro.intents)||[]).slice(0,8).map(x=>`<div class="feed-item"><b>主动意图</b><div>${esc(x.summary||'').slice(0,160)}</div><div class="badges"><span class="badge status-${esc(x.status)}">${statusText(x.status)}</span></div></div>`);
  $('messages').innerHTML = pending.concat(intents).join('') || '<div class="empty">暂无延迟回复或主动意图。</div>';
}
function renderTrace(items){
  const el=$('traceList');
  if(!items.length){el.innerHTML='<div class="empty">暂无流水。</div>';return;}
  el.innerHTML = items.map(t=>`<div class="trace-item" data-trace="${esc(t.transaction_id || t.id)}"><b>${esc(t.entry_type||'journal')}</b> · ${fmtDateTime(t.created_at)}<br><span class="subtle">${esc(t.owner_kind)}/${esc(t.owner_id)} · ${esc(t.source||'')}</span></div>`).join('');
  document.querySelectorAll('.trace-item').forEach(el=>el.onclick=()=>openTrace(el.dataset.trace));
}

function openDrawer(kind,title,html){$('drawerKind').textContent=kind;$('drawerTitle').textContent=title;$('drawerBody').innerHTML=html;$('drawer').classList.add('open');$('drawer').setAttribute('aria-hidden','false');}
function closeDrawer(){$('drawer').classList.remove('open');$('drawer').setAttribute('aria-hidden','true');}
function rows(items, render){return items&&items.length?`<div class="table-lite">${items.map(render).join('')}</div>`:'<div class="empty">无记录。</div>';}
function detailEventHtml(d){
  if(!d.found) return '<div class="empty">找不到这个 Event。</div>';
  const e=d.event;
  return `<div class="detail-section"><h3>事件摘要</h3>${kv('标题',e.title)}${kv('状态',statusText(e.status))}${kv('分类',`${kindText(e.event_category||e.event_type)} / ${e.event_type||''} / ${e.activity_domain||''}`)}${kv('计划',`${fmtDateTime(e.planned_start)} - ${fmtDateTime(e.planned_end)}`)}${kv('实际',`${fmtDateTime(e.actual_start)} - ${fmtDateTime(e.actual_end)}`)}${kv('重要度',e.importance)}${kv('地点',e.location?.name || '')}</div>
  <div class="detail-section"><h3>状态流转</h3>${rows(d.transitions, t=>`<div class="table-row"><b>${statusText(t.from_status)} → ${statusText(t.to_status)}</b><br><span class="subtle">${fmtDateTime(t.occurred_at)} · ${esc(t.reason||t.source||'')}</span></div>`)}</div>
  <div class="detail-section"><h3>日程块</h3>${rows(d.schedule_blocks, s=>`<div class="table-row"><b>${fmtDateTime(s.start)} - ${fmtDateTime(s.end)}</b> · ${statusText(s.status)}<br><span class="subtle">实际 ${fmtDateTime(s.actual_start)} - ${fmtDateTime(s.actual_end)} · ${esc(s.block_type)}</span></div>`)}</div>
  <div class="detail-section"><h3>结果 / 资源变化</h3>${rows((d.results||[]).concat(d.resource_ledger||[]), r=>`<div class="table-row"><b>${esc(r.summary || r.resource_key || r.result_type || r.operation)}</b><br><span class="subtle">${esc(r.delta!=null?('delta '+r.delta):'')} ${esc(r.reason||'')} ${fmtDateTime(r.created_at)}</span></div>`)}</div>
  <div class="detail-section"><h3>睡眠/执行调整</h3>${rows(d.execution_sleep_adjustments, a=>`<div class="table-row"><b>${esc(a.adjustment_type)}</b> · ${esc(a.severity)}<br><span class="subtle">${esc(a.reason||'')}</span></div>`)}</div>
  <div class="detail-section"><h3>Journal 引用</h3>${rows(d.journal, j=>`<div class="table-row"><b>${esc(j.entry_type)}</b><br><span class="subtle">${esc(j.id)} · ${fmtDateTime(j.created_at)}</span></div>`)}</div>${jsonBlock(d)}`;
}
function detailDreamHtml(d){
  if(!d.found) return '<div class="empty">找不到这个梦。</div>';
  const x=d.dream;
  return `<div class="detail-section"><h3>梦境</h3>${kv('标题',x.title)}${kv('情绪',x.emotional_tone)}${kv('Truth Layer',x.truth_layer)}${kv('创建时间',fmtDateTime(x.created_at))}<p>${esc(x.content||x.summary||'')}</p></div><div class="detail-section"><h3>醒来分享文本</h3><p>${esc(x.share_text||'')}</p></div><div class="detail-section"><h3>Dream Runs</h3>${rows(d.runs, r=>`<div class="table-row"><b>${esc(r.status)} / ${esc(r.run_type)}</b><br><span class="subtle">${fmtDateTime(r.started_at)} - ${fmtDateTime(r.completed_at)}</span></div>`)}</div><div class="detail-section"><h3>Audit Findings</h3>${rows(d.findings, f=>`<div class="table-row"><b>${esc(f.finding_type||f.type)}</b> · ${esc(f.status||'')}<br><span class="subtle">${esc(f.message||f.reason||'')}</span></div>`)}</div>${jsonBlock(d)}`;
}
function detailTraceHtml(d){
  if(!d.found) return `<div class="detail-section"><h3>未找到</h3><p>没有找到对应 trace 或对象。</p></div>${jsonBlock(d)}`;
  if(d.kind==='event') return detailEventHtml(d);
  if(d.kind==='dream') return detailDreamHtml(d);
  const title = d.kind==='transaction' ? '事务' : d.kind==='journal' ? 'Journal' : 'Trace';
  let html = `<div class="detail-section"><h3>${title}</h3>${kv('ID', d.id)}${kv('类型', d.kind)}</div>`;
  if(d.transaction) html += `<div class="detail-section"><h3>Transaction</h3>${kv('状态',d.transaction.status)}${kv('来源',d.transaction.source)}${kv('创建',fmtDateTime(d.transaction.created_at))}</div>`;
  if(d.ops) html += `<div class="detail-section"><h3>LifeOps</h3>${rows(d.ops, op=>`<div class="table-row"><b>${esc(op.op_type)}</b><br><span class="subtle">${esc(op.status)} · ${fmtDateTime(op.created_at)}</span></div>`)}</div>`;
  if(d.receipts) html += `<div class="detail-section"><h3>Receipts</h3>${rows(d.receipts, r=>`<div class="table-row"><b>${esc(r.id)}</b><br><span class="subtle">${(r.facts||[]).map(f=>f.claim).join('；')}</span></div>`)}</div>`;
  return html + jsonBlock(d);
}
async function openEvent(id){ if(!id) return; const d=await api(`/api/event/${encodeURIComponent(id)}`); openDrawer('event', d.event?.title || id, detailEventHtml(d)); }
async function openDream(id){ if(!id) return; const d=await api(`/api/dream/${encodeURIComponent(id)}`); openDrawer('dream', d.dream?.title || id, detailDreamHtml(d)); }
async function openTrace(id){ if(!id) return; const d=await api(`/api/trace/explain/${encodeURIComponent(id)}`); openDrawer(d.kind||'trace', id, detailTraceHtml(d)); }

async function selectPath(){const path=$('pathInput').value.trim();if(!path)return;const out=await api('/api/select',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path})});if(out.owners&&out.owners.length){await api('/api/owner',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(out.selected_owner)});}startStream();await refresh();}
async function selectOwner(){const [owner_kind,owner_id]=$('ownerSelect').value.split('::');await api('/api/owner',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({owner_kind,owner_id})});startStream();await refresh();}
async function action(name,payload={}){const out=await api('/api/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:name,payload})});if(!out.ok&&out.message)alert(out.message);if(out.error)alert(out.error);await refresh();}
function showView(id){document.querySelectorAll('.view-section').forEach(x=>x.classList.toggle('active',x.id===id));document.querySelectorAll('.view-tabs button').forEach(b=>b.classList.toggle('active',b.dataset.target===id));}

document.addEventListener('DOMContentLoaded',()=>{
  $('selectBtn').onclick=selectPath;$('ownerSelect').onchange=selectOwner;$('refreshBtn').onclick=refresh;$('drawerClose').onclick=closeDrawer;
  $('callBtn').onclick=()=>action('call',{message_text:'WebUI call'});$('tickBtn').onclick=()=>action('tick',{});$('recoveryBtn').onclick=()=>action('sleep_recovery_plan',{});$('applySafeBtn').onclick=()=>action('review_apply_all',{limit:5});
  document.querySelectorAll('.view-tabs button').forEach(btn=>btn.onclick=()=>showView(btn.dataset.target));
  document.querySelectorAll('[data-jump]').forEach(btn=>btn.onclick=()=>showView(btn.dataset.jump));
  document.querySelectorAll('.period-controls button').forEach(btn=>btn.onclick=()=>{document.querySelectorAll('.period-controls button').forEach(b=>b.classList.remove('active'));btn.classList.add('active');currentPeriod=btn.dataset.period;currentDate=null;startStream();refresh();});
  $('dateInput').onchange=(e)=>{currentPeriod='day';currentDate=e.target.value;document.querySelectorAll('.period-controls button').forEach(b=>b.classList.remove('active'));startStream();refresh();};
  startStream();refresh();
});
