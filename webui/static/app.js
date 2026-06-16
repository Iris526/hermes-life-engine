/* ═══════════════════════════════════════════════════════════
   归明观 · 废土观星台 — 前端逻辑
   三栏布局 + hotbar 全屏面板 + 模块化渲染
   ═══════════════════════════════════════════════════════════ */
const API = "";
let snapshotData = null;
let collectionsData = null;
let activeOverlay = "stage";
let currentPeriod = "today";
let currentBagTab = null;
let codexDocs = [];
let reloadSerial = Date.now();

// ── 初始化 ────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  bindEvents();
  loadSnapshot();
});

function bindEvents() {
  document.getElementById("btn-refresh").onclick = () => loadSnapshot();
  document.getElementById("btn-reload").onclick = () => reloadInPage();
  document.getElementById("btn-tick").onclick = () => doAction("tick");
  // hotbar
  document.querySelectorAll(".hotbar-btn").forEach(btn => {
    btn.onclick = () => switchOverlay(btn.dataset.overlay);
  });
  // 面板关闭
  document.querySelectorAll("[data-close]").forEach(btn => {
    btn.onclick = () => switchOverlay("stage");
  });
  // 抽屉关闭
  document.querySelectorAll("[data-close-drawer]").forEach(btn => {
    btn.onclick = closeDrawer;
  });
  // 日程周期切换
  document.querySelectorAll(".period-btn").forEach(btn => {
    btn.onclick = () => {
      currentPeriod = btn.dataset.period;
      document.querySelectorAll(".period-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      loadSchedule();
    };
  });
}

// ── 数据拉取 ──────────────────────────────────
async function loadSnapshot(options = {}) {
  try {
    const res = await fetch(apiUrl("/api/snapshot", { period: currentPeriod }, options.force));
    snapshotData = await res.json();
    try { collectionsData = await (await fetch(apiUrl("/api/snapshot", { period: currentPeriod }, options.force))).json(); } catch {}
    // 如果是第一次加载,隐藏 loading
    document.getElementById("loading-screen").classList.add("hidden");
    document.getElementById("main-layout").classList.remove("hidden");
    render();
    connectSSE();
  } catch (err) {
    console.error("loadSnapshot error:", err);
  }
}

async function reloadInPage() {
  const btn = document.getElementById("btn-reload");
  const keepOverlay = activeOverlay;
  reloadSerial = Date.now();
  if (sseSource) { sseSource.close(); sseSource = null; }
  codexDocs = [];
  closeDrawer();
  reloadStylesheets();
  btn?.classList.add("loading");
  btn?.setAttribute("disabled", "disabled");
  try {
    await loadSnapshot({ force: true });
    switchOverlay(keepOverlay);
  } finally {
    btn?.classList.remove("loading");
    btn?.removeAttribute("disabled");
  }
}

async function loadCollections() {
  try {
    collectionsData = await (await fetch(apiUrl("/api/snapshot", {}, true))).json();
  } catch {}
}

async function loadSchedule() {
  if (!snapshotData) return;
  try {
    const res = await fetch(apiUrl("/api/schedule", { period: currentPeriod }));
    const data = await res.json();
    snapshotData.schedule = data;
    renderSchedule();
  } catch {}
}

let sseSource = null;
function connectSSE() {
  if (sseSource) return;
  try {
    sseSource = new EventSource(`${API}/api/stream?period=${currentPeriod}`);
    sseSource.addEventListener("snapshot", async (e) => {
      try {
        const data = JSON.parse(e.data);
        if (data.snapshot_hash !== snapshotData?.snapshot_hash) {
          snapshotData = data;
          try { collectionsData = await (await fetch(`${API}/api/snapshot`)).json(); } catch {}
          render();
        }
      } catch {}
    });
    sseSource.addEventListener("error", () => { sseSource = null; });
  } catch {}
}

// ── 渲染编排 ──────────────────────────────────
function render() {
  renderTopBar();
  renderSidebar();
  renderStage();
  renderSchedule();
  renderProactive();
  renderRecentEvents();
  // 全屏面板(按当前激活的)
  renderBag();
  renderCloset();
  renderDreams();
  renderReview();
  renderTrace();
  renderSettings();
}

// ── 顶栏 ──────────────────────────────────────
function renderTopBar() {
  const meta = snapshotData.meta || {};
  const owner = snapshotData.owner || {};
  const control = snapshotData.control || {};
  const state = snapshotData.state || {};
  document.getElementById("db-selector").textContent = meta.db_path ? meta.db_path.split("/").pop() : "—";
  document.getElementById("owner-tag").textContent = `${owner.owner_kind || "agent"}:${owner.owner_id || "—"}`;
  const engState = control.engine_state || "—";
  const chip = document.getElementById("engine-state-tag");
  chip.textContent = engState;
  chip.className = "state-chip" + (engState === "active" ? " active" : "");
}

// ── 左栏 ──────────────────────────────────────
function renderSidebar() {
  const control = snapshotData.control || {};
  const state = snapshotData.state || {};
  const avatar = snapshotData.avatar || {};
  // 立绘信息
  const owner = snapshotData.owner || {};
  document.getElementById("agent-portrait").src = staticAssetUrl("default-agent-pixel.png");
  document.getElementById("char-name").textContent = owner.owner_id || "—";
  document.getElementById("char-title").textContent = avatar.label || avatar.scene || state.mode || "—";

  // 状态网格
  const stateGrid = document.getElementById("state-grid");
  const stateItems = [
    ["模式", state.mode],
    ["体态", state.body_state?.state],
    ["心神", state.mind_state?.state],
    ["环境", state.environment_state?.state],
    ["回复", state.reply_mode],
    ["中断", state.interruptibility_level],
  ].filter(([, v]) => v);
  stateGrid.innerHTML = stateItems.map(([l, v]) =>
    `<div class="state-item"><span class="label">${l}</span><span class="val">${v}</span></div>`
  ).join("") || `<div class="state-item"><span class="label">—</span><span class="val">—</span></div>`;

  // 资源条
  const resources = snapshotData.resources || [];
  const vitals = resources.filter(r => ["energy", "focus", "mood", "fatigue"].includes(r.resource_key));
  const vitalsEl = document.getElementById("vital-bars");
  vitalsEl.innerHTML = vitals.map(r => {
    const pct = r.max_value != null && r.max_value > r.min_value
      ? Math.max(0, Math.min(100, ((r.current_value - (r.min_value||0)) / (r.max_value - (r.min_value||0))) * 100))
      : 50;
    const cls = pct < 25 ? "low" : pct > 75 ? "high" : "";
    return `<div class="vital-bar">
      <div class="vital-bar-head"><span class="name">${r.display_name || r.resource_key}</span><span class="num">${formatNum(r.current_value)}</span></div>
      <div class="vital-bar-track"><div class="vital-bar-fill ${cls}" style="width:${pct}%"></div></div>
    </div>`;
  }).join("") || '<div class="empty-state">无状态资源</div>';

  // 货币/物资
  const currencies = resources.filter(r => !["energy", "focus", "mood", "fatigue"].includes(r.resource_key));
  document.getElementById("currency-stats").innerHTML = currencies.map(r =>
    `<div class="currency-item"><span class="ckey">${r.display_name || r.resource_key}</span><span class="cval">${formatNum(r.current_value)}${r.unit ? " " + r.unit : ""}</span></div>`
  ).join("") || '<div class="empty-state">无财物</div>';

  // 睡眠指标
  const sleep = snapshotData.sleep_day_state || {};
  const sleepEl = document.getElementById("sleep-metrics");
  const sleepItems = [
    ["睡眠状态", sleep.body_state?.state],
    ["恢复压力", sleep.recovery_pressure],
    ["疲劳", sleep.body_state?.fatigue],
    ["睡眠债(min)", sleep.sleep_debt_minutes],
  ].filter(([, v]) => v != null);
  sleepEl.innerHTML = sleepItems.map(([l, v]) =>
    `<div class="sleep-item"><span class="label">${l}</span><span class="val">${v}</span></div>`
  ).join("") || '<div class="empty-state">无睡眠数据</div>';
}

// ── 中央舞台 ──────────────────────────────────
function renderStage() {
  const avatar = snapshotData.avatar || {};
  const state = snapshotData.state || {};
  const currentEvent = snapshotData.current_event;
  // sprite
  const spriteState = avatar.sprite_state || "idle";
  const sceneName = avatar.scene || "observatory";
  document.getElementById("sprite-img").src = staticAssetUrl(`sprite-${spriteState}.png`);
  const stageEl = document.getElementById("stage-scene");
  if (stageEl) stageEl.className = `stage-scene scene-${sceneName}`;;
  // 对话气泡
  const bubble = document.getElementById("speech-bubble");
  if (avatar.bubble) {
    bubble.textContent = avatar.bubble;
    bubble.classList.remove("hidden");
  } else {
    bubble.classList.add("hidden");
  }
  // 舞台事件
  const eventEl = document.getElementById("stage-event");
  if (currentEvent) {
    eventEl.innerHTML = `<div class="ev-title">${currentEvent.title}</div><div class="ev-sub">${currentEvent.event_category || ""} · ${currentEvent.status}</div>`;
    eventEl.style.cursor = "pointer";
    eventEl.onclick = () => showEventDetail(currentEvent.id);
  } else {
    eventEl.innerHTML = `<div class="ev-title">${avatar.label || "静候"}</div><div class="ev-sub">${avatar.scene || ""}</div>`;
    eventEl.onclick = null;
    eventEl.style.cursor = "default";
  }
  // meta
  const meta = snapshotData.meta || {};
  document.getElementById("stage-meta").textContent = `v${meta.schema_version || "?"} · ${snapshotData.snapshot_hash ? snapshotData.snapshot_hash.slice(0,8) : ""}`;
  // 延迟回复
  const delayed = snapshotData.delayed_replies || [];
  const replyEl = document.getElementById("reply-indicator");
  if (delayed.length > 0) {
    replyEl.textContent = `📨 ${delayed.length} 条待复`;
    replyEl.classList.remove("hidden");
  } else {
    replyEl.classList.add("hidden");
  }
}

// ── 右栏:日程 ─────────────────────────────────
function renderSchedule() {
  const schedule = snapshotData.schedule || {};
  document.getElementById("schedule-label").textContent = `日程·${schedule.label || currentPeriod}`;
  const items = schedule.items || [];
  const el = document.getElementById("schedule-list");
  if (!items.length) {
    el.innerHTML = '<div class="empty-state">无日程</div>';
    return;
  }
  el.innerHTML = items.slice(0, 20).map(it => {
    const cls = it.status === "completed" ? "completed" : it.status === "active" || it.status === "in_progress" ? "active" : it.is_sleep ? "sleep" : "";
    const evTitle = it.event_title || it.title || "—";
    return `<div class="quest-card ${cls}" onclick="showScheduleDetail('${it.event_id || ""}')">
      <div class="quest-time">${formatTime(it.start)}—${formatTime(it.end)}</div>
      <div class="quest-title">${evTitle}${it.is_sleep ? " 🌙" : ""}</div>
      <div class="quest-sub">${it.status}${it.event_category ? " · " + it.event_category : ""}</div>
    </div>`;
  }).join("");
}

// ── 右栏:主动消息 ─────────────────────────────
function renderProactive() {
  const proactive = snapshotData.proactive || {};
  const intents = proactive.intents || [];
  const outbox = proactive.outbox || [];
  const all = [
    ...intents.map(i => ({...i, _t: "intent"})),
    ...outbox.map(o => ({...o, _t: "outbox", intent_type: o.intent_type || "outbox", summary: o.summary || o.message_text})),
  ];
  const el = document.getElementById("proactive-list");
  if (!all.length) {
    el.innerHTML = '<div class="empty-state">无待发讯息</div>';
    return;
  }
  el.innerHTML = all.slice(0, 10).map(item =>
    `<div class="proactive-card ${item._t === "outbox" ? "queued" : ""}">
      <div class="pi-type">${item.intent_type || item._t}</div>
      <div class="pi-summary">${(item.summary || "").slice(0, 60)}</div>
    </div>`
  ).join("");
}

// ── 右栏:最近事件 ─────────────────────────────
function renderRecentEvents() {
  const events = snapshotData.recent_events || [];
  const el = document.getElementById("recent-events");
  if (!events.length) {
    el.innerHTML = '<div class="empty-state">无近期事件</div>';
    return;
  }
  el.innerHTML = events.slice(0, 12).map(e => {
    const cls = e.status === "completed" ? "completed" : "";
    return `<div class="recent-event ${cls}" onclick="showEventDetail('${e.id}')">
      <div class="re-title">${e.title}</div>
      <div class="re-meta"><span class="ev-status ${e.status}">${e.status}</span>${e.event_category ? " · " + e.event_category : ""}</div>
    </div>`;
  }).join("");
}

// ── 背包面板 ──────────────────────────────────
function renderBag() {
  const board = collectionsData?.collections?.board || snapshotData?.collections?.board || [];
  if (!board.length) {
    document.getElementById("bag-tabs").innerHTML = "";
    document.getElementById("bag-grid").innerHTML = '<div class="empty-state">物品柜为空</div>';
    return;
  }
  if (!currentBagTab) currentBagTab = board[0]?.collection?.id;
  // tabs
  document.getElementById("bag-tabs").innerHTML = board.map(b => {
    const c = b.collection;
    return `<button class="sub-tab ${c.id === currentBagTab ? "active" : ""}" onclick="switchBagTab('${c.id}')">${c.name} (${b.item_count})</button>`;
  }).join("");
  // grid
  const entry = board.find(b => b.collection?.id === currentBagTab);
  const items = entry?.items || [];
  const grid = document.getElementById("bag-grid");
  if (!items.length) {
    grid.innerHTML = '<div class="empty-state">此柜为空</div>';
    return;
  }
  grid.innerHTML = items.map(item => {
    const img = getItemPrimaryImage(item);
    const badges = [];
    if (item.status === "active") badges.push('<span class="item-badge active">可用</span>');
    if (item.attributes?.is_consumable) badges.push('<span class="item-badge consumable">耗</span>');
    if (item.cleanliness_state === "dirty" || item.cleanliness_state === "laundry") badges.push('<span class="item-badge laundry">待洗</span>');
    if (item.usage_state?.checkout_for?.length) badges.push('<span class="item-badge used">在用</span>');
    return `<div class="item-card" onclick="showItemDetail('${item.id}')">
      ${img ? `<img class="item-card-img" src="${assetPreviewUrl(img)}" decoding="async" onerror="this.outerHTML='<div class=\\'item-card-img placeholder\\'>◈</div>'">` : '<div class="item-card-img placeholder">◈</div>'}
      <div class="item-card-name">${item.name}</div>
      <div class="item-card-meta">
        ${item.quantity > 1 ? `<span>×${item.quantity}</span>` : ""}
        ${badges.join("")}
      </div>
    </div>`;
  }).join("");
}

function switchBagTab(id) { currentBagTab = id; renderBag(); }

// ── 衣柜面板 ──────────────────────────────────
function renderCloset() {
  const collections = collectionsData?.collections || snapshotData?.collections || {};
  const outfits = collections.outfits || [];
  const presets = collections.outfit_presets || [];
  // 当前穿搭
  const currentEl = document.getElementById("current-outfit");
  const current = outfits.find(o => o.status === "active") || outfits[0];
  if (current) {
    const itemIds = current.item_ids || [];
    currentEl.innerHTML = `<div class="outfit-preset-card"><span>${current.name || current.title || "当前"}</span><span style="color:var(--cyan)">${itemIds.length} 件</span></div>`;
  } else {
    currentEl.innerHTML = '<div class="outfit-empty">无当前穿搭</div>';
  }
  // 预设
  const presetEl = document.getElementById("outfit-presets");
  if (presets.length) {
    presetEl.innerHTML = presets.slice(0, 10).map(p =>
      `<div class="outfit-preset-card"><span>${p.name}</span><span style="color:var(--text-dim)">${(p.item_refs||[]).length} 件</span></div>`
    ).join("");
  } else {
    presetEl.innerHTML = '<div class="outfit-empty">无预设</div>';
  }
  // 历史
  const histEl = document.getElementById("outfit-history");
  if (outfits.length) {
    histEl.innerHTML = outfits.slice(0, 10).map(o =>
      `<div class="outfit-snapshot-card"><span>${o.name || o.title || "—"}</span><span style="color:var(--text-dim)">${o.status}</span></div>`
    ).join("");
  } else {
    histEl.innerHTML = '<div class="outfit-empty">无穿搭记录</div>';
  }
}

// ── 梦境面板 ──────────────────────────────────
function renderDreams() {
  const dreams = snapshotData.dreams || [];
  const el = document.getElementById("dreams-list");
  if (!dreams.length) {
    el.innerHTML = '<div class="empty-state">尚无梦境记录</div>';
    return;
  }
  el.innerHTML = dreams.slice(0, 20).map(d => {
    const sev = d.severity === "high" ? "severity-high" : d.severity === "warn" ? "severity-warn" : "";
    const symbols = (d.symbols || []).slice(0, 6);
    return `<div class="dream-card ${sev}" onclick="showDreamDetail('${d.id}')">
      <div class="dream-date">${formatTime(d.created_at)}</div>
      <div class="dream-content">${(d.content || d.summary || "—").slice(0, 200)}</div>
      ${symbols.length ? `<div class="dream-symbols">${symbols.map(s => `<span class="dream-symbol">${s}</span>`).join("")}</div>` : ""}
    </div>`;
  }).join("");
}

// ── 功法库面板 ────────────────────────────────
async function renderCodex() {
  try {
    const res = await fetch(`${API}/api/workspace/docs?limit=30`);
    const data = await res.json();
    codexDocs = data.docs || [];
  } catch { codexDocs = []; }
  const listEl = document.getElementById("codex-list");
  if (!codexDocs.length) {
    listEl.innerHTML = '<div class="empty-state">无典籍</div>';
    return;
  }
  listEl.innerHTML = codexDocs.map((d, i) =>
    `<div class="codex-item" onclick="loadDoc(${i})"><div>${d.name}</div><div class="doc-root">${d.root_label || ""}</div></div>`
  ).join("");
}

async function loadDoc(idx) {
  const doc = codexDocs[idx];
  if (!doc) return;
  document.querySelectorAll(".codex-item").forEach(el => el.classList.remove("active"));
  document.querySelectorAll(".codex-item")[idx]?.classList.add("active");
  const reader = document.getElementById("codex-reader");
  reader.innerHTML = '<p class="codex-hint">载入中...</p>';
  try {
    const res = await fetch(`${API}/api/workspace/file?path=${encodeURIComponent(doc.path)}`);
    const data = await res.json();
    reader.innerHTML = `<h1>${data.name}</h1><div style="font-size:10px;color:var(--text-dim);margin-bottom:12px">${doc.root_label || ""} · ${(data.size_bytes/1024).toFixed(1)}KB</div><div class="codex-md">${escapeMd(data.content || "")}</div>`;
  } catch {
    reader.innerHTML = '<p class="codex-hint">载入失败</p>';
  }
}

// ── 回顾面板 ──────────────────────────────────
function renderReview() {
  const reviews = snapshotData.review_items || [];
  const actionsEl = document.getElementById("review-actions");
  // 批量操作按钮
  actionsEl.innerHTML = reviews.length > 0
    ? `<button class="review-action-btn" onclick="doAction('review_apply_all', {section:'all', limit:5})">批量处理低风险</button>`
    : "";
  const listEl = document.getElementById("review-list");
  if (!reviews.length) {
    listEl.innerHTML = '<div class="empty-state">✓ 无待办事项</div>';
    return;
  }
  listEl.innerHTML = reviews.slice(0, 30).map((r, i) => {
    const sev = r.severity === "high" ? "severity-high" : r.severity === "warn" ? "severity-warn" : "severity-info";
    return `<div class="review-card ${sev}">
      <div class="rv-title">${r.title}</div>
      <div class="rv-meta"><span>${r.severity || ""}</span><span>${r.item_type || ""}</span></div>
      ${r.id ? `<div class="rv-actions">
        <button class="rv-act-btn accept" onclick="doAction('review_apply', {item_id:'${r.id}', choice:'accept'})">采纳</button>
        <button class="rv-act-btn dismiss" onclick="doAction('review_apply', {item_id:'${r.id}', choice:'dismiss'})">忽略</button>
      </div>` : ""}
    </div>`;
  }).join("");
}

// ── 追溯面板 ──────────────────────────────────
function renderTrace() {
  const trace = snapshotData.trace || [];
  const listEl = document.getElementById("trace-list");
  if (!trace.length) {
    listEl.innerHTML = '<div class="empty-state">无追溯记录</div>';
    return;
  }
  listEl.innerHTML = trace.slice(0, 30).map((t, i) =>
    `<div class="trace-item" onclick="showTraceDetail('${t.id}')">
      <div class="ti-type">${t.entry_type}</div>
      <div class="ti-time">${formatTime(t.created_at)}</div>
    </div>`
  ).join("");
}

async function showTraceDetail(id) {
  document.querySelectorAll(".trace-item").forEach(el => el.classList.remove("active"));
  event?.target?.closest?.(".trace-item")?.classList?.add("active");
  const detail = document.getElementById("trace-detail");
  detail.innerHTML = '<p class="trace-hint">解析中...</p>';
  try {
    const res = await fetch(`${API}/api/trace/explain/${id}`);
    const data = await res.json();
    const sections = [];
    if (data.kind) sections.push(`<div class="td-section"><div class="td-label">类型</div><div>${data.kind}</div></div>`);
    if (data.event) sections.push(`<div class="td-section"><div class="td-label">事件</div><pre>${JSON.stringify(data.event, null, 2)}</pre></div>`);
    if (data.transaction) sections.push(`<div class="td-section"><div class="td-label">事务</div><pre>${JSON.stringify(data.transaction, null, 2)}</pre></div>`);
    if (data.ops) sections.push(`<div class="td-section"><div class="td-label">操作 (${data.ops.length})</div><pre>${JSON.stringify(data.ops, null, 2)}</pre></div>`);
    if (data.receipt) sections.push(`<div class="td-section"><div class="td-label">收据</div><pre>${JSON.stringify(data.receipt, null, 2)}</pre></div>`);
    if (data.journal) sections.push(`<div class="td-section"><div class="td-label">日志 (${(data.journal||[]).length})</div><pre>${JSON.stringify(data.journal, null, 2)}</pre></div>`);
    detail.innerHTML = sections.join("") || `<p class="trace-hint">无详情</p>`;
  } catch {
    detail.innerHTML = '<p class="trace-hint">解析失败</p>';
  }
}

// ── 设置面板 ──────────────────────────────────
function renderSettings() {
  const control = snapshotData.control || {};
  const meta = snapshotData.meta || {};
  const doctor = snapshotData.doctor;
  // 引擎
  document.getElementById("settings-engine").innerHTML = `<h3>引擎状态</h3>
    ${kv("引擎状态", control.engine_state)}
    ${kv("Canon 版本", control.active_canon_version)}
    ${kv("心跳模式", control.heartbeat_mode)}
    ${kv("工作区", control.workspace)}`;
  // 模块门
  const gates = control.module_gates || {};
  const gatesHtml = Object.entries(gates).map(([k, v]) => {
    const cls = String(v).toLowerCase();
    const badgeCls = cls === "auto" || cls === "full" || cls === "true" ? "auto" : cls === "off" || cls === "false" ? "off" : cls === "manual" ? "manual" : "advisory";
    return `<div class="settings-row"><span class="sk">${k}</span><span class="gate-badge ${badgeCls}">${v}</span></div>`;
  }).join("");
  document.getElementById("settings-gates").innerHTML = `<h3>模块阵门</h3>${gatesHtml || '<div class="empty-state">无</div>'}`;
  // doctor
  if (doctor?.summary) {
    const s = doctor.summary;
    document.getElementById("settings-doctor").innerHTML = `<h3>健康检查</h3>
      ${kv("状态", s.status)}
      ${kv("Schema", "v" + (meta.schema_version || "?"))}
      ${kv("Hash 链", s.journal_hash_chain?.ok !== false ? "完整" : "异常")}`;
  } else {
    document.getElementById("settings-doctor").innerHTML = `<h3>健康检查</h3><div class="empty-state">无记录</div>`;
  }
  // meta
  document.getElementById("settings-meta").innerHTML = `<h3>数据源</h3>
    ${kv("DB", meta.db_path)}
    ${kv("Schema", "v" + (meta.schema_version || "?"))}
    ${kv("大小", meta.size_bytes ? (meta.size_bytes/1024).toFixed(1) + " KB" : "—")}
    ${kv("表数", (meta.tables || []).length)}`;
}

// ── 详情抽屉 ──────────────────────────────────
async function showEventDetail(id) {
  if (!id) return;
  openDrawer("事件详情");
  const body = document.getElementById("drawer-body");
  body.innerHTML = '<p class="empty-state">载入中...</p>';
  try {
    const res = await fetch(`${API}/api/event/${id}`);
    const data = await res.json();
    const ev = data.event || {};
    body.innerHTML = `<h4>${ev.title || id}</h4>
      <div class="desc">${ev.description || ""}</div>
      ${kv("类别", ev.event_category)}${kv("类型", ev.event_type)}${kv("状态", ev.status)}${kv("重要度", ev.importance)}
      ${kv("开始", formatTime(ev.planned_start))}${kv("结束", formatTime(ev.planned_end))}
      ${ev.resource_costs && Object.keys(ev.resource_costs).length ? `<h4>资源消耗</h4><pre>${JSON.stringify(ev.resource_costs, null, 2)}</pre>` : ""}
      ${data.transitions?.length ? `<h4>状态流转 (${data.transitions.length})</h4><pre>${JSON.stringify(data.transitions, null, 2)}</pre>` : ""}
      ${data.resource_ledger?.length ? `<h4>资源账本</h4><pre>${JSON.stringify(data.resource_ledger, null, 2)}</pre>` : ""}`;
  } catch { body.innerHTML = '<p class="empty-state">载入失败</p>'; }
}

async function showDreamDetail(id) {
  if (!id) return;
  openDrawer("梦境详情");
  const body = document.getElementById("drawer-body");
  body.innerHTML = '<p class="empty-state">载入中...</p>';
  try {
    const res = await fetch(`${API}/api/dream/${id}`);
    const data = await res.json();
    const d = data.dream || {};
    body.innerHTML = `<h4>${formatTime(d.created_at)}</h4>
      <div class="desc">${d.content || d.summary || ""}</div>
      ${d.symbols?.length ? `<h4>象征</h4><div>${d.symbols.map(s => `<span class="dream-symbol">${s}</span>`).join(" ")}</div>` : ""}
      ${data.findings?.length ? `<h4>审计发现 (${data.findings.length})</h4><pre>${JSON.stringify(data.findings, null, 2)}</pre>` : ""}`;
  } catch { body.innerHTML = '<p class="empty-state">载入失败</p>'; }
}

function showItemDetail(itemId) {
  const board = collectionsData?.collections?.board || snapshotData?.collections?.board || [];
  let item = null;
  for (const b of board) { const found = (b.items || []).find(i => i.id === itemId); if (found) { item = found; break; } }
  if (!item) return;
  openDrawer(item.name || "物品");
  const body = document.getElementById("drawer-body");
  const img = getItemPrimaryImage(item);
  const attrs = item.attributes || {};
  const attrItems = Object.entries(attrs).filter(([k]) => !["reference_image","presentation_board","reference_crop","primary_image","display_image"].includes(k));
  body.innerHTML = `${img ? `<img class="detail-img" src="${assetUrl(img)}" onerror="this.remove()">` : ""}
    <h4>${item.name}</h4>
    <div class="desc">${item.description || ""}</div>
    ${kv("状态", item.status)}${kv("数量", item.quantity)}${kv("清洁度", item.cleanliness_state)}
    ${item.aliases?.length ? `<h4>别名</h4><div>${item.aliases.map(a => `<span class="dream-symbol">${a}</span>`).join(" ")}</div>` : ""}
    ${attrItems.length ? `<h4>属性</h4>${attrItems.map(([k,v]) => kv(attrLabel(k), v)).join("")}` : ""}
    ${item.material_spec ? `<h4>材质</h4><pre>${JSON.stringify(item.material_spec, null, 2)}</pre>` : ""}
    ${item.tags?.length ? `<h4>标签</h4><div>${item.tags.map(t => `<span class="dream-symbol">${t}</span>`).join(" ")}</div>` : ""}`;
}

function showScheduleDetail(eventId) { if (eventId) showEventDetail(eventId); }

function openDrawer(title) {
  document.getElementById("drawer-title").textContent = title;
  document.getElementById("detail-drawer").classList.remove("hidden");
}
function closeDrawer() { document.getElementById("detail-drawer").classList.add("hidden"); }

// ── 面板切换 ──────────────────────────────────
function switchOverlay(name) {
  activeOverlay = name;
  document.querySelectorAll(".overlay-panel").forEach(p => p.classList.remove("active"));
  document.querySelectorAll(".hotbar-btn").forEach(b => b.classList.remove("active"));
  const panel = document.getElementById(`overlay-${name}`);
  if (panel) panel.classList.add("active");
  const btn = document.querySelector(`.hotbar-btn[data-overlay="${name}"]`);
  if (btn) btn.classList.add("active");
  closeDrawer();
  // 懒加载:功法库首次打开时加载
  if (name === "codex" && !codexDocs.length) renderCodex();
}

// ── 操作 ──────────────────────────────────────
async function doAction(action, payload = {}) {
  try {
    const res = await fetch(`${API}/api/action`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, payload }),
    });
    const data = await res.json();
    if (data.ok !== false) { loadSnapshot(); }
    else { console.warn("Action failed:", data); }
  } catch (err) { console.error("Action error:", err); }
}

// ── 辅助函数 ──────────────────────────────────
function formatTime(ts) {
  if (!ts) return "—";
  try {
    const d = new Date(ts);
    if (isNaN(d)) return String(ts).slice(11, 16) || String(ts).slice(0, 16);
    return d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
  } catch { return "—"; }
}

function formatNum(n) {
  if (n == null) return "—";
  if (Math.abs(n) >= 1000) return n.toLocaleString("zh-CN", { maximumFractionDigits: 1 });
  return Number(n).toFixed(n % 1 === 0 ? 0 : 1);
}

function kv(k, v) {
  if (v == null || v === "" || v === "—") return "";
  return `<div class="kv"><span class="k">${k}</span><span class="v">${v}</span></div>`;
}

function getItemPrimaryImage(item) {
  // Priority: collection_item_assets primary → asset_bundle → attributes
  if (item.primary_asset_uri) return item.primary_asset_uri;
  const ab = item.asset_bundle || {};
  // asset_bundle uses display_image/reference_image (post-v0.13 migration);
  // primary_image is the legacy field name kept as last-resort fallback.
  if (ab.display_image) return ab.display_image;
  if (ab.reference_image) return ab.reference_image;
  const attrs = item.attributes || {};
  return attrs.display_image || attrs.reference_image || attrs.presentation_board || attrs.reference_crop || ab.primary_image || null;
}

function apiUrl(path, params = {}, force = false) {
  const url = new URL(`${API}${path}`, window.location.origin);
  Object.entries(params).forEach(([key, value]) => {
    if (value != null && value !== "") url.searchParams.set(key, value);
  });
  if (force) url.searchParams.set("_", String(reloadSerial));
  return `${url.pathname}${url.search}`;
}

function assetUrl(path) {
  return `/api/asset?path=${encodeURIComponent(path)}&v=${reloadSerial}`;
}

function assetPreviewUrl(path) {
  return `/api/asset/preview?path=${encodeURIComponent(path)}&max_width=360&max_height=480&v=${reloadSerial}`;
}

function staticAssetUrl(name) {
  return `/static/assets/${name}?v=${reloadSerial}`;
}

function reloadStylesheets() {
  document.querySelectorAll('link[rel="stylesheet"]').forEach(link => {
    const url = new URL(link.getAttribute("href"), window.location.origin);
    url.searchParams.set("v", String(reloadSerial));
    link.setAttribute("href", `${url.pathname}${url.search}`);
  });
}

function attrLabel(key) {
  const map = {
    category: "类别", color_family: "色系", season: "季节", style_tags: "风格",
    material: "材质", warmth: "保暖", formalness: "正式度", shoe_type: "鞋型",
    weather_suitability: "天气适配", comfort: "舒适度", sock_type: "袜型",
    length: "长度", thickness: "厚薄", accessory_type: "配饰类型",
    symbolic_meaning: "象征意义", vanity_type: "造型类型", palette: "色盘",
    is_consumable: "消耗品", purpose: "用途", structure: "结构",
  };
  return map[key] || key;
}

function escapeMd(md) {
  if (!md) return "";
  // 极简 markdown 渲染
  return md
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/^### (.+)$/gm, "<h3>$1</h3>")
    .replace(/^## (.+)$/gm, "<h2>$1</h2>")
    .replace(/^# (.+)$/gm, "<h1>$1</h1>")
    .replace(/```[\s\S]*?```/g, m => `<pre><code>${m.slice(3,-3)}</code></pre>`)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\n\n/g, "</p><p>")
    .replace(/^(?!<[hbp])(.+)$/gm, "<p>$1</p>");
}
