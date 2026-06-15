/* ═══════════════════════════════════════════════
   归明观 Observatory — 前端逻辑
   ═══════════════════════════════════════════════ */
"use strict";

const API = "";
let snapshotData = null;
let sseSource = null;
let currentWardrobeTab = null;
let collectionsData = null;

// ── Init ──────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  loadSnapshot();
  document.querySelectorAll(".tab").forEach(tab => {
    tab.addEventListener("click", () => switchTab(tab.dataset.tab));
  });
  document.getElementById("btn-refresh").addEventListener("click", loadSnapshot);
  document.getElementById("btn-tick").addEventListener("click", () => doAction("tick"));
});

// ── Snapshot Loading ──────────────────────────
async function loadSnapshot() {
  try {
    const res = await fetch(`${API}/api/snapshot?period=today`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    snapshotData = await res.json();
    collectionsData = snapshotData.collections;
    render();
    document.getElementById("loading-screen").classList.add("hidden");
    document.getElementById("main-layout").classList.remove("hidden");
    connectSSE();
  } catch (err) {
    console.error("Load failed:", err);
    document.getElementById("loading-screen").innerHTML =
      `<p style="color:#f87171">载入失败: ${err.message}</p><p style="color:#8a8674;font-size:12px">确认 LifeEngine WebUI 正在运行</p>`;
  }
}

// ── SSE Live Update ───────────────────────────
function connectSSE() {
  if (sseSource) sseSource.close();
  sseSource = new EventSource(`${API}/api/stream?period=today`);
  sseSource.addEventListener("snapshot", (e) => {
    try {
      const data = JSON.parse(e.data);
      if (data.snapshot_hash !== snapshotData?.snapshot_hash) {
        snapshotData = data;
        collectionsData = data.collections;
        render();
      }
    } catch (err) { /* ignore parse errors */ }
  });
  sseSource.addEventListener("error", () => { /* auto-reconnect by browser */ });
}

// ── Render Orchestrator ───────────────────────
function render() {
  renderTopBar();
  renderSidebar();
  renderOverview();
  renderWardrobeTabs();
  renderSchedule();
  renderDreams();
  renderTrace();
}

// ── Top Bar ───────────────────────────────────
function renderTopBar() {
  const avatar = snapshotData.avatar || {};
  const label = document.getElementById("avatar-label");
  const bubble = document.getElementById("avatar-bubble");
  label.textContent = avatar.label || snapshotData.state?.mode || "—";
  if (avatar.bubble) {
    bubble.textContent = avatar.bubble;
    bubble.classList.remove("hidden");
  } else {
    bubble.classList.add("hidden");
  }
}

// ── Sidebar: Sprite + Resources ───────────────
function renderSidebar() {
  // Sprite
  const avatar = snapshotData.avatar || {};
  const spriteMap = {
    idle: "sprite-idle", work: "sprite-work", sleep: "sprite-sleep",
    dream: "sprite-dream", reply: "sprite-reply", eat: "sprite-eat",
    walk: "sprite-walk", tired: "sprite-tired", battle: "sprite-battle", recover: "sprite-recover",
  };
  const spriteFile = spriteMap[avatar.sprite_state] || "sprite-idle";
  const spriteImg = document.getElementById("sprite-img");
  spriteImg.src = `/static/assets/${spriteFile}.png`;

  // Resources
  const resBar = document.getElementById("resource-bars");
  resBar.innerHTML = "";
  const resources = snapshotData.resources || [];
  // Group: vitals vs currency vs supplies
  const vitalKeys = ["energy", "fatigue", "focus", "mood"];
  const vitalRes = resources.filter(r => vitalKeys.includes(r.resource_key));
  const currencyRes = resources.filter(r => r.resource_key?.startsWith("money."));
  const supplyRes = resources.filter(r => r.resource_key?.startsWith("supplies.") || r.resource_key?.startsWith("tools."));

  vitalRes.forEach(r => resBar.appendChild(buildResBar(r)));
  currencyRes.forEach(r => resBar.appendChild(buildCurrencyStat(r)));
  supplyRes.forEach(r => resBar.appendChild(buildResBar(r, true)));

  // Quick stats
  const qs = document.getElementById("quick-stats");
  qs.innerHTML = "";
  const sleepDebt = resources.find(r => r.resource_key === "sleep_debt_minutes");
  if (sleepDebt) {
    qs.innerHTML += `<div class="quick-stat"><span class="qs-label">睡眠负债</span><span class="qs-value">${Math.round(sleepDebt.current_value)} min</span></div>`;
  }
  const collections = snapshotData.collections || {};
  const totalItems = (collections.items || []).filter(i => i.status === "active").length;
  qs.innerHTML += `<div class="quick-stat"><span class="qs-label">装扮物品</span><span class="qs-value">${totalItems} 件</span></div>`;
  const goals = snapshotData.recent_events || [];
  qs.innerHTML += `<div class="quick-stat"><span class="qs-label">近期事件</span><span class="qs-value">${goals.length}</span></div>`;
  const control = snapshotData.control || {};
  qs.innerHTML += `<div class="quick-stat"><span class="qs-label">引擎状态</span><span class="qs-value" style="color:var(--jade)">${control.engine_state || "—"}</span></div>`;
}

function buildResBar(r, isSupply = false) {
  const div = document.createElement("div");
  div.className = "res-bar";
  const key = r.resource_key || "";
  const label = r.display_name || key;
  const val = r.current_value ?? 0;
  const min = r.min_value ?? 0;
  const max = r.max_value ?? r.capacity ?? 100;
  const pct = max > min ? Math.min(100, Math.max(0, ((val - min) / (max - min)) * 100)) : 0;
  const cls = key.includes("fatigue") ? "fatigue" : key.includes("focus") ? "focus" : key.includes("mood") ? "mood" : key.includes("energy") ? "energy" : isSupply ? "consumable" : "capacity";
  const icon = key.includes("fatigue") ? "🔥" : key.includes("focus") ? "🎯" : key.includes("mood") ? "✨" : key.includes("energy") ? "⚡" : isSupply ? "📦" : "📊";
  div.innerHTML = `
    <div class="res-bar-header">
      <span class="res-bar-label">${icon} ${label}</span>
      <span class="res-bar-value">${formatNum(val)}${r.unit && !r.unit.includes("point") ? " " + r.unit : ""}</span>
    </div>
    <div class="res-bar-track">
      <div class="res-bar-fill ${cls}" style="width:${pct}%"></div>
    </div>`;
  return div;
}

function buildCurrencyStat(r) {
  const div = document.createElement("div");
  div.className = "quick-stat";
  div.style.cssText = "background:linear-gradient(135deg,rgba(251,191,36,.1),rgba(251,191,36,.03));border:1px solid rgba(251,191,36,.15)";
  div.innerHTML = `<span class="qs-label">💰 ${r.display_name || r.resource_key}</span><span class="qs-value">${formatNum(r.current_value)} ${r.unit || ""}</span>`;
  return div;
}

// ── Overview Tab ──────────────────────────────
function renderOverview() {
  // State
  const stateEl = document.getElementById("state-detail");
  const state = snapshotData.state || {};
  const avatar = snapshotData.avatar || {};
  const currentEvent = snapshotData.current_event;
  stateEl.innerHTML = `
    <div class="state-row"><span class="sr-label">模式</span><span class="sr-value">${state.mode || "—"}</span></div>
    <div class="state-row"><span class="sr-label">场景</span><span class="sr-value">${avatar.scene || "—"}</span></div>
    <div class="state-row"><span class="sr-label">当前事件</span><span class="sr-value">${currentEvent?.title || "无"}</span></div>
    <div class="state-row"><span class="sr-label">可打断性</span><span class="sr-value">${state.interruptibility_level || "—"}</span></div>
    <div class="state-row"><span class="sr-label">回复模式</span><span class="sr-value">${state.reply_mode || "—"}</span></div>`;

  // Goals
  const goalEl = document.getElementById("goal-detail");
  const goals = snapshotData.recent_goals || [];
  if (goals.length === 0) {
    goalEl.innerHTML = `<p style="color:var(--text-dim);font-size:12px">暂无活跃目标</p>`;
  } else {
    goalEl.innerHTML = goals.slice(0, 3).map(g => `
      <div style="margin-bottom:8px">
        <div style="color:var(--gold);font-size:13px">${g.title}</div>
        <div style="font-size:11px;color:var(--text-dim)">进度: ${g.progress || 0}% · 优先级: ${g.priority || "—"}</div>
      </div>`).join("");
  }

  // Today schedule
  const schedEl = document.getElementById("schedule-today-list");
  const schedule = snapshotData.schedule || {};
  const items = schedule.items || [];
  if (items.length === 0) {
    schedEl.innerHTML = `<p style="color:var(--text-dim);font-size:12px">今日无安排</p>`;
  } else {
    schedEl.innerHTML = items.slice(0, 6).map(s => {
      const time = formatTime(s.start) + "—" + formatTime(s.end);
      const badge = s.status === "completed" ? `<span class="timeline-badge completed">完成</span>`
        : s.status === "active" ? `<span class="timeline-badge active">进行中</span>`
        : `<span class="timeline-badge planned">计划</span>`;
      return `<div class="timeline-item">
        <div class="timeline-time">${time}</div>
        <div class="timeline-body">
          <div class="timeline-title">${s.event_title || s.title || "—"} ${badge}</div>
        </div>
      </div>`;
    }).join("");
  }

  // Recent events
  const eventsEl = document.getElementById("recent-events-list");
  const events = snapshotData.recent_events || [];
  if (events.length === 0) {
    eventsEl.innerHTML = `<p style="color:var(--text-dim);font-size:12px">暂无事件</p>`;
  } else {
    eventsEl.innerHTML = events.slice(0, 8).map(e => {
      const statusCls = e.status === "completed" ? "completed" : e.status === "planned" ? "planned" : "";
      return `<div class="event-item ${statusCls}" onclick="showEventDetail('${e.id}')">
        <div class="ei-title">${e.title}</div>
        <div class="ei-meta">${e.event_category || e.event_type || ""} · ${e.status}</div>
      </div>`;
    }).join("");
  }

  // Review items
  const reviewEl = document.getElementById("review-list");
  const reviews = snapshotData.review_items || [];
  if (reviews.length === 0) {
    reviewEl.innerHTML = `<p style="color:var(--text-dim);font-size:12px">✓ 无待处理事项</p>`;
  } else {
    reviewEl.innerHTML = reviews.slice(0, 5).map(r => `
      <div style="padding:6px;border-radius:4px;background:var(--bg-2);font-size:12px;margin-bottom:4px;border-left:3px solid var(--coral)">
        <div style="color:var(--text)">${r.title}</div>
        <div style="color:var(--text-dim);font-size:10px">${r.severity || ""} · ${r.item_type || ""}</div>
      </div>`).join("");
  }

  // Doctor
  const docEl = document.getElementById("doctor-detail");
  const doctor = snapshotData.doctor;
  if (!doctor) {
    docEl.innerHTML = `<p style="color:var(--text-dim);font-size:12px">无记录</p>`;
  } else {
    const summary = doctor.summary || {};
    const ok = summary.status === "ok";
    docEl.innerHTML = `
      <div class="state-row"><span class="sr-label">状态</span><span class="sr-value" style="color:${ok ? "var(--jade)" : "var(--coral)"}">${ok ? "✓ 正常" : "⚠ 有问题"}</span></div>
      <div class="state-row"><span class="sr-label">Schema</span><span class="sr-value">v${snapshotData.meta?.schema_version || "?"}</span></div>
      <div class="state-row"><span class="sr-label">Hash 链</span><span class="sr-value">${summary.journal_hash_chain?.ok !== false ? "✓ 完整" : "✗ 异常"}</span></div>`;
  }
}

// ── Wardrobe / Collections ────────────────────
function renderWardrobeTabs() {
  const board = collectionsData?.board || [];
  if (board.length === 0) return;
  const tabBar = document.getElementById("wardrobe-tabs");
  if (!currentWardrobeTab) {
    currentWardrobeTab = board[0]?.collection?.id;
  }
  tabBar.innerHTML = board.map(b => {
    const c = b.collection;
    const isActive = c.id === currentWardrobeTab;
    return `<button class="wardrobe-tab ${isActive ? "active" : ""}" onclick="switchWardrobeTab('${c.id}')">${c.name}<span class="wt-count">${b.item_count}</span></button>`;
  }).join("");
  renderWardrobeGrid();
}

function switchWardrobeTab(id) {
  currentWardrobeTab = id;
  renderWardrobeTabs();
}

function renderWardrobeGrid() {
  const board = collectionsData?.board || [];
  const entry = board.find(b => b.collection?.id === currentWardrobeTab);
  if (!entry) return;
  const grid = document.getElementById("wardrobe-grid");
  const items = entry.items || [];
  if (items.length === 0) {
    grid.innerHTML = `<p style="color:var(--text-dim);grid-column:1/-1;text-align:center;padding:40px">这个柜子还是空的</p>`;
    return;
  }
  grid.innerHTML = items.map(item => {
    const primaryAsset = getItemPrimaryImage(item);
    const statusCls = item.status === "archived" ? "archived" : "active";
    const tags = (item.tags || []).slice(0, 4);
    const attrs = item.attributes || {};
    return `<div class="item-card" onclick="showItemDetail('${item.id}')">
      <div class="item-card-image">
        ${primaryAsset
          ? `<img src="/api/asset?path=${encodeURIComponent(primaryAsset)}" alt="${item.name}" loading="lazy">`
          : `<div class="no-image"><span>🖼</span><span>无资产图</span></div>`}
        <span class="item-card-badge ${statusCls}">${item.status === "archived" ? "归档" : "可用"}</span>
      </div>
      <div class="item-card-info">
        <div class="item-card-name">${item.name}</div>
        <div class="item-card-meta">
          ${item.quantity > 1 ? `<span>×${item.quantity}</span>` : ""}
          ${item.cleanliness_state ? `<span>${item.cleanliness_state}</span>` : ""}
          ${(item.asset_counts?.available || 0) > 0 ? `<span>📦${item.asset_counts.available}</span>` : ""}
        </div>
        ${tags.length > 0 ? `<div class="item-card-tags">${tags.map(t => `<span class="item-card-tag">${t}</span>`).join("")}</div>` : ""}
      </div>
    </div>`;
  }).join("");
}

function getItemPrimaryImage(item) {
  // Priority: DB asset URI > attributes > asset_bundle
  if (item.primary_asset_uri) return item.primary_asset_uri;
  const attrs = item.attributes || {};
  if (attrs.reference_image) return attrs.reference_image;
  if (attrs.presentation_board) return attrs.presentation_board;
  if (attrs.reference_crop) return attrs.reference_crop;
  const bundle = item.asset_bundle || {};
  if (bundle.primary_image) return bundle.primary_image;
  return null;
}

// ── Item Detail Modal ─────────────────────────
async function showItemDetail(itemId) {
  const board = collectionsData?.board || [];
  let item = null;
  for (const b of board) {
    item = (b.items || []).find(i => i.id === itemId);
    if (item) break;
  }
  if (!item) return;

  const modalBody = document.getElementById("item-modal-body");
  const primaryAsset = getItemPrimaryImage(item);
  const attrs = item.attributes || {};
  const matSpec = item.material_spec || {};
  const tags = item.tags || [];
  const aliases = item.aliases || [];

  // Build attribute fields
  const attrFields = Object.entries(attrs)
    .filter(([k]) => !["image_reference_role", "presentation_board", "reference_image", "reference_crop"].includes(k))
    .filter(([, v]) => v != null && v !== "" && (typeof v !== "string" || !v.startsWith("/")))
    .map(([k, v]) => `<div class="im-field"><div class="im-field-label">${attrLabel(k)}</div><div class="im-field-value">${String(v)}</div></div>`)
    .join("");

  const matFields = Object.entries(matSpec)
    .filter(([, v]) => v != null && v !== "")
    .map(([k, v]) => `<div class="im-field"><div class="im-field-label">${attrLabel(k)}</div><div class="im-field-value">${String(v).slice(0, 200)}</div></div>`)
    .join("");

  modalBody.innerHTML = `
    <h2>${item.name}</h2>
    <div class="im-subtitle">${item.collection_name || ""} · ${item.status} · ${item.cleanliness_state || ""} ${aliases.length > 0 ? "· 别名: " + aliases.join(", ") : ""}</div>
    ${primaryAsset ? `<div class="im-image"><img src="/api/asset?path=${encodeURIComponent(primaryAsset)}" alt="${item.name}"></div>` : ""}
    ${item.description ? `<div class="im-section"><p style="font-size:13px;line-height:1.6">${item.description}</p></div>` : ""}
    ${attrFields ? `<div class="im-section"><h3 style="font-size:12px;color:var(--gold);margin-bottom:8px">属性</h3><div class="im-grid">${attrFields}</div></div>` : ""}
    ${matFields ? `<div class="im-section"><h3 style="font-size:12px;color:var(--gold);margin-bottom:8px">材质 / 规格</h3><div class="im-grid">${matFields}</div></div>` : ""}
    ${tags.length > 0 ? `<div class="im-section"><div style="display:flex;flex-wrap:wrap;gap:4px">${tags.map(t => `<span class="item-card-tag" style="padding:2px 8px;font-size:11px">${t}</span>`).join("")}</div></div>` : ""}
  `;
  document.getElementById("item-modal").classList.remove("hidden");
}

function closeModal() {
  document.getElementById("item-modal").classList.add("hidden");
}

// ── Schedule Tab ──────────────────────────────
function renderSchedule() {
  const schedule = snapshotData.schedule || {};
  const items = schedule.items || [];
  const timeline = document.getElementById("schedule-timeline");
  const label = document.getElementById("schedule-label");
  label.textContent = schedule.label || "日程时间线";

  if (items.length === 0) {
    timeline.innerHTML = `<p style="color:var(--text-dim);text-align:center;padding:40px">暂无日程</p>`;
    return;
  }
  timeline.innerHTML = items.map(s => {
    const badge = s.status === "completed" ? `<span class="timeline-badge completed">完成</span>`
      : s.status === "active" ? `<span class="timeline-badge active">进行中</span>`
      : `<span class="timeline-badge planned">计划</span>`;
    const time = formatTime(s.start) + "—" + formatTime(s.end);
    const isSleep = s.is_sleep ? " 🌙" : "";
    return `<div class="timeline-item">
      <div class="timeline-time">${time}</div>
      <div class="timeline-body">
        <div class="timeline-title">${s.event_title || s.title || "—"}${isSleep} ${badge}</div>
        <div class="timeline-meta">${s.event_category || ""} · ${s.block_type || ""} · ${s.status}</div>
      </div>
    </div>`;
  }).join("");
}

// ── Dreams Tab ────────────────────────────────
function renderDreams() {
  const dreams = snapshotData.dreams || [];
  const container = document.getElementById("dreams-list");
  if (dreams.length === 0) {
    container.innerHTML = `<p style="color:var(--text-dim);text-align:center;padding:40px">暂无梦境记录</p>`;
    return;
  }
  container.innerHTML = dreams.map(d => {
    const symbols = d.symbols || [];
    return `<div class="dream-card">
      <div class="dc-time">${d.created_at || "—"}</div>
      <div class="dc-content">${d.content || d.summary || "—"}</div>
      ${symbols.length > 0 ? `<div class="dc-symbols">${symbols.map(s => `<span class="dc-symbol">${s}</span>`).join("")}</div>` : ""}
      ${d.severity ? `<div style="font-size:10px;color:var(--text-dim);margin-top:4px">严重度: ${d.severity}</div>` : ""}
    </div>`;
  }).join("");
}

// ── Trace / Log Tab ───────────────────────────
function renderTrace() {
  const trace = snapshotData.trace || [];
  const container = document.getElementById("trace-list");
  if (trace.length === 0) {
    container.innerHTML = `<p style="color:var(--text-dim);text-align:center;padding:40px">暂无日志</p>`;
    return;
  }
  container.innerHTML = trace.map(t => {
    return `<div class="trace-item" onclick="showTraceDetail('${t.id}')">
      <span class="ti-type">${t.entry_type || "—"}</span>
      <span class="ti-source">${t.source || "—"}</span>
      <span class="ti-time">${t.created_at || ""}</span>
    </div>`;
  }).join("");
}

async function showTraceDetail(id) {
  try {
    const res = await fetch(`${API}/api/trace/explain/${id}`);
    const data = await res.json();
    const body = document.getElementById("item-modal-body");
    body.innerHTML = `
      <h2 style="font-size:16px">追踪详情</h2>
      <div class="im-subtitle">${id}</div>
      <pre style="background:var(--bg-2);padding:12px;border-radius:4px;font-size:11px;overflow-x:auto;color:var(--text);max-height:60vh;line-height:1.5">${JSON.stringify(data, null, 2)}</pre>`;
    document.getElementById("item-modal").classList.remove("hidden");
  } catch (err) {
    console.error(err);
  }
}

async function showEventDetail(id) {
  try {
    const res = await fetch(`${API}/api/event/${id}`);
    const data = await res.json();
    const ev = data.event || {};
    const body = document.getElementById("item-modal-body");
    const attrs = ev.attributes || {};
    const costs = ev.resource_costs || {};
    body.innerHTML = `
      <h2>${ev.title || id}</h2>
      <div class="im-subtitle">${ev.event_category || ""} · ${ev.status} · ${ev.importance || ""}</div>
      ${ev.description ? `<p style="font-size:13px;line-height:1.6;margin-bottom:12px">${ev.description}</p>` : ""}
      <div class="im-grid">
        <div class="im-field"><div class="im-field-label">类型</div><div class="im-field-value">${ev.event_type || "—"}</div></div>
        <div class="im-field"><div class="im-field-label">活动域</div><div class="im-field-value">${ev.activity_domain || "—"}</div></div>
        <div class="im-field"><div class="im-field-label">优先级</div><div class="im-field-value">${ev.priority || "—"}</div></div>
        <div class="im-field"><div class="im-field-label">子类型</div><div class="im-field-value">${ev.subtype || "—"}</div></div>
      </div>
      ${Object.keys(costs).length > 0 ? `<div class="im-section"><div class="im-field-label">资源消耗</div><pre style="font-size:11px;color:var(--text-dim)">${JSON.stringify(costs, null, 2)}</pre></div>` : ""}
      <div class="im-section">
        <div class="im-field-label">调度块 (${(data.schedule_blocks || []).length})</div>
        ${(data.schedule_blocks || []).map(s => `<div style="font-size:11px;color:var(--text-dim);margin-top:4px">${formatTime(s.start)}—${formatTime(s.end)} · ${s.status}</div>`).join("")}
      </div>`;
    document.getElementById("item-modal").classList.remove("hidden");
  } catch (err) {
    console.error(err);
  }
}

// ── Actions ───────────────────────────────────
async function doAction(action) {
  try {
    const res = await fetch(`${API}/api/action`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action }),
    });
    const data = await res.json();
    if (data.ok !== false) {
      loadSnapshot();
    } else {
      console.warn("Action failed:", data);
    }
  } catch (err) {
    console.error("Action error:", err);
  }
}

// ── Tab Switching ─────────────────────────────
function switchTab(tab) {
  document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
  document.querySelectorAll(".tab-panel").forEach(p => { p.classList.remove("active"); p.classList.add("hidden"); });
  document.querySelector(`[data-tab="${tab}"]`).classList.add("active");
  const panel = document.getElementById(`tab-${tab}`);
  if (panel) { panel.classList.add("active"); panel.classList.remove("hidden"); }
}

// ── Helpers ───────────────────────────────────
function formatTime(ts) {
  if (!ts) return "—";
  try {
    const d = new Date(ts);
    if (isNaN(d)) return String(ts).slice(11, 16) || "—";
    return d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
  } catch { return "—"; }
}

function formatNum(n) {
  if (n == null) return "—";
  if (Math.abs(n) >= 1000) return n.toLocaleString("zh-CN", { maximumFractionDigits: 1 });
  return Number(n).toFixed(n % 1 === 0 ? 0 : 1);
}

function attrLabel(key) {
  const map = {
    category: "类别", color_family: "色系", season: "季节", style_tags: "风格",
    material: "材质", warmth: "保暖", formalness: "正式度", shoe_type: "鞋型",
    weather_suitability: "天气适配", comfort: "舒适度", sock_type: "袜型",
    length: "长度", thickness: "厚薄", quantity_per_pair: "每双数量",
    accessory_type: "配饰类型", symbolic_meaning: "象征意义",
    vanity_type: "造型类型", palette: "色盘", hair_accessories: "发饰",
    time_cost_minutes: "造型时间", structure: "结构", sole: "鞋底", heel: "鞋跟",
  };
  return map[key] || key;
}
