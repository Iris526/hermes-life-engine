const $ = (q, root=document) => root.querySelector(q);
const $$ = (q, root=document) => Array.from(root.querySelectorAll(q));

let SNAP = null;
let PERIOD = 'today';
let SELECTED_DATE = null;
let EVENT_SRC = null;

const api = async (url, options={}) => {
  const res = await fetch(url, options);
  if (!res.ok) throw new Error(await res.text());
  return await res.json();
};

const esc = (v) => String(v ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));
const short = (v, n=80) => { const s = String(v ?? ''); return s.length > n ? s.slice(0,n-1)+'…' : s; };
const fmtTime = (v) => {
  if (!v) return '??:??';
  const s = String(v);
  const m = s.match(/T(\d\d:\d\d)/) || s.match(/\s(\d\d:\d\d)/);
  return m ? m[1] : s.slice(0,16);
};
const statusCN = (s) => ({planned:'已排期',scheduled:'已排期',in_progress:'进行中',completed:'完成',partial:'部分完成',postponed:'推迟',rescheduled:'改期',cancelled:'取消',missed:'错过',ready:'就绪'}[s] || s || '未知');
const categoryCN = (s) => ({work:'工作',study:'学习',sleep:'睡眠',meal:'吃饭',leisure:'休闲',health:'健康',travel:'外出',purchase:'购物',social:'社交',dream:'梦',serendipity:'小发现',maintenance:'维护',creative:'创作'}[s] || s || '事件');

function statValue(resources, key, fallback=0){
  const r = (resources||[]).find(x => x.resource_key === key || x.resource_key === key.replace('_','.'));
  return Number(r?.current_value ?? fallback);
}
function pct(v, min=0, max=100){ return Math.max(0, Math.min(100, Math.round((Number(v)-min)/(max-min)*100))); }
function bar(label, value, max=100, danger=false){
  const p = pct(value,0,max);
  return `<div class="stat"><span>${esc(label)}</span><div class="bar"><span style="width:${p}%;${danger?'background:linear-gradient(90deg,#ff5d73,#ffb86b)':''}"></span></div><b>${esc(Math.round(value))}</b></div>`;
}

function spriteFor(data){
  const a = data?.avatar || {}; return a.sprite || 'idle';
}
function spritePath(sprite){ return `/static/assets/sprite-${sprite}.png`; }

async function refresh(period=PERIOD, date=SELECTED_DATE){
  PERIOD = period || PERIOD; SELECTED_DATE = date ?? SELECTED_DATE;
  const params = new URLSearchParams({period: PERIOD});
  if (SELECTED_DATE) params.set('date', SELECTED_DATE);
  SNAP = await api(`/api/snapshot?${params}`);
  renderAll();
}

function renderAll(){
  if (!SNAP) return;
  $('#boot-screen')?.classList.add('hide');
  renderTop(); renderStage(); renderStats(); renderSchedule(); renderWindows();
}
function renderTop(){
  const owner = SNAP.owner || {};
  $('#agent-name').textContent = owner.owner_id || 'Agent';
  $('#life-path').value = SNAP.meta?.db_path || '';
}
function renderStage(){
  const st = SNAP.state || {}; const ev = SNAP.current_event || {}; const av = SNAP.avatar || {};
  const sprite = spriteFor(SNAP);
  const img = $('#agent-sprite');
  img.src = spritePath(sprite); img.className = `agent-sprite ${sprite}`;
  $('#scene-title').textContent = ev.title || av.label || modeLabel(st.mode) || '待机中';
  $('#speech-bubble').textContent = av.bubble || ev.title || '今天要做什么，由 LifeEngine 自己安排。';
  $('#scene-tags').innerHTML = [st.mode, ev.event_category, ev.event_type, ev.status].filter(Boolean).map(x=>`<span class="tag">${esc(x)}</span>`).join('');
  $('#stage-status-row').innerHTML = [
    ['当前模式', modeLabel(st.mode)],
    ['活动事件', ev.title || '无 active event'],
    ['回复模式', st.reply_mode || 'immediate'],
  ].map(([k,v])=>`<div class="status-card"><b>${esc(k)}</b><span>${esc(v)}</span></div>`).join('');
}
function modeLabel(m){ return ({idle:'待机',awake:'清醒',busy:'忙碌',in_conversation:'回消息',asleep:'睡觉',napping:'小憩',dreaming:'做梦',uninterruptible_event:'不可打断',waiting_to_reply:'待回复',recovering:'恢复中'}[m] || m || '未知'); }
function renderStats(){
  const res = SNAP.resources || []; const sd = SNAP.sleep_day_state || {}; const body = SNAP.state?.body_state || {};
  const energy = statValue(res,'energy', body.energy ?? 50);
  const focus = statValue(res,'focus', body.focus ?? 50);
  const mood = statValue(res,'mood', body.mood ?? 50);
  const fatigue = statValue(res,'fatigue', body.fatigue ?? sd.fatigue_delta ?? 0);
  const debt = statValue(res,'sleep_debt_minutes', sd.cumulative_sleep_debt_minutes ?? 0);
  $('#stat-stack').innerHTML = [bar('精力',energy),bar('专注',focus),bar('心情',mood),bar('疲劳',fatigue,100,true),bar('睡眠债',Math.min(debt,300),300,true)].join('');
  const logs = [];
  if (SNAP.review_items?.length) logs.push(`Review: ${SNAP.review_items.length} 项`);
  if (SNAP.delayed_replies?.length) logs.push(`延迟回复: ${SNAP.delayed_replies.length} 条`);
  if (sd?.all_nighter) logs.push('昨晚通宵 / 睡眠不足');
  if (!logs.length) logs.push('状态稳定，等待下一次心跳。');
  $('#mini-log').innerHTML = logs.map(x=>`<div>◆ ${esc(x)}</div>`).join('');
}
function renderSchedule(){
  const items = SNAP.schedule?.items || [];
  $('#schedule-list').innerHTML = items.length ? items.map(item => `
    <div class="quest" data-event="${esc(item.event_id||'')}" data-block="${esc(item.id||'')}">
      <div class="quest-time">${fmtTime(item.start)} - ${fmtTime(item.end)}</div>
      <div class="quest-title">${esc(item.event_title || item.title || item.block_type || '未命名时间块')}</div>
      <div class="quest-meta">${esc(categoryCN(item.event_category || item.block_type))} · 排期：${esc(statusCN(item.status))} · 事件：${esc(statusCN(item.event_status))}</div>
    </div>`).join('') : `<div class="muted">这个时间范围没有日程。可以用 /life schedule unscheduled 查看待排期事件。</div>`;
  $$('#schedule-list .quest').forEach(el => el.addEventListener('click', () => openEvent(el.dataset.event || el.dataset.block)));
}

function renderWindows(){
  renderCollections(); renderCloset(); renderReview(); renderDreams(); renderTrace(); renderSettings(); renderWorkspace();
}
function renderCollections(){
  const board = SNAP.collections?.board || [];
  $('#collection-board').innerHTML = board.length ? board.map(b => {
    const c=b.collection||{}; const items=b.items||[];
    return `<div class="collection-card"><h3>${esc(c.name || c.collection_type)}</h3>
      <div class="muted">${esc(c.collection_type)} · ${b.item_count||0} 件 · 可用 ${b.available_count||0} · 待补资产 ${b.needs_asset_count||0}</div>
      <div class="item-list">${items.slice(0,8).map(i=>`<div class="item-row" data-item="${esc(i.id)}"><span>${esc(i.name)}</span><span>${esc(i.availability_state||i.status||'')}</span></div>`).join('')}</div>
    </div>`;
  }).join('') : `<div class="muted">还没有集合。可以用 /life closet init 初始化衣橱、鞋柜、袜子抽屉、配饰柜、梳妆台。</div>`;
}
function renderCloset(){
  const items = SNAP.collections?.items || [];
  $('#closet-board').innerHTML = items.length ? items.map(i => `
    <div class="item-card" data-item="${esc(i.id)}"><h3>${esc(i.name)}</h3>
      <div class="muted">${esc(i.collection_name || i.collection_type)} · ${esc(i.item_type || '')} · ${esc(i.availability_state || '')} · ${esc(i.cleanliness_state || '')}</div>
      <div>${(i.aliases||[]).map(a=>`<span class="pill">${esc(a)}</span>`).join('')}</div>
      <div class="muted">资产：${i.asset_counts?.available || 0}/${i.asset_counts?.total || 0} 可用</div>
    </div>`).join('') : `<div class="muted">衣橱/柜子为空。</div>`;
}
function renderReview(){
  const items = SNAP.review_items || [];
  $('#review-list').innerHTML = items.length ? items.map(i=>`<div class="review-card"><span class="pill">${esc(i.severity||'info')}</span><span class="pill">${esc(i.item_type||'review')}</span><h3>${esc(i.title||'待处理')}</h3><p>${esc(i.message||'')}</p></div>`).join('') : `<div class="muted">没有需要人类处理的项目。Agent 会按策略自行处理低风险维护项。</div>`;
}
function renderDreams(){
  const items = SNAP.dreams || [];
  $('#dream-list').innerHTML = items.length ? items.map(d=>`<div class="dream-card" data-dream="${esc(d.id)}"><span class="pill">dream_symbolic</span><h3>${esc(d.title||'梦境')}</h3><p>${esc(short(d.summary||d.content,180))}</p></div>`).join('') : `<div class="muted">还没有梦境记录。</div>`;
  $$('#dream-list .dream-card').forEach(el=>el.addEventListener('click',()=>openDream(el.dataset.dream)));
}
function renderTrace(){
  const items = SNAP.trace || [];
  $('#trace-list').innerHTML = items.length ? items.map(t=>`<div class="trace-card" data-trace="${esc(t.id)}"><span class="pill">${esc(t.entry_type||t.run_type||'trace')}</span><b>${esc(t.id)}</b><div class="muted">${esc(t.created_at||'')}</div></div>`).join('') : `<div class="muted">暂无 trace。</div>`;
  $$('#trace-list .trace-card').forEach(el=>el.addEventListener('click',()=>openTrace(el.dataset.trace)));
}
function renderSettings(){
  const c = SNAP.control || {}; const meta = SNAP.meta || {}; const req = SNAP.required_settings || SNAP.control?.required_settings || {};
  $('#settings-panel').innerHTML = [
    ['Engine', c.engine_state], ['Schema', meta.schema_version], ['DB', meta.db_path], ['Heartbeat', c.heartbeat_mode], ['Context', (c.module_gates||{}).context_mode || 'slim'], ['Required settings', req.ok ? 'OK' : 'Needs setup']
  ].map(([k,v])=>`<div class="status-card"><b>${esc(k)}</b><span>${esc(v ?? '—')}</span></div>`).join('') + `<p class="muted">设定修改请走 /life setup、/life config 或 life_config。WebUI 只显示可读状态，不直接绕过 CanonDraft。</p>`;
}
async function renderWorkspace(){
  const box = $('#workspace-list');
  try{
    const data = await api('/api/workspace/docs?limit=80');
    box.innerHTML = data.docs?.length ? data.docs.map(d=>`<div class="doc-card" data-path="${esc(d.path)}"><b>${esc(d.name)}</b><div class="muted">${esc(d.root_label)} / ${esc(d.relative_path)} · ${Math.round((d.size_bytes||0)/1024)}KB</div></div>`).join('') : `<div class="muted">未找到 SOUL.md / AGENT.md / README.md 等 markdown 文档。</div>`;
    $$('#workspace-list .doc-card').forEach(el=>el.addEventListener('click',()=>openWorkspaceFile(el.dataset.path)));
  }catch(e){ box.innerHTML = `<div class="danger-text">${esc(e.message)}</div>`; }
}

async function openWorkspaceFile(path){
  try{ const d=await api(`/api/workspace/file?path=${encodeURIComponent(path)}`); $('#workspace-view').textContent = d.content || ''; }
  catch(e){ $('#workspace-view').textContent = e.message; }
}
async function openEvent(id){ if(!id) return; const d=await api(`/api/event/${encodeURIComponent(id)}`); openDrawer('EVENT', d.event?.title || id, renderDetail(d)); }
async function openDream(id){ if(!id) return; const d=await api(`/api/dream/${encodeURIComponent(id)}`); openDrawer('DREAM', d.dream?.title || id, renderDetail(d)); }
async function openTrace(id){ if(!id) return; const d=await api(`/api/trace/explain/${encodeURIComponent(id)}`); openDrawer('TRACE', id, renderDetail(d)); }
function renderDetail(d){
  const rows=[];
  const target = d.event || d.dream || d.transaction || d.journal_entry || d;
  for(const [k,v] of Object.entries(target||{}).slice(0,24)){
    if(String(k).endsWith('_json') || typeof v === 'object') continue;
    rows.push(`<div class="kv"><b>${esc(k)}</b><span>${esc(v)}</span></div>`);
  }
  return rows.join('') + `<details open><summary>结构化上下文</summary><pre class="raw">${esc(JSON.stringify(d,null,2))}</pre></details>`;
}
function openDrawer(kind,title,html){ $('#drawer-kind').textContent=kind; $('#drawer-title').textContent=title; $('#drawer-body').innerHTML=html; $('#drawer').classList.add('open'); }

async function doAction(action,payload={}){
  const out = await api('/api/action',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({action,payload})});
  if(!out.ok) alert(out.message || out.error || '操作失败');
  await refresh();
}

function wire(){
  $('#select-path').addEventListener('click', async()=>{ const path=$('#life-path').value.trim(); if(!path)return; await api('/api/select',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({path})}); await refresh(); connectStream(); });
  $$('.period').forEach(b=>b.addEventListener('click',()=>{ $$('.period').forEach(x=>x.classList.remove('active')); b.classList.add('active'); SELECTED_DATE=null; refresh(b.dataset.period); connectStream(); }));
  $('#date-input').addEventListener('change', e=>{ $$('.period').forEach(x=>x.classList.remove('active')); SELECTED_DATE=e.target.value; refresh('day', SELECTED_DATE); connectStream(); });
  $$('.hotkey').forEach(b=>b.addEventListener('click',()=>{ $$('.hotkey').forEach(x=>x.classList.remove('active')); $$('.game-window').forEach(w=>w.classList.remove('active')); b.classList.add('active'); $('#window-'+b.dataset.tab).classList.add('active'); }));
  $$('.quick-actions [data-action]').forEach(b=>b.addEventListener('click',()=>doAction(b.dataset.action)));
  $('#drawer-close').addEventListener('click',()=>$('#drawer').classList.remove('open'));
}
function connectStream(){
  if(EVENT_SRC) EVENT_SRC.close();
  const params = new URLSearchParams({period:PERIOD}); if(SELECTED_DATE) params.set('date',SELECTED_DATE);
  EVENT_SRC = new EventSource(`/api/stream?${params}`);
  EVENT_SRC.addEventListener('snapshot', ev=>{ SNAP=JSON.parse(ev.data); $('#live-pill').textContent='live'; renderAll(); });
  EVENT_SRC.addEventListener('heartbeat', ev=>{ $('#live-pill').textContent='live · no changes'; });
  EVENT_SRC.addEventListener('error', ev=>{ $('#live-pill').textContent='reconnecting'; });
}

wire(); refresh().then(connectStream).catch(e=>{ $('#boot-screen')?.classList.add('hide'); alert(e.message); });
