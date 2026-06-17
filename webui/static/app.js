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
let currentCollectionTab = null;
let codexDocs = [];
let reloadSerial = Date.now();
let soundOn = true;
let audioCtx = null;

// 程序化音效(WebAudio,无需素材)。需用户手势后才能发声。
function blip(kind) {
  if (!soundOn) return;
  try {
    if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    const ctx = audioCtx, t = ctx.currentTime;
    const freq = kind === "open" ? 660 : kind === "primary" ? 392 : 523;
    const o = ctx.createOscillator(), g = ctx.createGain();
    o.type = "triangle"; o.frequency.setValueAtTime(freq, t);
    o.frequency.exponentialRampToValueAtTime(freq * 1.5, t + 0.06);
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(0.05, t + 0.012);
    g.gain.exponentialRampToValueAtTime(0.0001, t + 0.16);
    o.connect(g); g.connect(ctx.destination);
    o.start(t); o.stop(t + 0.18);
  } catch (e) {}
}

// ── 初始化 ────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  bindEvents();
  loadSnapshot();
});

function bindEvents() {
  document.getElementById("btn-refresh").onclick = () => loadSnapshot();
  document.getElementById("btn-reload").onclick = () => reloadInPage();
  document.getElementById("btn-tick").onclick = () => { blip("primary"); doEnginePrimaryAction(); };
  const soundBtn = document.getElementById("btn-sound");
  if (soundBtn) soundBtn.onclick = () => {
    soundOn = !soundOn;
    soundBtn.textContent = soundOn ? "🔊" : "🔇";
    soundBtn.classList.toggle("muted", !soundOn);
    if (soundOn) blip("open");
  };
  // hotbar — 技能栏:槽位编号 + 1-9 快捷键
  const hotbarBtns = Array.from(document.querySelectorAll(".hotbar-btn"));
  hotbarBtns.forEach((btn, i) => {
    if (i < 9 && !btn.querySelector(".hotbar-key")) {
      const key = document.createElement("span");
      key.className = "hotbar-key";
      key.textContent = String(i + 1);
      btn.appendChild(key);
    }
    btn.onclick = () => switchOverlay(btn.dataset.overlay);
  });
  document.addEventListener("keydown", (e) => {
    const tag = (e.target && e.target.tagName) || "";
    if (tag === "INPUT" || tag === "TEXTAREA" || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === "Escape") { switchOverlay("stage"); return; }
    const n = parseInt(e.key, 10);
    if (n >= 1 && n <= hotbarBtns.length) { switchOverlay(hotbarBtns[n - 1].dataset.overlay); }
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
    collectionsData = snapshotData;
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
          collectionsData = data;
          render();
        }
      } catch {}
    });
    sseSource.addEventListener("error", () => {
      if (sseSource) sseSource.close();
      sseSource = null;
    });
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
  renderCollections();
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
  const engine = engineDisplayState(control, state, snapshotData.current_event);
  document.getElementById("db-selector").textContent = meta.db_path ? meta.db_path.split("/").pop() : "—";
  document.getElementById("owner-tag").textContent = `${owner.owner_kind || "agent"}:${owner.owner_id || "—"}`;
  const chip = document.getElementById("engine-state-tag");
  chip.textContent = engine.label;
  chip.title = `engine=${control.engine_state || "—"}; heartbeat=${control.heartbeat_mode || "—"}`;
  chip.className = `state-chip ${engine.className}`;
  const tickBtn = document.getElementById("btn-tick");
  if (tickBtn) {
    tickBtn.title = engine.active ? "手动推进一次 LifeEngine 心跳" : "开启 LifeEngine 并推进一次";
    tickBtn.innerHTML = `${engine.active ? "▶" : "⏵"}<span class="btn-label">${engine.active ? "推进" : "开启"}</span>`;
  }
}

// ── 左栏 ──────────────────────────────────────
function renderSidebar() {
  const control = snapshotData.control || {};
  const state = snapshotData.state || {};
  const avatar = snapshotData.avatar || {};
  // 立绘信息
  const owner = snapshotData.owner || {};
  document.getElementById("agent-portrait").src = staticAssetUrl("default-agent-reference.jpg");
  document.getElementById("char-name").textContent = owner.owner_id || "—";
  document.getElementById("char-title").textContent = avatar.label || avatar.scene || state.mode || "—";
  const engine = engineDisplayState(control, state, snapshotData.current_event);
  const eventTitle = snapshotData.current_event?.title || "暂无当前事项";
  const engineCard = document.getElementById("engine-live-card");
  if (engineCard) {
    engineCard.className = `engine-live-card ${engine.className}`;
    engineCard.innerHTML = `<div class="engine-live-head">
      <span><span class="engine-dot"></span><span class="engine-state-text">${engine.label}</span></span>
      <button class="engine-mini-btn" onclick="doEnginePrimaryAction()">${engine.active ? "推进" : "开启"}</button>
    </div>
    <div class="engine-live-sub">${escapeHtml(eventTitle)}<br>心跳：${escapeHtml(control.heartbeat_mode || "—")}</div>`;
  }

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

  // 一日三餐
  const mealsRow = document.getElementById("meals-row");
  const mealsBlock = document.getElementById("meals-block");
  const meals = (snapshotData.meals_today || {}).meals || [];
  if (mealsRow) {
    if (!meals.length) { if (mealsBlock) mealsBlock.style.display = "none"; }
    else {
      if (mealsBlock) mealsBlock.style.display = "";
      const icon = { eaten: "🍚", skipped: "✕", pending: "·", covered: "🍱" };
      const label = { breakfast: "早", lunch: "午", dinner: "晚", brunch: "早午", afternoon_tea: "茶", late_night_snack: "夜宵", snack: "加餐" };
      const extras = (snapshotData.meals_today || {}).extras || [];
      const chip = (m, name) =>
        `<div class="meal-chip ${m.status}" title="${name}${m.skip_reason ? " — " + m.skip_reason : ""}">
          <span class="meal-name">${name}</span><span class="meal-mark">${icon[m.status] || "🍚"}</span>
        </div>`;
      mealsRow.innerHTML =
        meals.map(m => chip(m, (label[m.meal_type] || m.meal_type) + (m.time ? " " + m.time : ""))).join("") +
        extras.map(m => chip(m, label[m.meal_type] || m.meal_type)).join("");
    }
  }

  // 活体人格
  const persona = snapshotData.persona || {};
  const personaBlock = document.getElementById("persona-block");
  const traits = persona.traits || [];
  if (!persona.seeded || !traits.length) {
    if (personaBlock) personaBlock.style.display = "none";
  } else {
    if (personaBlock) personaBlock.style.display = "";
    document.getElementById("persona-tone").textContent = persona.tone_hint || "性格平稳";
    // value is -1..1 -> map to 0..100% with a center mark; color by direction/magnitude.
    document.getElementById("persona-traits").innerHTML = traits.map(t => {
      const v = Number(t.value) || 0;
      const pct = Math.max(0, Math.min(100, (v + 1) / 2 * 100));
      const cls = Math.abs(v) < 0.2 ? "" : (v > 0 ? "high" : "low");
      const drift = Number(t.drift) || 0;
      const driftTag = Math.abs(drift) >= 0.3 ? ` <span class="persona-drift">${drift > 0 ? "↑" : "↓"}</span>` : "";
      return `<div class="persona-trait" title="基线 ${t.baseline}, 经历 ${t.evidence_count} 次">
        <div class="persona-trait-head"><span class="name">${t.key}${driftTag}</span><span class="num">${v.toFixed(2)}</span></div>
        <div class="persona-trait-track"><div class="persona-trait-center"></div><div class="persona-trait-fill ${cls}" style="width:${pct}%"></div></div>
      </div>`;
    }).join("");
  }
}

// ── 中央舞台(沉浸式场景) ──────────────────────
// 全部状态映射到新版明灯三头身像素形象的 4 个姿势,CSS 负责动作。
// 1:1 — every realtime state has its own 明灯 pose.
const SPRITE_FOR = {
  idle: "idle", work: "work", walk: "walk", sleep: "sleep", dream: "dream",
  eat: "eat", reply: "reply", battle: "battle", tired: "tired", recover: "recover",
};
const MOVING_STATES = new Set(["walk"]);
const RESTING_STATES = new Set(["sleep", "tired", "recover", "dream"]);
// every pose now has a 2-frame looping WebP (native animation); PNG is fallback.
const ANIMATED_POSES = new Set(["idle", "work", "walk", "sleep", "dream", "eat", "reply", "battle", "tired", "recover"]);
let particlesBuilt = false;
let _bgScene = null, _bgOk = false;

// Per-scene real background image (host-overridable via /api/avatar). On load it
// fades in and the CSS-drawn scene hides; if missing it falls back to CSS.
function setSceneBackground(sceneName) {
  const stage = document.getElementById("stage-scene");
  const photo = document.getElementById("scene-photo");
  if (!stage || !photo) return;
  if (sceneName === _bgScene) { if (_bgOk) stage.classList.add("has-bg"); return; }
  _bgScene = sceneName; _bgOk = false;
  const url = `/api/avatar/bg-${sceneName}.webp?v=${reloadSerial}`;
  const probe = new Image();
  probe.onload = () => { if (_bgScene !== sceneName) return; photo.style.backgroundImage = `url(${url})`; _bgOk = true; stage.classList.add("has-bg"); };
  probe.onerror = () => { if (_bgScene !== sceneName) return; _bgOk = false; stage.classList.remove("has-bg"); photo.style.backgroundImage = "none"; };
  probe.src = url;
}

function animateSprite(spriteState) {
  const img = document.getElementById("sprite-img");
  if (!img) return;
  const file = SPRITE_FOR[spriteState] || "idle";
  const ext = ANIMATED_POSES.has(file) ? "webp" : "png";
  const name = `sprite-${file}.${ext}`;
  if (img.src.indexOf(name) === -1) img.src = staticAssetUrl(name);
}

function buildParticles() {
  if (particlesBuilt) return;
  const host = document.getElementById("stage-particles");
  if (!host) return;
  let html = "";
  for (let i = 0; i < 16; i++) {
    const left = Math.round(Math.random() * 100);
    const dur = (6 + Math.random() * 8).toFixed(1);
    const delay = (Math.random() * 8).toFixed(1);
    const bottom = Math.round(20 + Math.random() * 55);
    html += `<span class="mote" style="left:${left}%;bottom:${bottom}%;animation-duration:${dur}s;animation-delay:${delay}s"></span>`;
  }
  host.innerHTML = html;
  particlesBuilt = true;
}

function positionCelestial(clock) {
  const el = document.getElementById("celestial");
  if (!el) return;
  const hour = clock && clock.hour != null ? clock.hour : 12;
  const phase = (clock && clock.phase) || "day";
  // sun rides 6→18, moon rides 18→6
  let progress;
  if (phase === "night") progress = (((hour - 18 + 24) % 24)) / 12;
  else progress = Math.max(0, Math.min(1, (hour - 6) / 12));
  const left = 8 + progress * 84;
  const top = 42 - Math.sin(progress * Math.PI) * 30;   // arc
  el.style.left = `${left}%`;
  el.style.top = `${top}%`;
}

function renderStage() {
  const avatar = snapshotData.avatar || {};
  const state = snapshotData.state || {};
  const owner = snapshotData.owner || {};
  const clock = snapshotData.clock || {};
  const currentEvent = snapshotData.current_event;
  const spriteState = avatar.sprite_state || "idle";
  const sceneName = avatar.scene || "observatory";
  const phase = clock.phase || "day";

  const stageEl = document.getElementById("stage-scene");
  if (stageEl) stageEl.className = `stage-scene scene-${sceneName} phase-${phase}`;
  setSceneBackground(sceneName);

  // 角色:落地 + 帧动画 + 动作姿态
  const actor = document.getElementById("actor");
  if (actor) actor.className = "actor" + (MOVING_STATES.has(spriteState) ? " moving" : RESTING_STATES.has(spriteState) ? " resting" : "");
  animateSprite(spriteState);
  buildParticles();
  positionCelestial(clock);

  // 时钟
  const clockEl = document.getElementById("stage-clock");
  if (clockEl) {
    const icon = phase === "night" ? "🌙" : phase === "dusk" ? "🌆" : phase === "dawn" ? "🌅" : "☀";
    clockEl.innerHTML = clock.hhmm ? `${icon} ${clock.hhmm}<span class="ph">${clock.label || ""}</span>` : "";
  }

  // 任务条
  const ribbon = document.getElementById("quest-ribbon");
  if (ribbon) {
    if (currentEvent) {
      ribbon.innerHTML = `<span class="qr-tag">⚔ 当前</span>${escapeHtml(currentEvent.title)}`;
      ribbon.classList.remove("hidden");
      ribbon.onclick = () => showEventDetail(currentEvent.id);
    } else {
      ribbon.classList.add("hidden");
      ribbon.onclick = null;
    }
  }

  // 生命力宝珠
  const orbs = document.getElementById("vital-orbs");
  if (orbs) {
    const resources = snapshotData.resources || [];
    const want = [["energy", "精"], ["focus", "专"], ["mood", "心"]];
    orbs.innerHTML = want.map(([key, glyph]) => {
      const r = resources.find(x => x.resource_key === key);
      if (!r) return "";
      const min = r.min_value != null ? r.min_value : 0;
      const max = r.max_value != null ? r.max_value : 100;
      const pct = max > min ? Math.max(0, Math.min(100, (r.current_value - min) / (max - min) * 100)) : 50;
      return `<div class="v-orb ${key}" title="${r.display_name || key}: ${formatNum(r.current_value)}"><div class="fill" style="height:${pct}%"></div><span class="glyph">${glyph}</span></div>`;
    }).join("");
  }

  // 角色:活动特效 + 情绪着色 + 头顶表情
  renderActorEffects(spriteState, snapshotData.resources || []);
  if (actor) {
    actor.onclick = currentEvent ? () => showEventDetail(currentEvent.id) : null;
    actor.style.cursor = currentEvent ? "pointer" : "default";
  }

  // JRPG 对话框
  document.getElementById("dlg-name").textContent = owner.owner_id || "—";
  const dlgText = document.getElementById("dlg-text");
  const line = avatar.bubble || (currentEvent ? currentEvent.title : null) || avatar.label || "观察生活流……";
  dlgText.textContent = line;
  const dlgMeta = document.getElementById("dlg-meta");
  const sub = currentEvent ? `${currentEvent.event_category || ""} · ${currentEvent.status}` : (avatar.label || "");
  dlgMeta.textContent = sub;

  // 主动说话气泡
  renderProactiveBubble();

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

const ACTOR_FX = {
  work:    { fx: "✦ ✦ ✦", cls: "fx-rise" },
  battle:  { fx: "⚡ ⚡", cls: "fx-rise" },
  walk:    { fx: "· · ·", cls: "fx-trail" },
  eat:     { fx: "♨ ♨", cls: "fx-rise" },
  sleep:   { fx: "z Z z", cls: "fx-rise" },
  dream:   { fx: "✧ ✦ ✧", cls: "fx-rise" },
  reply:   { fx: "✉", cls: "fx-pop" },
  tired:   { fx: "💧", cls: "fx-rise" },
  recover: { fx: "✿ ✿", cls: "fx-rise" },
  idle:    { fx: "", cls: "" },
};

function renderActorEffects(spriteState, resources) {
  const fxEl = document.getElementById("actor-fx");
  const emoteEl = document.getElementById("actor-emote");
  const img = document.getElementById("sprite-img");
  const def = ACTOR_FX[spriteState] || ACTOR_FX.idle;
  if (fxEl) {
    if (def.fx) {
      fxEl.className = "actor-fx " + def.cls;
      fxEl.innerHTML = def.fx.split(/\s+/).filter(Boolean)
        .map((c, i) => `<span style="--i:${i}">${c}</span>`).join("");
    } else { fxEl.className = "actor-fx"; fxEl.innerHTML = ""; }
  }
  const mood = (resources.find(r => r.resource_key === "mood") || {}).current_value;
  if (img) {
    let filter = "drop-shadow(0 6px 10px rgba(0,0,0,.55))";
    if (mood != null && mood <= -30) filter += " saturate(.6) brightness(.9)";
    else if (mood != null && mood >= 40) filter += " saturate(1.15) brightness(1.06)";
    img.style.filter = filter;
  }
  // Head emote removed: it rendered as a stray floating glyph and the dialogue
  // box + FX already convey state/mood.
  if (emoteEl) { emoteEl.textContent = ""; emoteEl.style.display = "none"; }
}

// 主动说话:有 outbox 待发(想对你说)或 pending 意图(想找机会说)时,头上冒话
function renderProactiveBubble() {
  const el = document.getElementById("speech-bubble");
  if (!el) return;
  const pro = snapshotData.proactive || {};
  const term = new Set(["sent", "suppressed", "expired", "cancelled", "delivered"]);
  const ob = (pro.outbox || []).find(o => !term.has(o.status) && (o.draft_text || o.message_text || o.summary));
  const it = (pro.intents || []).find(i => (i.status === "queued" || i.status === "generated") && i.summary);
  let text = null, kind = "";
  if (ob) { text = ob.draft_text || ob.message_text || ob.summary; kind = "want-say"; }
  else if (it) { text = it.summary; kind = "on-mind"; }
  if (!text) { el.className = "speech-bubble hidden"; el.onclick = null; return; }
  const full = String(text);
  const tag = kind === "want-say" ? "📣 想对你说" : "💭 想找机会说";
  const shown = full.length > 42 ? full.slice(0, 42) + "…" : full;
  el.className = "speech-bubble " + kind;
  el.innerHTML = `<span class="sb-tag">${tag}</span><span class="sb-text">${escapeHtml(shown)}</span>`;
  el.onclick = () => { switchOverlay("stage"); showToast((kind === "want-say" ? "📣 " : "💭 ") + full, "ok", 6500); };
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
  const nowMs = (snapshotData.clock && snapshotData.clock.iso) ? Date.parse(snapshotData.clock.iso) : Date.now();
  const nowLabel = (snapshotData.clock && snapshotData.clock.hhmm) || "";
  const isToday = currentPeriod === "today";
  let nowMarked = false;
  let html = '<div class="timeline">';
  items.slice(0, 24).forEach(it => {
    const s = Date.parse(it.start), e = Date.parse(it.end);
    let temporal = "upcoming";
    if (!isNaN(e) && e < nowMs) temporal = "past";
    else if (!isNaN(s) && !isNaN(e) && s <= nowMs && nowMs <= e) temporal = "now";
    if (isToday && !nowMarked && temporal !== "past") {
      html += `<div class="tl-now"><span class="tl-now-label">现在 ${nowLabel}</span></div>`;
      nowMarked = true;
    }
    const status = it.status || "";
    const cls = status === "completed" ? "completed"
      : temporal === "now" || status === "in_progress" || status === "active" ? "active"
      : it.is_sleep ? "sleep" : temporal;
    const evTitle = it.event_title || it.title || "—";
    html += `<div class="tl-node ${cls}" onclick="showScheduleDetail('${it.event_id || ""}')">
      <span class="tl-dot"></span>
      <div class="tl-body">
        <div class="tl-time">${formatTime(it.start)}—${formatTime(it.end)}</div>
        <div class="tl-title">${escapeHtml(evTitle)}${it.is_sleep ? " 🌙" : ""}</div>
        <div class="tl-sub">${escapeHtml(status)}${it.event_category ? " · " + escapeHtml(it.event_category) : ""}</div>
      </div>
    </div>`;
  });
  html += "</div>";
  el.innerHTML = html;
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

// ── 随身物品面板 ──────────────────────────────
function renderBag() {
  const loadout = collectionsData?.collections?.loadout || snapshotData?.collections?.loadout || [];
  const grid = document.getElementById("bag-grid");
  if (!loadout.length) {
    grid.innerHTML = '<div class="empty-state">当前没有随身携带物品</div>';
    return;
  }
  grid.innerHTML = renderItemCards(loadout, { showSlot: true });
}

// ── Collection 仓库面板 ────────────────────────
function renderCollections() {
  const board = collectionsData?.collections?.board || snapshotData?.collections?.board || [];
  if (!board.length) {
    document.getElementById("collection-tabs").innerHTML = "";
    document.getElementById("collection-grid").innerHTML = '<div class="empty-state">Collection 为空</div>';
    return;
  }
  if (!currentCollectionTab) currentCollectionTab = board[0]?.collection?.id;
  // tabs
  document.getElementById("collection-tabs").innerHTML = board.map(b => {
    const c = b.collection;
    return `<button class="sub-tab ${c.id === currentCollectionTab ? "active" : ""}" onclick="switchCollectionTab('${c.id}')">${c.name} (${b.item_count})</button>`;
  }).join("");
  // grid
  const entry = board.find(b => b.collection?.id === currentCollectionTab);
  const items = entry?.items || [];
  const grid = document.getElementById("collection-grid");
  if (!items.length) {
    grid.innerHTML = '<div class="empty-state">此柜为空</div>';
    return;
  }
  grid.innerHTML = renderItemCards(items);
}

function renderItemCards(items, options = {}) {
  return items.map(item => {
    const img = getItemPrimaryImage(item);
    const badges = [];
    if (item.status === "active") badges.push('<span class="item-badge active">可用</span>');
    if (item.attributes?.is_consumable) badges.push('<span class="item-badge consumable">耗</span>');
    if (item.cleanliness_state === "dirty" || item.cleanliness_state === "laundry") badges.push('<span class="item-badge laundry">待洗</span>');
    if (item.usage_state?.checkout_for?.length) badges.push('<span class="item-badge used">在用</span>');
    if (options.showSlot && item.slot) badges.push(`<span>${item.slot}</span>`);
    const detailId = item.item_id || item.id;
    return `<div class="item-card" onclick="showItemDetail('${detailId}')">
      ${img ? `<img class="item-card-img" src="${assetPreviewUrl(img)}" decoding="async" onerror="this.outerHTML='<div class=\\'item-card-img placeholder\\'>◈</div>'">` : '<div class="item-card-img placeholder">◈</div>'}
      <div class="item-card-name">${item.name}</div>
      <div class="item-card-meta">
        ${item.quantity > 1 ? `<span>×${item.quantity}</span>` : ""}
        ${badges.join("")}
      </div>
    </div>`;
  }).join("");
}

function switchCollectionTab(id) { currentCollectionTab = id; renderCollections(); }

// ── 衣柜面板 ──────────────────────────────────
function renderCloset() {
  const collections = collectionsData?.collections || snapshotData?.collections || {};
  const outfits = collections.outfits || [];
  const presets = collections.outfit_presets || [];
  const allItems = collections.items || [];
  // 当前穿搭
  const currentEl = document.getElementById("current-outfit");
  const current = outfits.find(o => o.status === "active") || outfits[0];
  if (current) {
    const itemIds = current.item_ids || [];
    const wornItems = itemIds.map(id => allItems.find(item => item.id === id)).filter(Boolean);
    currentEl.innerHTML = wornItems.length
      ? renderItemCards(wornItems)
      : `<div class="outfit-preset-card"><span>${current.context?.query_text || current.occasion || "当前穿着"}</span><span style="color:var(--cyan)">${itemIds.length} 件</span></div>`;
  } else {
    currentEl.innerHTML = '<div class="outfit-empty">当前没有着装记录</div>';
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
  blip(name === "stage" ? "switch" : "open");
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
function showToast(msg, kind = "info", ms = 4600) {
  const host = document.getElementById("toast-host");
  if (!host) return;
  const el = document.createElement("div");
  el.className = `toast toast-${kind}`;
  el.textContent = msg;
  host.appendChild(el);
  requestAnimationFrame(() => el.classList.add("show"));
  setTimeout(() => { el.classList.remove("show"); setTimeout(() => el.remove(), 350); }, ms);
}

function pulseHeartbeat() {
  // dedicated overlay so the snapshot re-render (which rebuilds stage-scene's
  // className) can't wipe the flash mid-animation.
  const flash = document.getElementById("tick-flash");
  if (flash) { flash.classList.remove("flash"); void flash.offsetWidth; flash.classList.add("flash"); }
}

// Summarize what a heartbeat tick actually did, for the feedback toast.
function tickSummary(d) {
  d = d || {};
  const parts = [];
  const rr = d.resource_recovery || {};
  if (rr.gap) parts.push(`补算缺口 ${rr.gap.elapsed_min}分`);
  else if (rr.settled_minutes > 0) parts.push(`结算 ${rr.settled_minutes}分`);
  const m = d.meals || {};
  if (m.derived && m.derived.length) parts.push(`加餐:${m.derived.join("/")}`);
  if (m.skipped && m.skipped.length) parts.push(`漏餐:${m.skipped.join("/")}`);
  const done = (d.completed || []).filter(c => c && (c.commit || c.execution_decision || c.sleep_wake_commit || c.sleep_start_commit)).length;
  if (done) parts.push(`处理 ${done} 项到期`);
  const rel = d.delayed_reply_release || {};
  if (rel.released_count > 0) parts.push(`释放 ${rel.released_count} 条延迟回复`);
  if (!parts.length) parts.push("暂无新变化(等下一次心跳)");
  return "心跳 ✓ " + parts.join(" · ");
}

async function doAction(action, payload = {}) {
  try {
    pulseHeartbeat();
    const res = await fetch(`${API}/api/action`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, payload }),
    });
    const data = await res.json();
    if (data.ok === false) {
      // read-only DB or error — make it visible instead of failing silently.
      showToast(data.message || data.error || "操作未生效", "warn", 5200);
      return;
    }
    if (action === "tick") showToast(tickSummary(data), "ok");
    else if (action === "start") showToast("已开启 LifeEngine · " + tickSummary(data.tick), "ok");
    else if (action === "call") showToast("已发起 call(唤醒/插话)", "ok");
    else showToast("✓ 已执行", "ok");
    loadSnapshot();
  } catch (err) {
    console.error("Action error:", err);
    showToast("网络错误,操作未送达", "warn");
  }
}

function doEnginePrimaryAction() {
  const control = snapshotData?.control || {};
  const action = control.engine_state === "active" ? "tick" : "start";
  return doAction(action);
}

// ── 辅助函数 ──────────────────────────────────
function engineDisplayState(control = {}, state = {}, currentEvent = null) {
  const active = control.engine_state === "active";
  const activeModes = new Set(["busy", "asleep", "napping", "dreaming", "uninterruptible_event"]);
  const running = active && (!!currentEvent || activeModes.has(state.mode));
  if (running) return { active, running, label: "进行中", className: "running" };
  if (active) return { active, running, label: "已开启", className: "active" };
  if (control.engine_state === "paused") return { active, running, label: "已暂停", className: "paused" };
  return { active, running, label: control.engine_state || "未开启", className: "paused" };
}

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

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
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

// Avatar/skin assets resolve through /api/avatar so a host can override the
// bundled default character (明灯) without touching code. Non-avatar static
// files should use /static/assets directly.
function staticAssetUrl(name) {
  return `/api/avatar/${name}?v=${reloadSerial}`;
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
