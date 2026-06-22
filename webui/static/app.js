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

// 世界地图交互状态。作用域仅限当前 WebUI 页面生命周期；数据源来自 snapshotData.world_model.map，
// 临时 viewBox/mark/settings 不落库，保存标记或设定时才通过 worldAction 写入 LifeEngine。
let worldMapState = {
  mapKey: null,
  viewBox: null,
  markMode: false,
  dragging: false,
  dragStart: null,
  selectedMarkerId: null,
  hoverPoint: null,
  pendingMarker: null,
  settingsOpen: false,
};

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
  renderCampaigns();
  renderWorldModel();
  renderSocialWorld();
  renderInnerLife();
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
  const identity = snapshotData.identity || {};
  const displayName = identity.name || owner.owner_id || "—";
  document.getElementById("agent-portrait").src = staticAssetUrl("default-agent-reference.jpg");
  const nameEl = document.getElementById("char-name");
  nameEl.textContent = displayName;
  nameEl.title = "点击改名(改 Canon 身份名,不动内部 owner_id)";
  nameEl.style.cursor = "pointer";
  nameEl.onclick = () => {
    const next = (window.prompt("给她起个名字(Canon 身份名):", identity.name || "") || "").trim();
    if (next && next !== identity.name) doAction("rename", { name: next });
  };
  document.getElementById("char-title").textContent = identity.role || avatar.label || avatar.scene || state.mode || "—";
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
  const vitals = resources.filter(r => ["energy", "mood", "fatigue"].includes(r.resource_key));
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
  const currencies = resources.filter(r => !["energy", "mood", "fatigue"].includes(r.resource_key));
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
  if (actor) actor.className = "actor pose-" + spriteState + (MOVING_STATES.has(spriteState) ? " moving" : RESTING_STATES.has(spriteState) ? " resting" : "");
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
    const activeCampaign = (snapshotData.campaigns || []).find(c => c.status === "active");
    if (currentEvent) {
      ribbon.innerHTML = `<span class="qr-tag">⚔ 当前</span>${escapeHtml(currentEvent.title)}`;
      ribbon.classList.remove("hidden");
      ribbon.onclick = () => showEventDetail(currentEvent.id);
    } else if (activeCampaign) {
      const ph = activeCampaign.current_phase_title ? ` · ${escapeHtml(activeCampaign.current_phase_title)}` : "";
      ribbon.innerHTML = `<span class="qr-tag">🗺 资料片</span>${escapeHtml(activeCampaign.title)}${ph}`;
      ribbon.classList.remove("hidden");
      ribbon.onclick = () => switchOverlay("campaigns");
    } else {
      ribbon.classList.add("hidden");
      ribbon.onclick = null;
    }
  }

  // 生命力宝珠
  const orbs = document.getElementById("vital-orbs");
  if (orbs) {
    const resources = snapshotData.resources || [];
    const want = [["energy", "精"], ["mood", "心"]];
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
  document.getElementById("dlg-name").textContent = (snapshotData.identity || {}).name || owner.owner_id || "—";
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
  const intentId = ob ? (ob.intent_id || null) : (it ? it.id : null);
  const full = String(text);
  const tag = kind === "want-say" ? "📣 想对你说" : "💭 想找机会说";
  const shown = full.length > 42 ? full.slice(0, 42) + "…" : full;
  el.className = "speech-bubble " + kind;
  el.innerHTML = `<span class="sb-tag">${tag}</span><span class="sb-text">${escapeHtml(shown)}</span><span class="sb-close" title="不说了 / 关闭">✕</span>`;
  el.onclick = () => { switchOverlay("stage"); showToast((kind === "want-say" ? "📣 " : "💭 ") + full, "ok", 6500); };
  const closeBtn = el.querySelector(".sb-close");
  if (closeBtn) closeBtn.onclick = (e) => {
    e.stopPropagation();
    el.className = "speech-bubble hidden";          // hide immediately (observatory)
    if (intentId) doAction("proactive_dismiss", { intent_id: intentId });  // persist if writable
  };
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
  el.innerHTML = all.slice(0, 10).map(item => {
    const iid = item._t === "outbox" ? (item.intent_id || "") : (item.id || "");
    const sendBtn = (item._t === "outbox" && item.id) ? `<button class="pi-act send" title="标记为已送达" onclick="doAction('proactive_send',{outbox_id:'${item.id}'})">送达</button>` : "";
    const dropBtn = iid ? `<button class="pi-act drop" title="消掉这条" onclick="doAction('proactive_dismiss',{intent_id:'${iid}'})">消掉</button>` : "";
    return `<div class="proactive-card ${item._t === "outbox" ? "queued" : ""}">
      <div class="pi-type">${item.intent_type || item._t}</div>
      <div class="pi-summary">${(item.summary || "").slice(0, 60)}</div>
      <div class="pi-acts">${sendBtn}${dropBtn}</div>
    </div>`;
  }).join("");
}

// ── 右栏:最近事件 ─────────────────────────────
function renderRecentEvents() {
  const events = snapshotData.recent_events || [];
  const el = document.getElementById("recent-events");
  if (!events.length) {
    el.innerHTML = '<div class="empty-state">无近期事件</div>';
    return;
  }
  // 合并重复事项(同标题+状态+类别),用 ×N 角标代替重复刷屏
  const groups = []; const idx = {};
  for (const e of events) {
    const key = `${e.title}|${e.status}|${e.event_category || ""}`;
    if (idx[key] == null) { idx[key] = groups.length; groups.push({ e, count: 1 }); }
    else groups[idx[key]].count++;
  }
  el.innerHTML = groups.slice(0, 14).map(g => {
    const e = g.e;
    const cls = e.status === "completed" ? "completed" : "";
    const badge = g.count > 1 ? `<span class="re-badge">×${g.count}</span>` : "";
    return `<div class="recent-event ${cls}" onclick="showEventDetail('${e.id}')">
      <div class="re-title">${escapeHtml(e.title)}${badge}</div>
      <div class="re-meta"><span class="ev-status ${e.status}">${escapeHtml(e.status)}</span>${e.event_category ? " · " + escapeHtml(e.event_category) : ""}</div>
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

// ── 事变 / 资料片面板 ──────────────────────────
function renderCampaigns() {
  const camps = snapshotData.campaigns || [];
  const el = document.getElementById("campaigns-list");
  if (!el) return;
  if (!camps.length) {
    el.innerHTML = '<div class="empty-state">还没有在张罗的大事</div>';
    return;
  }
  el.innerHTML = camps.map(c => {
    const phases = c.phases || [];
    const cp = Number(c.current_phase) || 0;
    const resolved = c.status === "resolved";
    const pct = Math.max(0, Math.min(100, (Number(c.progress) || 0) * 100));
    const phaseChips = phases.map((p, i) => {
      const st = resolved || i < cp ? "done" : (i === cp ? "now" : "todo");
      return `<span class="phase-chip ${st}">${escapeHtml(p.title || ("第" + (i + 1) + "阶段"))}</span>`;
    }).join('<span class="phase-arrow">→</span>');
    return `<div class="campaign-card ${resolved ? "resolved" : "active"}">
      <div class="campaign-head">
        <span class="campaign-title">${escapeHtml(c.title)}</span>
        <span class="campaign-status">${resolved ? "已收尾" : "进行中"}</span>
      </div>
      ${c.description ? `<div class="campaign-desc">${escapeHtml(c.description)}</div>` : ""}
      <div class="campaign-phases">${phaseChips}</div>
      <div class="vital-bar"><div class="vital-bar-track"><div class="vital-bar-fill ${resolved ? "" : "high"}" style="width:${pct}%"></div></div></div>
    </div>`;
  }).join("");
}

// ── 世界本体 / 社会世界面板 ─────────────────────
function escapeJsArg(value) {
  return escapeHtml(JSON.stringify(String(value ?? "")));
}

function worldAction(worldActionName, payload = {}) {
  return doAction("world", { world_action: worldActionName, ...payload });
}

function socialAction(socialActionName, payload = {}) {
  return doAction("social", { social_action: socialActionName, ...payload });
}

function worldList(kind) {
  const data = snapshotData.world_model || {};
  if (kind === "profile") return data.profiles || [];
  if (kind === "region") return data.regions || [];
  if (kind === "place") return data.places || [];
  if (kind === "lore") return data.lore || [];
  if (kind === "faction_presence") return data.faction_presence || [];
  if (kind === "route") return data.routes || [];
  if (kind === "condition") return data.conditions || [];
  return [];
}

function worldFind(kind, objectId) {
  return worldList(kind).find(item => item.id === objectId) || null;
}

function worldEditButton(kind, item) {
  if (!item?.id) return "";
  return `<button class="world-mini-btn" title="编辑" onclick="worldEdit(${escapeJsArg(kind)}, ${escapeJsArg(item.id)})">改</button>`;
}

function worldArchiveButton(kind, item, label, cascade = false) {
  if (!item?.id) return "";
  return `<button class="world-mini-btn danger" title="归档" onclick="archiveWorldObject(${escapeJsArg(kind)}, ${escapeJsArg(item.id)}, ${escapeJsArg(label)}, ${cascade ? "true" : "false"})">归档</button>`;
}

function worldActionButtons(kind, item, label, cascade = false) {
  return `<div class="world-card-actions">${worldEditButton(kind, item)}${worldArchiveButton(kind, item, label, cascade)}</div>`;
}

function promptWorldValue(label, fallback = "", required = false) {
  const raw = window.prompt(label, fallback ?? "");
  if (raw === null) return null;
  const value = raw.trim();
  if (required && !value) {
    showToast(`${label} 不能为空`, "warn");
    return null;
  }
  return value;
}

// 读取世界观数字输入；空值保留为空，坐标/尺寸按当前地图画布范围限制。
function promptWorldNumber(label, fallback = "", required = false, minimum = 0, maximum = 100) {
  const raw = promptWorldValue(label, fallback == null ? "" : String(fallback), required);
  if (raw === null) return null;
  if (!raw && !required) return "";
  const value = Number(raw);
  if (!Number.isFinite(value)) {
    showToast(`${label} 必须是数字`, "warn");
    return null;
  }
  return Math.max(minimum, Math.min(maximum, value));
}

// 把用户输入合并回原有结构字段，避免编辑世界地图时擦掉其它 worldview 扩展。
function mergeMapFields(base = {}, fields = {}) {
  const map = { ...(base.map || {}) };
  Object.entries(fields).forEach(([key, value]) => {
    if (value !== "" && value != null) map[key] = value;
  });
  return { ...base, map };
}

// 兼容 list/dict 两种世界地图配置列表；用于保留用户已有图片层、资源和路线。
function normalizeWorldMapRecords(raw = []) {
  if (Array.isArray(raw)) return raw.filter(item => item && typeof item === "object").map(item => ({ ...item }));
  if (raw && typeof raw === "object") {
    return Object.entries(raw)
      .filter(([, value]) => value && typeof value === "object")
      .map(([id, value]) => ({ id, ...value }));
  }
  return [];
}

// 按 id 覆盖一条地图配置记录；调用方是世界档案编辑器，避免重建底图时丢失其它图层。
function upsertWorldMapRecord(raw = [], entry = {}) {
  const records = normalizeWorldMapRecords(raw);
  const id = entry.id || entry.key;
  const index = records.findIndex(item => item.id === id || item.key === id);
  if (index >= 0) records[index] = { ...records[index], ...entry };
  else records.push(entry);
  return records;
}

function promptWorldScope(item = {}) {
  const scopeKind = promptWorldValue("作用域 world / region / place", item.scope_kind || "world", true);
  if (scopeKind === null) return null;
  if (!["world", "region", "place"].includes(scopeKind)) {
    showToast("作用域必须是 world/region/place", "warn");
    return null;
  }
  let scopeId = "__world__";
  if (scopeKind !== "world") {
    scopeId = promptWorldValue(`${scopeKind} id`, item.scope_id || "", true);
    if (scopeId === null) return null;
  }
  return { scope_kind: scopeKind, scope_id: scopeId };
}

async function worldEdit(kind, objectId = null) {
  const item = objectId ? worldFind(kind, objectId) : {};
  if (objectId && !item) {
    showToast("找不到要编辑的世界对象", "warn");
    return;
  }
  const mapCanvas = snapshotData.world_model?.map?.canvas || {};
  const mapWidth = Number(mapCanvas.width) || 100;
  const mapHeight = Number(mapCanvas.height) || 100;
  let action = "";
  let payload = {};
  if (kind === "profile") {
    const mapCfg = item.rules?.map || {};
    const key = promptWorldValue("档案 key", item.key || "default", true);
    if (key === null) return;
    const title = promptWorldValue("档案标题", item.title || key, true);
    if (title === null) return;
    const summary = promptWorldValue("摘要", item.summary || "");
    if (summary === null) return;
    const background = promptWorldValue("背景正文", item.background_text || "");
    if (background === null) return;
    const mapTitle = promptWorldValue("地图标题", mapCfg.title || title || "世界地图");
    if (mapTitle === null) return;
    const width = promptWorldNumber("地图画布宽度", mapCfg.width ?? 100, true, 10, 10000);
    if (width === null) return;
    const height = promptWorldNumber("地图画布高度", mapCfg.height ?? 100, true, 10, 10000);
    if (height === null) return;
    const unit = promptWorldValue("地图单位", mapCfg.unit || "grid");
    if (unit === null) return;
    const projection = promptWorldValue("地图投影/坐标系", mapCfg.projection || "local_grid");
    if (projection === null) return;
    const baseLayer = normalizeWorldMapRecords(mapCfg.image_layers || mapCfg.layers).find(layer => layer.id === "base" || layer.asset_id === "base_map") || {};
    const baseAsset = normalizeWorldMapRecords(mapCfg.assets || mapCfg.asset_refs).find(asset => asset.id === "base_map") || {};
    const baseHref = promptWorldValue("底图资源路径/URL，可空", baseLayer.href || baseAsset.href || baseAsset.path || mapCfg.background_image || "");
    if (baseHref === null) return;
    const minZoom = promptWorldNumber("最小缩放", mapCfg.viewport?.min_zoom ?? 0.75, true, 0.1, 20);
    if (minZoom === null) return;
    const maxZoom = promptWorldNumber("最大缩放", mapCfg.viewport?.max_zoom ?? 6, true, minZoom, 50);
    if (maxZoom === null) return;
    const gridSize = promptWorldNumber("网格尺寸", mapCfg.grid?.size ?? Math.max(1, Math.min(width, height) / 10), true, 0.1, Math.max(width, height));
    if (gridSize === null) return;
    const nextMap = {
      ...mapCfg,
      title: mapTitle || title,
      width,
      height,
      unit: unit || "grid",
      projection: projection || "local_grid",
      viewport: {
        ...(mapCfg.viewport || {}),
        min_zoom: minZoom,
        max_zoom: maxZoom,
        default_zoom: Math.max(minZoom, Math.min(maxZoom, Number(mapCfg.viewport?.default_zoom) || 1)),
      },
      grid: { ...(mapCfg.grid || {}), visible: true, size: gridSize },
    };
    if (baseHref) {
      nextMap.assets = upsertWorldMapRecord(mapCfg.assets || mapCfg.asset_refs, {
        id: "base_map",
        name: "地图底图",
        kind: "image",
        href: baseHref,
        path: baseHref,
      });
      nextMap.image_layers = upsertWorldMapRecord(mapCfg.image_layers || mapCfg.layers, {
        id: "base",
        name: "底图",
        asset_id: "base_map",
        href: baseHref,
        x: 0,
        y: 0,
        width,
        height,
        opacity: 1,
        order: -10,
      });
    }
    action = "profile";
    payload = {
      key,
      title,
      summary,
      background_text: background,
      rules: { ...(item.rules || {}), map: nextMap },
      evidence: item.evidence || {},
    };
  } else if (kind === "region") {
    const key = promptWorldValue("区域 key", item.key || "", true);
    if (key === null) return;
    const name = promptWorldValue("区域/城池名称", item.name || key, true);
    if (name === null) return;
    const regionType = promptWorldValue("区域类型", item.region_type || "region");
    if (regionType === null) return;
    const parentRegionId = promptWorldValue("父区域 id，可空", item.parent_region_id || "");
    if (parentRegionId === null) return;
    const summary = promptWorldValue("摘要", item.summary || "");
    if (summary === null) return;
    const content = promptWorldValue("正文", item.content || "");
    if (content === null) return;
    const terrain = promptWorldValue("地形 terrain", item.traits?.map?.terrain || item.traits?.terrain || "urban");
    if (terrain === null) return;
    const x = promptWorldNumber(`地图 x 0..${mapWidth}`, item.traits?.map?.x ?? mapWidth * 0.18, true, 0, mapWidth);
    if (x === null) return;
    const y = promptWorldNumber(`地图 y 0..${mapHeight}`, item.traits?.map?.y ?? mapHeight * 0.18, true, 0, mapHeight);
    if (y === null) return;
    const width = promptWorldNumber(`地图宽度 0..${mapWidth}`, item.traits?.map?.width ?? mapWidth * 0.5, true, 1, mapWidth);
    if (width === null) return;
    const height = promptWorldNumber(`地图高度 0..${mapHeight}`, item.traits?.map?.height ?? mapHeight * 0.36, true, 1, mapHeight);
    if (height === null) return;
    action = "region";
    payload = {
      key,
      name,
      region_type: regionType || "region",
      parent_region_id: parentRegionId || null,
      summary,
      content,
      traits: mergeMapFields({ ...(item.traits || {}), terrain: terrain || "custom" }, { terrain, x, y, width, height }),
      evidence: item.evidence || {},
    };
  } else if (kind === "place") {
    const firstRegion = (snapshotData.world_model?.regions || [])[0];
    const key = promptWorldValue("地点 key", item.key || "", true);
    if (key === null) return;
    const name = promptWorldValue("地点名称", item.name || key, true);
    if (name === null) return;
    const placeType = promptWorldValue("地点类型", item.place_type || "place");
    if (placeType === null) return;
    const regionId = promptWorldValue("所属区域 id，可空", item.region_id || firstRegion?.id || "");
    if (regionId === null) return;
    const parentPlaceId = promptWorldValue("父地点 id，可空", item.parent_place_id || "");
    if (parentPlaceId === null) return;
    const summary = promptWorldValue("摘要", item.summary || "");
    if (summary === null) return;
    const content = promptWorldValue("正文", item.content || "");
    if (content === null) return;
    const terrain = promptWorldValue("地形 terrain", item.coordinates?.terrain || item.traits?.terrain || "urban");
    if (terrain === null) return;
    const x = promptWorldNumber(`地图 x 0..${mapWidth}`, item.coordinates?.x ?? mapWidth / 2, true, 0, mapWidth);
    if (x === null) return;
    const y = promptWorldNumber(`地图 y 0..${mapHeight}`, item.coordinates?.y ?? mapHeight / 2, true, 0, mapHeight);
    if (y === null) return;
    const importance = promptWorldNumber("重要度 0..100", item.coordinates?.importance ?? item.traits?.importance ?? 50, true);
    if (importance === null) return;
    const defaultImportant = item.traits?.important || item.traits?.landmark || item.coordinates?.important || importance >= 70;
    const importantRaw = promptWorldValue("重要建筑/地标？y/n", defaultImportant ? "y" : "n", true);
    if (importantRaw === null) return;
    const isImportant = /^(y|yes|true|1|是|重要)$/i.test(importantRaw);
    const markerRole = promptWorldValue("地图标记 role", item.coordinates?.marker_role || (isImportant ? "important_building" : "place"));
    if (markerRole === null) return;
    const icon = promptWorldValue("地图图标，可空", item.coordinates?.icon || "");
    if (icon === null) return;
    const markerAsset = promptWorldValue("标记图片资源路径/URL，可空", item.coordinates?.asset_url || item.coordinates?.image || "");
    if (markerAsset === null) return;
    action = "place";
    payload = {
      key,
      name,
      place_type: placeType || "place",
      region_id: regionId || null,
      parent_place_id: parentPlaceId || null,
      summary,
      content,
      coordinates: mergeMapFields({
        ...(item.coordinates || {}),
        x,
        y,
        terrain,
        importance,
        important: isImportant,
        marker_role: markerRole || "place",
        icon: icon || undefined,
        asset_url: markerAsset || undefined,
      }, {}),
      traits: { ...(item.traits || {}), terrain, important: isImportant || Boolean(item.traits?.important) },
      evidence: item.evidence || {},
    };
  } else if (kind === "lore") {
    const key = promptWorldValue("知识 key", item.key || "", true);
    if (key === null) return;
    const title = promptWorldValue("知识标题", item.title || key, true);
    if (title === null) return;
    const loreType = promptWorldValue("知识类型", item.lore_type || "background");
    if (loreType === null) return;
    const scope = promptWorldScope(item);
    if (!scope) return;
    const content = promptWorldValue("正文", item.content || "");
    if (content === null) return;
    const tagsRaw = promptWorldValue("标签，逗号分隔", (item.tags || []).join(","));
    if (tagsRaw === null) return;
    action = "upsert_lore";
    payload = { key, title, lore_type: loreType || "background", ...scope, content, tags: tagsRaw.split(",").map(s => s.trim()).filter(Boolean), evidence: item.evidence || {} };
  } else if (kind === "faction_presence") {
    const socialEntities = snapshotData.social_world?.entities || [];
    const fallbackFaction = socialEntities.find(e => ["faction", "organization", "club"].includes(e.entity_kind)) || socialEntities[0];
    const factionEntityId = promptWorldValue("势力 entity id", item.faction_entity_id || fallbackFaction?.id || "", true);
    if (factionEntityId === null) return;
    const scope = promptWorldScope(item);
    if (!scope) return;
    const influenceRaw = promptWorldValue("影响力 -100..100", String(item.influence ?? 0), true);
    if (influenceRaw === null) return;
    const influence = Number(influenceRaw);
    if (!Number.isFinite(influence)) {
      showToast("影响力必须是数字", "warn");
      return;
    }
    const stance = promptWorldValue("立场", item.stance || "neutral");
    if (stance === null) return;
    const summary = promptWorldValue("摘要", item.summary || "");
    if (summary === null) return;
    const content = promptWorldValue("正文", item.content || "");
    if (content === null) return;
    action = "upsert_faction_presence";
    payload = { faction_entity_id: factionEntityId, ...scope, influence, stance, summary, content, evidence: item.evidence || {} };
  } else if (kind === "route") {
    const key = promptWorldValue("路线 key", item.key || "", true);
    if (key === null) return;
    const name = promptWorldValue("路线名称", item.name || key, true);
    if (name === null) return;
    const routeType = promptWorldValue("路线类型", item.route_type || "road");
    if (routeType === null) return;
    const fromKind = promptWorldValue("起点 scope_kind，可空", item.from_scope_kind || "place");
    if (fromKind === null) return;
    const fromId = fromKind ? promptWorldValue("起点 scope_id", item.from_scope_id || "") : "";
    if (fromId === null) return;
    const toKind = promptWorldValue("终点 scope_kind，可空", item.to_scope_kind || "place");
    if (toKind === null) return;
    const toId = toKind ? promptWorldValue("终点 scope_id", item.to_scope_id || "") : "";
    if (toId === null) return;
    const travelMode = promptWorldValue("交通方式", item.travel_mode || "walk");
    if (travelMode === null) return;
    const durationMinutes = promptWorldNumber("耗时分钟，可空", item.duration_minutes ?? "", false, 0, 100000);
    if (durationMinutes === null) return;
    const riskLevel = promptWorldNumber("风险 0..100", item.risk_level ?? 0, true, 0, 100);
    if (riskLevel === null) return;
    const pointsRaw = promptWorldValue("折线点 x,y;x,y，可空", (item.points || []).map(p => `${p.x},${p.y}`).join(";"));
    if (pointsRaw === null) return;
    const points = pointsRaw.split(";").map(pair => {
      const [x, y] = pair.split(",").map(v => Number(v.trim()));
      return Number.isFinite(x) && Number.isFinite(y) ? { x: Math.max(0, Math.min(mapWidth, x)), y: Math.max(0, Math.min(mapHeight, y)) } : null;
    }).filter(Boolean);
    action = "route";
    payload = {
      key,
      name,
      route_type: routeType || "road",
      from_scope_kind: fromKind || null,
      from_scope_id: fromId || null,
      to_scope_kind: toKind || null,
      to_scope_id: toId || null,
      travel_mode: travelMode || null,
      duration_minutes: durationMinutes === "" ? null : durationMinutes,
      risk_level: riskLevel,
      points,
      traits: item.traits || {},
      evidence: item.evidence || {},
      status: item.status || "active",
    };
  } else if (kind === "condition") {
    const key = promptWorldValue("状态 key", item.key || "", true);
    if (key === null) return;
    const title = promptWorldValue("状态标题", item.title || key, true);
    if (title === null) return;
    const conditionType = promptWorldValue("状态类型", item.condition_type || "state");
    if (conditionType === null) return;
    const scope = promptWorldScope(item);
    if (!scope) return;
    const severity = promptWorldNumber("严重度 0..100", item.severity ?? 0, true, 0, 100);
    if (severity === null) return;
    const intensity = promptWorldNumber("强度 0..100", item.intensity ?? 0, true, 0, 100);
    if (intensity === null) return;
    const summary = promptWorldValue("摘要", item.summary || "");
    if (summary === null) return;
    const content = promptWorldValue("正文", item.content || "");
    if (content === null) return;
    const startsAt = promptWorldValue("开始时间，可空", item.starts_at || "");
    if (startsAt === null) return;
    const endsAt = promptWorldValue("结束时间，可空", item.ends_at || "");
    if (endsAt === null) return;
    const status = promptWorldValue("状态 active/resolved/expired/archived", item.status || "active", true);
    if (status === null) return;
    action = "condition";
    payload = {
      key,
      title,
      condition_type: conditionType || "state",
      ...scope,
      severity,
      intensity,
      summary,
      content,
      starts_at: startsAt || null,
      ends_at: endsAt || null,
      payload: item.payload || {},
      evidence: item.evidence || {},
      status,
    };
  } else {
    showToast("未知世界对象类型", "warn");
    return;
  }
  await worldAction(action, payload);
}

function worldQuickCreate(kind) {
  return worldEdit(kind, null);
}

async function archiveWorldObject(kind, objectId, label, cascade = false) {
  if (!objectId) {
    showToast("缺少对象 id，无法归档", "warn");
    return;
  }
  const hint = cascade ? "，并级联归档子项和作用域条目" : "";
  if (!window.confirm(`归档 ${label || kind}${hint}？`)) return;
  await worldAction("archive", { object_kind: kind, object_id: objectId, cascade });
}

// 规整地图数值输入。调用方是地图渲染和交互计算；输出始终是有限数字，避免坏数据进入 SVG 属性。
function mapNum(value, fallback = 0) {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

// 格式化 SVG 坐标。调用方是地图 SVG 字符串生成；输出短小数字字符串，降低 DOM 噪声。
function fmtMapNum(value, digits = 2) {
  const n = mapNum(value, 0);
  const fixed = n.toFixed(digits);
  return fixed.replace(/\.?0+$/, "");
}

// 读取地图画布配置。输入来自 world_model.map.canvas；输出是 WebUI 坐标系和标题/单位显示的唯一来源。
function worldMapCanvas(map = {}) {
  const canvas = map.canvas || {};
  return {
    width: Math.max(10, mapNum(canvas.width, 100)),
    height: Math.max(10, mapNum(canvas.height, 100)),
    title: canvas.title || "世界地图",
    unit: canvas.unit || "grid",
    backgroundColor: canvas.background_color || "#15181c",
  };
}

// 生成地图视口状态 key。画布身份变化时调用方会重置 pan/zoom，避免旧视角套到新地图。
function worldMapKey(map = {}) {
  const canvas = worldMapCanvas(map);
  return `${canvas.title}:${canvas.width}:${canvas.height}`;
}

// 限制地图视口边界。输入是临时 viewBox；输出不会越过 canvas/zoom 约束，供缩放和拖拽共用。
function clampWorldMapViewBox(view, map = {}) {
  const canvas = worldMapCanvas(map);
  const width = Math.max(canvas.width / Math.max(1, mapNum(map.viewport?.max_zoom, 6)), Math.min(view.width, canvas.width / Math.max(0.1, mapNum(map.viewport?.min_zoom, 1))));
  const height = Math.max(canvas.height / Math.max(1, mapNum(map.viewport?.max_zoom, 6)), Math.min(view.height, canvas.height / Math.max(0.1, mapNum(map.viewport?.min_zoom, 1))));
  const minX = width >= canvas.width ? (canvas.width - width) / 2 : 0;
  const maxX = width >= canvas.width ? minX : canvas.width - width;
  const minY = height >= canvas.height ? (canvas.height - height) / 2 : 0;
  const maxY = height >= canvas.height ? minY : canvas.height - height;
  return {
    x: Math.max(minX, Math.min(maxX, mapNum(view.x, 0))),
    y: Math.max(minY, Math.min(maxY, mapNum(view.y, 0))),
    width,
    height,
  };
}

// 计算默认视口。输入来自 map.viewport.default_*；输出用于首次渲染和“回到默认视角”。
function defaultWorldMapViewBox(map = {}) {
  const canvas = worldMapCanvas(map);
  const viewport = map.viewport || {};
  const zoom = Math.max(mapNum(viewport.min_zoom, 1), Math.min(mapNum(viewport.max_zoom, 6), mapNum(viewport.default_zoom, 1)));
  const width = canvas.width / Math.max(0.1, zoom);
  const height = canvas.height / Math.max(0.1, zoom);
  const center = viewport.default_center || {};
  return clampWorldMapViewBox({
    x: mapNum(center.x, canvas.width / 2) - width / 2,
    y: mapNum(center.y, canvas.height / 2) - height / 2,
    width,
    height,
  }, map);
}

// 确保当前地图有可用视口。调用方是 renderWorldMap；只更新页面内临时状态，不写数据库。
function ensureWorldMapView(map = {}) {
  const key = worldMapKey(map);
  if (worldMapState.mapKey !== key || !worldMapState.viewBox) {
    worldMapState.mapKey = key;
    worldMapState.viewBox = defaultWorldMapViewBox(map);
    worldMapState.selectedMarkerId = null;
    worldMapState.dragging = false;
  } else {
    worldMapState.viewBox = clampWorldMapViewBox(worldMapState.viewBox, map);
  }
  return worldMapState.viewBox;
}

// 规整地图图片引用。输入可为 URL、/static、data:image 或本地资产路径；输出给 SVG image.href。
function safeMapImageHref(raw) {
  const value = String(raw || "").trim();
  if (!value || /^javascript:/i.test(value)) return "";
  if (/^(https?:|data:image\/|blob:)/i.test(value) || value.startsWith("/")) return value;
  return assetUrl(value);
}

// 把世界观 role/terrain 转成安全 CSS class token，避免用户输入破坏选择器或样式边界。
function worldMapClassToken(value, fallback = "custom") {
  return String(value || fallback).replace(/[^a-zA-Z0-9_-]/g, "_");
}

// 生成地形样式类名。调用方是地形层渲染，保持 terrain key 与 CSS 调色分离。
function worldMapTerrainClass(key) {
  return `terrain_${worldMapClassToken(key)}`;
}

// 渲染地图网格层。输入来自 map.grid/canvas；输出 SVG line 组，过密时自动增大步长。
function renderWorldMapGrid(map = {}) {
  const canvas = worldMapCanvas(map);
  const grid = map.grid || {};
  if (grid.visible === false) return "";
  let size = Math.max(0.1, mapNum(grid.size, Math.min(canvas.width, canvas.height) / 10));
  const maxLines = 90;
  while ((canvas.width / size) + (canvas.height / size) > maxLines) size *= 2;
  const majorEvery = Math.max(1, Math.round(mapNum(grid.major_every, 5)));
  const lines = [];
  for (let x = 0, i = 0; x <= canvas.width + 0.0001; x += size, i += 1) {
    lines.push(`<line class="${i % majorEvery === 0 ? "major" : ""}" x1="${fmtMapNum(x)}" y1="0" x2="${fmtMapNum(x)}" y2="${fmtMapNum(canvas.height)}"></line>`);
  }
  for (let y = 0, i = 0; y <= canvas.height + 0.0001; y += size, i += 1) {
    lines.push(`<line class="${i % majorEvery === 0 ? "major" : ""}" x1="0" y1="${fmtMapNum(y)}" x2="${fmtMapNum(canvas.width)}" y2="${fmtMapNum(y)}"></line>`);
  }
  return `<g class="map-grid">${lines.join("")}</g>`;
}

// 渲染图片图层。输入来自 map.image_layers；输出 SVG image，不校验文件存在性，加载交给浏览器。
function renderWorldMapImageLayers(map = {}) {
  return (map.image_layers || []).map(layer => {
    const href = safeMapImageHref(layer.href || layer.path || layer.url);
    if (!href) return "";
    return `<image class="map-image-layer" href="${escapeHtml(href)}" x="${fmtMapNum(layer.x)}" y="${fmtMapNum(layer.y)}"
      width="${fmtMapNum(layer.width, 3)}" height="${fmtMapNum(layer.height, 3)}" opacity="${fmtMapNum(layer.opacity ?? 1, 3)}"
      preserveAspectRatio="none"><title>${escapeHtml(layer.name || "地图图层")}</title></image>`;
  }).join("");
}

// 渲染结构化地形层。输入来自 profile/region 派生结构；输出可点击标记之下的 SVG 区块。
function renderWorldMapTerrain(map = {}) {
  return (map.terrain || []).map(t => {
    const x = mapNum(t.x), y = mapNum(t.y);
    const w = Math.max(1, mapNum(t.width, 1));
    const h = Math.max(1, mapNum(t.height, 1));
    return `<g class="map-terrain ${worldMapTerrainClass(t.terrain)}">
      <rect x="${fmtMapNum(x)}" y="${fmtMapNum(y)}" width="${fmtMapNum(w)}" height="${fmtMapNum(h)}" rx="1.2"></rect>
      ${t.name ? `<text x="${fmtMapNum(x + Math.min(8, w * 0.06))}" y="${fmtMapNum(y + Math.min(8, h * 0.14))}" class="map-region-label">${escapeHtml(t.name)}</text>` : ""}
    </g>`;
  }).join("");
}

// 渲染路线/道路层。输入来自 profile.rules.map.routes；输出 SVG polyline，用于道路、河道或边界线。
function renderWorldMapRoutes(map = {}) {
  return (map.routes || []).map(route => {
    const points = (route.points || []).map(p => `${fmtMapNum(p.x)},${fmtMapNum(p.y)}`).join(" ");
    if (!points) return "";
    const style = route.color ? ` style="--route-color:${escapeHtml(route.color)}"` : "";
    const dash = route.dash ? ` stroke-dasharray="${escapeHtml(route.dash)}"` : "";
    return `<polyline class="map-route route-${worldMapClassToken(route.role, "road")}" points="${points}"${dash}${style}>
      <title>${escapeHtml(route.name || "路线")}</title>
    </polyline>`;
  }).join("");
}

// 渲染地点标记层。输入来自 world places；输出可点选的 SVG marker，并提供透明命中区。
function renderWorldMapMarkers(map = {}) {
  return (map.markers || []).map(m => {
    const x = mapNum(m.x), y = mapNum(m.y);
    const role = m.marker_role || "place";
    const selected = worldMapState.selectedMarkerId === m.id ? " selected" : "";
    const important = role === "important_building" || m.is_important;
    const title = `${m.name || m.key || "地点"} · ${m.terrain || ""}`;
    const label = escapeHtml(m.name || m.key || "");
    const asset = safeMapImageHref(m.asset_url);
    const size = Math.max(0.6, Math.min(3, mapNum(m.size, 1)));
    const radius = important ? 3.2 * size : 2.2 * size;
    const image = asset
      ? `<image class="map-marker-image" href="${escapeHtml(asset)}" x="${fmtMapNum(-radius)}" y="${fmtMapNum(-radius)}" width="${fmtMapNum(radius * 2)}" height="${fmtMapNum(radius * 2)}" preserveAspectRatio="xMidYMid meet"></image>`
      : "";
    const icon = m.icon ? `<text class="map-marker-icon" x="0" y="1.4">${escapeHtml(m.icon)}</text>` : "";
    const shape = important
      ? `<path d="M0 ${fmtMapNum(-radius)} L${fmtMapNum(radius)} 0 L0 ${fmtMapNum(radius)} L${fmtMapNum(-radius)} 0 Z"></path>`
      : `<circle r="${fmtMapNum(radius)}"></circle>`;
    return `<g class="map-marker role-${worldMapClassToken(role, "place")}${important ? " important" : ""}${selected}" transform="translate(${fmtMapNum(x)} ${fmtMapNum(y)})" data-place-id="${escapeHtml(m.id || "")}">
      <circle class="map-marker-hit" r="${fmtMapNum(Math.max(7, radius + 5))}"></circle>
      ${image || shape}${icon}
      <text x="${fmtMapNum(radius + 2)}" y="${fmtMapNum(-radius * 0.45)}">${label}</text>
      <title>${escapeHtml(title)}</title>
    </g>`;
  }).join("");
}

// 渲染明灯当前位置。输入来自 map.actor；只展示结构化定位成功的地点，不从文本猜测位置。
function renderWorldMapActor(map = {}) {
  const actor = map.actor || {};
  if (actor.status !== "located") return "";
  const x = mapNum(actor.x), y = mapNum(actor.y);
  return `<g class="map-actor" transform="translate(${fmtMapNum(x)} ${fmtMapNum(y)})">
    <circle r="5.4"></circle><circle r="1.9"></circle>
    <text x="6.2" y="2">${escapeHtml(actor.label || "明灯")}</text>
    <title>${escapeHtml((actor.label || "明灯") + " · " + (actor.place_name || ""))}</title>
  </g>`;
}

// 渲染待保存标记。输入来自 worldMapState.pendingMarker；输出只存在于前端，保存前不落库。
function renderPendingWorldMapMarker() {
  const pending = worldMapState.pendingMarker;
  if (!pending) return "";
  return `<g class="map-marker pending" transform="translate(${fmtMapNum(pending.x)} ${fmtMapNum(pending.y)})">
    <circle class="map-marker-hit" r="8"></circle>
    <circle r="2.8"></circle>
    <text x="5" y="-2">${escapeHtml(pending.name || "新地点")}</text>
  </g>`;
}

// 解析检查区当前地点。优先使用用户点选标记，其次回落到明灯所在地点，供 inspector 展示。
function selectedWorldMapMarker(map = {}) {
  const markers = map.markers || [];
  return markers.find(m => m.id === worldMapState.selectedMarkerId) || markers.find(m => m.id === map.actor?.place_id) || null;
}

// 渲染地图检查区。根据 settings/pending/selected 状态输出设定表单、新标记表单或地点摘要。
function renderWorldMapInspector(map = {}) {
  if (worldMapState.settingsOpen) {
    const profile = (snapshotData.world_model?.profiles || [])[0] || {};
    const mapCfg = profile.rules?.map || {};
    const canvas = worldMapCanvas(map);
    const baseLayer = (map.image_layers || [])[0] || {};
    const baseHref = baseLayer.href || map.assets?.[0]?.href || mapCfg.background_image || "";
    return `<div class="world-map-inspector marker-editor">
      <div class="world-map-inspector-title">地图设定</div>
      <div class="world-map-form-grid">
        <label>标题<input id="world-map-setting-title" value="${escapeHtml(canvas.title)}"></label>
        <label>底图资源<input id="world-map-setting-base" value="${escapeHtml(baseHref)}"></label>
        <label>宽度<input id="world-map-setting-width" type="number" min="10" max="10000" value="${escapeHtml(canvas.width)}"></label>
        <label>高度<input id="world-map-setting-height" type="number" min="10" max="10000" value="${escapeHtml(canvas.height)}"></label>
        <label>单位<input id="world-map-setting-unit" value="${escapeHtml(canvas.unit)}"></label>
        <label>投影<input id="world-map-setting-projection" value="${escapeHtml(map.canvas?.projection || "local_grid")}"></label>
        <label>最大缩放<input id="world-map-setting-max-zoom" type="number" min="1" max="50" step="0.1" value="${escapeHtml(map.viewport?.max_zoom ?? 6)}"></label>
        <label>网格<input id="world-map-setting-grid" type="number" min="0.1" step="0.1" value="${escapeHtml(map.grid?.size ?? 10)}"></label>
      </div>
      <div class="world-map-inspector-actions">
        <button class="world-mini-btn create" data-map-save-settings>保存设定</button>
        <button class="world-mini-btn" data-map-cancel-settings>取消</button>
      </div>
    </div>`;
  }
  const pending = worldMapState.pendingMarker;
  if (pending) {
    const roles = ["place", "important_building", "quest", "danger", "resource", "camp", "portal"];
    const options = roles.map(role => `<option value="${role}"${pending.role === role ? " selected" : ""}>${escapeHtml(role)}</option>`).join("");
    return `<div class="world-map-inspector marker-editor">
      <div class="world-map-inspector-title">新地图标记</div>
      <div class="world-map-form-grid">
        <label>名称<input id="world-map-mark-name" value="${escapeHtml(pending.name || "新地点")}"></label>
        <label>Key<input id="world-map-mark-key" value="${escapeHtml(pending.key || "")}"></label>
        <label>类型<select id="world-map-mark-role">${options}</select></label>
        <label>地形<input id="world-map-mark-terrain" value="${escapeHtml(pending.terrain || "custom")}"></label>
      </div>
      <div class="world-map-inspector-meta">${fmtMapNum(pending.x)}, ${fmtMapNum(pending.y)}${pending.region_name ? ` · ${escapeHtml(pending.region_name)}` : ""}</div>
      <div class="world-map-inspector-actions">
        <button class="world-mini-btn create" data-map-save-marker>保存标记</button>
        <button class="world-mini-btn" data-map-cancel-marker>取消</button>
      </div>
    </div>`;
  }
  const marker = selectedWorldMapMarker(map);
  if (!marker) return '<div class="world-map-inspector muted">未选中标记</div>';
  return `<div class="world-map-inspector">
    <div class="world-map-inspector-title">${escapeHtml(marker.name || marker.key || "地点")}</div>
    <div class="world-map-inspector-meta">${escapeHtml(marker.marker_role || "place")} · ${escapeHtml(marker.terrain || "terrain")} · ${fmtMapNum(marker.x)}, ${fmtMapNum(marker.y)}</div>
    <div class="world-map-inspector-actions">${marker.id ? `<button class="world-mini-btn" onclick="worldEdit(${escapeJsArg("place")}, ${escapeJsArg(marker.id)})">编辑地点</button>` : ""}</div>
  </div>`;
}

// 渲染地图图例。输入来自 map.legend 和 actor；输出 marker role 与明灯状态的紧凑说明。
function renderWorldMapLegend(map = {}) {
  const actor = map.actor || {};
  const roles = map.legend?.marker_roles || {};
  const roleLegend = Object.entries(roles).slice(0, 8)
    .map(([role, label]) => `<span><i class="map-dot role-${worldMapClassToken(role, "place")}"></i>${escapeHtml(label)}</span>`)
    .join("");
  return `<div class="world-map-legend">
    <span><i class="map-dot actor"></i>${escapeHtml(actor.status === "located" ? `${actor.label || "明灯"} · ${actor.place_name || ""}` : "明灯位置未知")}</span>
    ${roleLegend}
  </div>`;
}

// 执行地图缩放。输入是缩放倍率和可选锚点；副作用仅更新页面 viewBox 并重绘地图。
function worldMapZoom(map = {}, factor = 1, anchor = null) {
  const view = ensureWorldMapView(map);
  const cx = anchor?.x ?? (view.x + view.width / 2);
  const cy = anchor?.y ?? (view.y + view.height / 2);
  const nextWidth = view.width / factor;
  const nextHeight = view.height / factor;
  const ratioX = (cx - view.x) / view.width;
  const ratioY = (cy - view.y) / view.height;
  worldMapState.viewBox = clampWorldMapViewBox({
    x: cx - nextWidth * ratioX,
    y: cy - nextHeight * ratioY,
    width: nextWidth,
    height: nextHeight,
  }, map);
  renderWorldMap(map);
}

// 适配整张地图。副作用是把临时 viewBox 重置为完整 canvas。
function worldMapFit(map = {}) {
  const canvas = worldMapCanvas(map);
  worldMapState.viewBox = { x: 0, y: 0, width: canvas.width, height: canvas.height };
  renderWorldMap(map);
}

// 回到默认视角。副作用是恢复 profile.rules.map.viewport 定义的默认 zoom/center。
function worldMapHome(map = {}) {
  worldMapState.viewBox = defaultWorldMapViewBox(map);
  renderWorldMap(map);
}

// 把浏览器点击坐标转换为地图坐标。输入是 SVG 与 pointer/click 事件；输出 canvas 坐标点。
function svgPointFromEvent(svg, event) {
  const rect = svg.getBoundingClientRect();
  const view = worldMapState.viewBox;
  return {
    x: view.x + ((event.clientX - rect.left) / rect.width) * view.width,
    y: view.y + ((event.clientY - rect.top) / rect.height) * view.height,
  };
}

// 查找点击点所在区域。输入是地图坐标；输出包含该点的 region，用于新标记自动归属。
function regionAtMapPoint(map = {}, point = {}) {
  return (map.regions || []).find(region => {
    const x = mapNum(region.x), y = mapNum(region.y);
    return point.x >= x && point.x <= x + mapNum(region.width) && point.y >= y && point.y <= y + mapNum(region.height);
  }) || null;
}

// 在地图坐标处创建待保存标记。副作用只写入 pendingMarker 并重绘，实际持久化由保存按钮触发。
async function createWorldMapMarkerAt(map = {}, point = {}) {
  const region = regionAtMapPoint(map, point);
  worldMapState.markMode = false;
  worldMapState.pendingMarker = {
    x: Number(point.x.toFixed(2)),
    y: Number(point.y.toFixed(2)),
    key: `map_marker_${Date.now()}`,
    name: "新地点",
    role: "place",
    terrain: region?.terrain || "custom",
    region_id: region?.id || null,
    region_name: region?.name || null,
  };
  worldMapState.selectedMarkerId = null;
  renderWorldMap(map);
}

// 保存待标记地点。输入来自内联表单和 pendingMarker；副作用是通过 worldAction 写入 world_places。
async function savePendingWorldMapMarker() {
  const pending = worldMapState.pendingMarker;
  if (!pending) return;
  const name = document.getElementById("world-map-mark-name")?.value.trim() || "";
  const key = document.getElementById("world-map-mark-key")?.value.trim() || "";
  const role = document.getElementById("world-map-mark-role")?.value || "place";
  const terrain = document.getElementById("world-map-mark-terrain")?.value.trim() || pending.terrain || "custom";
  if (!name || !key) {
    showToast("标记名称和 key 不能为空", "warn");
    return;
  }
  const important = role === "important_building";
  worldMapState.pendingMarker = null;
  await worldAction("place", {
    key,
    name,
    place_type: important ? "building" : "map_marker",
    region_id: pending.region_id || null,
    summary: "从世界地图标记创建。",
    coordinates: {
      x: pending.x,
      y: pending.y,
      terrain,
      marker_role: role || "place",
      importance: important ? 85 : 45,
      important,
    },
    traits: { terrain, important },
  });
}

// 保存地图设定。输入来自内联设定表单；副作用是更新当前世界档案 rules.map。
async function saveWorldMapSettings() {
  const profile = (snapshotData.world_model?.profiles || [])[0];
  if (!profile) {
    showToast("需要先创建世界档案", "warn");
    return;
  }
  const title = document.getElementById("world-map-setting-title")?.value.trim() || "世界地图";
  const baseHref = document.getElementById("world-map-setting-base")?.value.trim() || "";
  const width = Math.max(10, Math.min(10000, Number(document.getElementById("world-map-setting-width")?.value) || 100));
  const height = Math.max(10, Math.min(10000, Number(document.getElementById("world-map-setting-height")?.value) || 100));
  const unit = document.getElementById("world-map-setting-unit")?.value.trim() || "grid";
  const projection = document.getElementById("world-map-setting-projection")?.value.trim() || "local_grid";
  const maxZoom = Math.max(1, Math.min(50, Number(document.getElementById("world-map-setting-max-zoom")?.value) || 6));
  const gridSize = Math.max(0.1, Math.min(Math.max(width, height), Number(document.getElementById("world-map-setting-grid")?.value) || 10));
  const rules = profile.rules || {};
  const mapCfg = rules.map || {};
  const nextMap = {
    ...mapCfg,
    title,
    width,
    height,
    unit,
    projection,
    viewport: {
      ...(mapCfg.viewport || {}),
      max_zoom: maxZoom,
      default_zoom: Math.max(1, Math.min(maxZoom, Number(mapCfg.viewport?.default_zoom) || 1)),
    },
    grid: { ...(mapCfg.grid || {}), visible: true, size: gridSize },
  };
  if (baseHref) {
    nextMap.assets = upsertWorldMapRecord(mapCfg.assets || mapCfg.asset_refs, {
      id: "base_map",
      name: "地图底图",
      kind: "image",
      href: baseHref,
      path: baseHref,
    });
    nextMap.image_layers = upsertWorldMapRecord(mapCfg.image_layers || mapCfg.layers, {
      id: "base",
      name: "底图",
      asset_id: "base_map",
      href: baseHref,
      x: 0,
      y: 0,
      width,
      height,
      opacity: 1,
      order: -10,
    });
  }
  worldMapState.settingsOpen = false;
  await worldAction("profile", {
    key: profile.key || "default",
    title: profile.title || profile.key || "世界档案",
    summary: profile.summary || "",
    background_text: profile.background_text || "",
    rules: { ...rules, map: nextMap },
    evidence: profile.evidence || {},
  });
}

// 绑定地图控件事件。调用方是 renderWorldMap；所有监听只作用于刚渲染出的地图 DOM。
function bindWorldMapEvents(map = {}) {
  const svg = document.getElementById("world-map-svg");
  if (!svg) return;
  const coord = document.getElementById("world-map-coords");
  document.querySelectorAll("[data-map-action]").forEach(btn => {
    btn.onclick = () => {
      const action = btn.dataset.mapAction;
      if (action === "zoom-in") worldMapZoom(map, mapNum(map.viewport?.zoom_step, 1.25));
      if (action === "zoom-out") worldMapZoom(map, 1 / mapNum(map.viewport?.zoom_step, 1.25));
      if (action === "fit") worldMapFit(map);
      if (action === "home") worldMapHome(map);
      if (action === "settings") {
        worldMapState.settingsOpen = !worldMapState.settingsOpen;
        worldMapState.pendingMarker = null;
        renderWorldMap(map);
      }
      if (action === "mark") {
        if (map.viewport?.marking_enabled === false) return;
        worldMapState.markMode = !worldMapState.markMode;
        worldMapState.settingsOpen = false;
        renderWorldMap(map);
      }
    };
  });
  const saveSettingsBtn = document.querySelector("[data-map-save-settings]");
  if (saveSettingsBtn) saveSettingsBtn.onclick = () => saveWorldMapSettings();
  const cancelSettingsBtn = document.querySelector("[data-map-cancel-settings]");
  if (cancelSettingsBtn) {
    cancelSettingsBtn.onclick = () => {
      worldMapState.settingsOpen = false;
      renderWorldMap(map);
    };
  }
  const saveMarkerBtn = document.querySelector("[data-map-save-marker]");
  if (saveMarkerBtn) saveMarkerBtn.onclick = () => savePendingWorldMapMarker();
  const cancelMarkerBtn = document.querySelector("[data-map-cancel-marker]");
  if (cancelMarkerBtn) {
    cancelMarkerBtn.onclick = () => {
      worldMapState.pendingMarker = null;
      renderWorldMap(map);
    };
  }
  svg.querySelectorAll(".map-marker").forEach(marker => {
    marker.addEventListener("click", event => {
      event.stopPropagation();
      worldMapState.selectedMarkerId = marker.dataset.placeId || null;
      renderWorldMap(map);
    });
  });
  svg.addEventListener("wheel", event => {
    event.preventDefault();
    const factor = event.deltaY < 0 ? mapNum(map.viewport?.zoom_step, 1.25) : 1 / mapNum(map.viewport?.zoom_step, 1.25);
    worldMapZoom(map, factor, svgPointFromEvent(svg, event));
  }, { passive: false });
  svg.addEventListener("pointerdown", event => {
    if (worldMapState.markMode) return;
    if (map.viewport?.pan_enabled === false) return;
    svg.setPointerCapture?.(event.pointerId);
    worldMapState.dragging = true;
    worldMapState.dragStart = { clientX: event.clientX, clientY: event.clientY, view: { ...worldMapState.viewBox } };
  });
  svg.addEventListener("pointermove", event => {
    const point = svgPointFromEvent(svg, event);
    if (coord) coord.textContent = `${fmtMapNum(point.x)}, ${fmtMapNum(point.y)}`;
    if (!worldMapState.dragging || !worldMapState.dragStart) return;
    const rect = svg.getBoundingClientRect();
    const start = worldMapState.dragStart;
    const dx = ((event.clientX - start.clientX) / rect.width) * start.view.width;
    const dy = ((event.clientY - start.clientY) / rect.height) * start.view.height;
    worldMapState.viewBox = clampWorldMapViewBox({ ...start.view, x: start.view.x - dx, y: start.view.y - dy }, map);
    svg.setAttribute("viewBox", `${fmtMapNum(worldMapState.viewBox.x)} ${fmtMapNum(worldMapState.viewBox.y)} ${fmtMapNum(worldMapState.viewBox.width)} ${fmtMapNum(worldMapState.viewBox.height)}`);
  });
  const finishDrag = event => {
    if (worldMapState.dragging) svg.releasePointerCapture?.(event.pointerId);
    worldMapState.dragging = false;
    worldMapState.dragStart = null;
  };
  svg.addEventListener("pointerup", finishDrag);
  svg.addEventListener("pointercancel", finishDrag);
  const createMarkerFromMapClick = event => {
    if (!worldMapState.markMode || map.viewport?.marking_enabled === false) return;
    if (event.target?.closest?.(".map-marker")) return;
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation?.();
    createWorldMapMarkerAt(map, svgPointFromEvent(svg, event));
  };
  const canvasEl = document.querySelector(".world-map-canvas");
  canvasEl?.addEventListener("click", createMarkerFromMapClick, true);
  svg.addEventListener("click", createMarkerFromMapClick, true);
  svg.querySelectorAll(".map-canvas-bg,.map-image-layer,.map-terrain rect").forEach(target => {
    target.addEventListener("click", createMarkerFromMapClick);
  });
}

// 渲染结构化世界地图；输入来自 world_model.map，输出可缩放、可平移、可标记的 RPG 地图视口。
function renderWorldMap(map = {}) {
  const el = document.getElementById("world-map");
  if (!el) return;
  const terrain = map.terrain || [];
  const markers = map.markers || [];
  const imageLayers = map.image_layers || [];
  const routes = map.routes || [];
  if (!terrain.length && !markers.length && !imageLayers.length && !routes.length) {
    el.innerHTML = '<div class="empty-state">还没有地图坐标或底图资源</div>';
    return;
  }
  const canvas = worldMapCanvas(map);
  const view = ensureWorldMapView(map);
  const zoom = canvas.width / view.width;
  const markDisabled = map.viewport?.marking_enabled === false;
  const svg = [
    renderWorldMapImageLayers(map),
    renderWorldMapTerrain(map),
    renderWorldMapRoutes(map),
    renderWorldMapGrid(map),
    renderWorldMapMarkers(map),
    renderPendingWorldMapMarker(),
    renderWorldMapActor(map),
  ].join("");
  el.innerHTML = `<div class="world-map-shell ${worldMapState.markMode ? "marking" : ""}">
    <div class="world-map-toolbar">
      <div class="world-map-title"><span>${escapeHtml(canvas.title)}</span><small>${fmtMapNum(canvas.width)}×${fmtMapNum(canvas.height)} ${escapeHtml(canvas.unit)}</small></div>
      <div class="world-map-tools">
        <button class="world-mini-btn" data-map-action="zoom-in" title="放大">＋</button>
        <button class="world-mini-btn" data-map-action="zoom-out" title="缩小">－</button>
        <button class="world-mini-btn" data-map-action="home" title="回到默认视角">⌂</button>
        <button class="world-mini-btn" data-map-action="fit" title="适配整张地图">□</button>
        <button class="world-mini-btn ${worldMapState.settingsOpen ? "active" : ""}" data-map-action="settings" title="地图画布和底图资源">设定</button>
        <button class="world-mini-btn ${worldMapState.markMode ? "active" : ""}" data-map-action="mark" title="点击地图新增标记"${markDisabled ? " disabled" : ""}>标记</button>
      </div>
      <div class="world-map-readout"><span>${Math.round(zoom * 100)}%</span><span id="world-map-coords">—</span></div>
    </div>
    <div class="world-map-canvas" style="--map-bg:${escapeHtml(canvas.backgroundColor)}">
      <svg id="world-map-svg" viewBox="${fmtMapNum(view.x)} ${fmtMapNum(view.y)} ${fmtMapNum(view.width)} ${fmtMapNum(view.height)}"
        role="img" aria-label="${escapeHtml(canvas.title)}" preserveAspectRatio="xMidYMid meet">
        <rect class="map-canvas-bg" x="0" y="0" width="${fmtMapNum(canvas.width)}" height="${fmtMapNum(canvas.height)}"></rect>
        ${svg}
      </svg>
    </div>
    <div class="world-map-footer">
      ${renderWorldMapLegend(map)}
      ${renderWorldMapInspector(map)}
    </div>
  </div>`;
  bindWorldMapEvents(map);
}

function renderWorldModel() {
  const data = snapshotData.world_model || {};
  const counts = data.counts || {};
  const overviewEl = document.getElementById("world-overview");
  if (!overviewEl) return;
  const editorEl = document.getElementById("world-editor");
  if (editorEl) {
    editorEl.innerHTML = `<div class="world-editor-actions">
      <button class="world-mini-btn create" onclick="worldQuickCreate('profile')">新档案</button>
      <button class="world-mini-btn create" onclick="worldQuickCreate('region')">新区域</button>
      <button class="world-mini-btn create" onclick="worldQuickCreate('place')">新地点</button>
      <button class="world-mini-btn create" onclick="worldQuickCreate('lore')">新知识</button>
      <button class="world-mini-btn create" onclick="worldQuickCreate('faction_presence')">新势力影响</button>
      <button class="world-mini-btn create" onclick="worldQuickCreate('route')">新路线</button>
      <button class="world-mini-btn create" onclick="worldQuickCreate('condition')">新状态</button>
    </div>`;
  }
  const stat = (label, value) => `<div class="social-stat world-stat"><span class="social-stat-num">${formatNum(value || 0)}</span><span class="social-stat-label">${label}</span></div>`;
  overviewEl.innerHTML = [
    stat("档案", counts.profiles),
    stat("区域", counts.regions),
    stat("地点", counts.places),
    stat("知识", counts.lore),
    stat("势力", counts.faction_presence),
    stat("路线", counts.routes),
    stat("状态", counts.conditions),
  ].join("");
  renderWorldMap(data.map || {});

  const profileEl = document.getElementById("world-profiles");
  if (profileEl) {
    const profiles = data.profiles || [];
    profileEl.innerHTML = profiles.length ? profiles.slice(0, 4).map(p => {
      const rules = Object.entries(p.rules || {}).slice(0, 4)
        .map(([k, v]) => `<span class="social-chip mini">${escapeHtml(k)}:${escapeHtml(v)}</span>`).join("");
      return `<div class="social-card world-card profile-card">
        <div class="social-card-head"><span class="social-title">${escapeHtml(p.title || p.key || "世界档案")}</span><div class="world-card-actions"><span class="social-tag">${escapeHtml(p.key || "default")}</span>${worldEditButton("profile", p)}${worldArchiveButton("profile", p, p.title || p.key || "世界档案")}</div></div>
        ${p.summary ? `<div class="social-desc">${escapeHtml(p.summary)}</div>` : ""}
        ${p.background_text ? `<div class="world-long-text">${escapeHtml(p.background_text)}</div>` : ""}
        ${rules ? `<div class="social-chip-list tight">${rules}</div>` : ""}
      </div>`;
    }).join("") : '<div class="empty-state">还没有世界档案</div>';
  }

  const regionEl = document.getElementById("world-regions");
  if (regionEl) {
    const regions = data.regions || [];
    regionEl.innerHTML = regions.length ? regions.slice(0, 18).map(r => {
      const parent = r.parent_region_id ? `父级 ${r.parent_region_id}` : "根区域";
      return `<div class="social-row-card world-row">
        <div class="world-row-head"><div><span class="social-name">${escapeHtml(r.name || r.key)}</span><span class="social-tag">${escapeHtml(r.region_type || "region")}</span></div>${worldActionButtons("region", r, r.name || r.key, true)}</div>
        ${r.summary ? `<div class="social-desc">${escapeHtml(r.summary)}</div>` : ""}
        <div class="social-row-meta">${escapeHtml(r.key || "")} · id ${escapeHtml(r.id || "")} · ${escapeHtml(parent)}</div>
      </div>`;
    }).join("") : '<div class="empty-state">还没有区域/城池</div>';
  }

  const placeEl = document.getElementById("world-places");
  if (placeEl) {
    const places = data.places || [];
    placeEl.innerHTML = places.length ? places.slice(0, 18).map(p => {
      const scope = p.region_name || p.region_id || "未绑定区域";
      return `<div class="social-row-card world-row">
        <div class="world-row-head"><div><span class="social-name">${escapeHtml(p.name || p.key)}</span><span class="social-tag">${escapeHtml(p.place_type || "place")}</span></div>${worldActionButtons("place", p, p.name || p.key, true)}</div>
        ${p.summary ? `<div class="social-desc">${escapeHtml(p.summary)}</div>` : ""}
        <div class="social-row-meta">${escapeHtml(p.key || "")} · id ${escapeHtml(p.id || "")} · ${escapeHtml(scope)}</div>
      </div>`;
    }).join("") : '<div class="empty-state">还没有地点</div>';
  }

  const loreEl = document.getElementById("world-lore");
  if (loreEl) {
    const lore = data.lore || [];
    loreEl.innerHTML = lore.length ? lore.slice(0, 16).map(l => {
      const tags = (l.tags || []).slice(0, 4).map(t => `<span class="social-chip mini">${escapeHtml(t)}</span>`).join("");
      const scope = [l.scope_kind || "world", l.scope_name || l.scope_id].filter(Boolean).join(" · ");
      return `<div class="social-card world-card lore-card">
        <div class="social-card-head"><span class="social-title">${escapeHtml(l.title || l.key)}</span><div class="world-card-actions"><span class="social-tag">${escapeHtml(l.lore_type || "lore")}</span>${worldEditButton("lore", l)}${worldArchiveButton("lore", l, l.title || l.key)}</div></div>
        ${l.content ? `<div class="social-desc">${escapeHtml(l.content)}</div>` : ""}
        <div class="social-row-meta">${escapeHtml(scope)}</div>
        ${tags ? `<div class="social-chip-list tight">${tags}</div>` : ""}
      </div>`;
    }).join("") : '<div class="empty-state">还没有知识条目</div>';
  }

  const presenceEl = document.getElementById("world-faction-presence");
  if (presenceEl) {
    const presence = data.faction_presence || [];
    presenceEl.innerHTML = presence.length ? presence.slice(0, 16).map(p => {
      const influence = Number(p.influence) || 0;
      const scope = [p.scope_kind || "world", p.scope_name || p.scope_id].filter(Boolean).join(" · ");
      return `<div class="social-card world-card faction-presence-card">
        <div class="social-card-head"><span class="social-title">${escapeHtml(p.faction_name || p.faction_entity_id)}</span><div class="world-card-actions"><span class="social-tag">${escapeHtml(p.stance || "presence")}</span>${worldEditButton("faction_presence", p)}${worldArchiveButton("faction_presence", p, p.faction_name || p.faction_entity_id)}</div></div>
        <div class="social-axis-line"><span>${escapeHtml(scope)}</span><span class="${socialValueClass(influence)}">${formatSigned(influence)}</span></div>
        ${socialMeter(influence)}
        ${p.summary ? `<div class="social-desc">${escapeHtml(p.summary)}</div>` : ""}
      </div>`;
    }).join("") : '<div class="empty-state">还没有势力影响</div>';
  }

  const routesEl = document.getElementById("world-routes");
  if (routesEl) {
    const routes = data.routes || [];
    routesEl.innerHTML = routes.length ? routes.slice(0, 18).map(r => {
      const risk = Number(r.risk_level) || 0;
      const endpoints = [r.from_scope_name || r.from_scope_id, r.to_scope_name || r.to_scope_id].filter(Boolean).join(" → ");
      const detail = [r.travel_mode, r.duration_minutes != null ? `${formatNum(r.duration_minutes)}min` : "", endpoints].filter(Boolean).join(" · ");
      return `<div class="social-row-card world-row route-row">
        <div class="world-row-head"><div><span class="social-name">${escapeHtml(r.name || r.key)}</span><span class="social-tag">${escapeHtml(r.route_type || "route")}</span></div>${worldActionButtons("route", r, r.name || r.key)}</div>
        <div class="social-axis-line"><span>${escapeHtml(r.status || "active")}</span><span class="${socialValueClass(risk - 50)}">${formatNum(risk)}</span></div>
        ${detail ? `<div class="social-row-meta">${escapeHtml(detail)}</div>` : ""}
      </div>`;
    }).join("") : '<div class="empty-state">还没有路线</div>';
  }

  const conditionsEl = document.getElementById("world-conditions");
  if (conditionsEl) {
    const conditions = data.conditions || [];
    conditionsEl.innerHTML = conditions.length ? conditions.slice(0, 18).map(c => {
      const severity = Number(c.severity) || 0;
      const scope = [c.scope_kind || "world", c.scope_name || c.scope_id].filter(Boolean).join(" · ");
      const time = [c.starts_at, c.ends_at].filter(Boolean).join(" → ");
      return `<div class="social-card world-card condition-card">
        <div class="social-card-head"><span class="social-title">${escapeHtml(c.title || c.key)}</span><div class="world-card-actions"><span class="social-tag">${escapeHtml(c.condition_type || "state")}</span>${worldEditButton("condition", c)}${worldArchiveButton("condition", c, c.title || c.key)}</div></div>
        <div class="social-axis-line"><span>${escapeHtml(scope)}</span><span class="${socialValueClass(severity - 50)}">${formatNum(severity)}</span></div>
        ${c.summary ? `<div class="social-desc">${escapeHtml(c.summary)}</div>` : ""}
        ${time ? `<div class="social-row-meta">${escapeHtml(time)}</div>` : ""}
      </div>`;
    }).join("") : '<div class="empty-state">还没有动态状态</div>';
  }
}

const SOCIAL_SLOT_LABEL = {
  entity_kind: "实体",
  relationship_axis: "关系轴",
  reputation_axis: "声望轴",
  evaluation_axis: "评价轴",
  rumor_channel: "流言渠道",
  request_type: "请求类型",
};

function socialRequestAction(requestId, action) {
  if (!requestId) return;
  const reason = action === "reject" ? "webui_rejected" : `webui_${action}`;
  return socialAction("request_transition", { request_id: requestId, transition_action: action, reason });
}

// 渲染 reader 提供的社会世界快照；输入是只读 snapshotData.social_world，
// 输出是覆盖社会槽位、实体、声望、评价、请求和流言 DOM，不写数据库。
function renderSocialWorld() {
  const data = snapshotData.social_world || {};
  const overviewEl = document.getElementById("social-overview");
  if (!overviewEl) return;
  const counts = data.counts || {};
  const stat = (label, value) => `<div class="social-stat"><span class="social-stat-num">${formatNum(value || 0)}</span><span class="social-stat-label">${label}</span></div>`;
  overviewEl.innerHTML = [
    stat("槽位", counts.slots),
    stat("实体", counts.entities),
    stat("归属", counts.affiliations),
    stat("关系", counts.edges),
    stat("声望", counts.reputation),
    stat("评价", counts.evaluations),
    stat("流言", counts.rumors),
    stat("请求", counts.requests),
    stat("提示", counts.advisories),
  ].join("");

  const advisoryEl = document.getElementById("social-advisories");
  if (advisoryEl) {
    const advisories = data.advisories || [];
    advisoryEl.innerHTML = advisories.length ? advisories.slice(0, 10).map(a => {
      const sources = (a.sources || []).slice(0, 3).join(" · ");
      return `<div class="social-row-card advisory-row">
        <div><span class="social-name">${escapeHtml(SOCIAL_SLOT_LABEL[a.slot_type] || a.slot_type || "槽位")}</span><span class="social-tag">${escapeHtml(a.key || "")}</span></div>
        <div class="social-row-meta">${escapeHtml(a.message || "未定义槽位")} · ${formatNum(a.usage_count || 0)} 次${sources ? ` · ${escapeHtml(sources)}` : ""}</div>
      </div>`;
    }).join("") : '<div class="empty-state">无槽位提示</div>';
  }

  const slots = data.slots || [];
  const slotsEl = document.getElementById("social-slots");
  if (slotsEl) {
    slotsEl.innerHTML = slots.length ? slots.slice(0, 60).map(s => {
      const title = [s.description, s.origin || s.source].filter(Boolean).join(" · ");
      return `<span class="social-chip" title="${escapeHtml(title)}"><b>${escapeHtml(SOCIAL_SLOT_LABEL[s.slot_type] || s.slot_type || "槽位")}</b>${escapeHtml(s.label || s.key || "—")}</span>`;
    }).join("") : '<div class="empty-state">无社会槽位</div>';
  }

  const entitiesEl = document.getElementById("social-entities");
  if (entitiesEl) {
    const entities = data.entities || [];
    entitiesEl.innerHTML = entities.length ? entities.slice(0, 18).map(e => {
      const traits = Object.entries(e.traits || {}).slice(0, 3).map(([k, v]) => `<span class="social-chip mini">${escapeHtml(k)}:${escapeHtml(v)}</span>`).join("");
      return `<div class="social-card entity-card">
        <div class="social-card-head"><span class="social-title">${escapeHtml(e.display_name || e.id)}</span><span class="social-tag">${escapeHtml(e.entity_kind || "")}</span></div>
        ${e.summary ? `<div class="social-desc">${escapeHtml(e.summary)}</div>` : ""}
        ${traits ? `<div class="social-chip-list tight">${traits}</div>` : ""}
      </div>`;
    }).join("") : '<div class="empty-state">还没有社会实体</div>';
  }

  const affEl = document.getElementById("social-affiliations");
  if (affEl) {
    const affiliations = data.affiliations || [];
    affEl.innerHTML = affiliations.length ? affiliations.slice(0, 14).map(a => {
      const pct = Math.max(0, Math.min(100, (Number(a.strength) || 0) * 100));
      return `<div class="social-row-card">
        <div><span class="social-name">${escapeHtml(a.subject_name || a.subject_entity_id)}</span><span class="social-arrow">→</span><span class="social-name">${escapeHtml(a.faction_name || a.faction_entity_id)}</span></div>
        <div class="social-row-meta">${escapeHtml(a.role || "member")} · ${formatNum(pct)}%</div>
      </div>`;
    }).join("") : '<div class="empty-state">无归属</div>';
  }

  const edgesEl = document.getElementById("social-edges");
  if (edgesEl) {
    const edges = data.edges || [];
    edgesEl.innerHTML = edges.length ? edges.slice(0, 14).map(e => {
      const value = Number(e.value) || 0;
      return `<div class="social-row-card">
        <div><span class="social-name">${escapeHtml(e.source_name || e.source_entity_id)}</span><span class="social-arrow">→</span><span class="social-name">${escapeHtml(e.target_name || e.target_entity_id)}</span></div>
        <div class="social-axis-line"><span>${escapeHtml(e.axis || "关系")}</span><span class="${socialValueClass(value)}">${formatSigned(value)}</span></div>
        ${socialMeter(value)}
      </div>`;
    }).join("") : '<div class="empty-state">无关系边</div>';
  }

  const repEl = document.getElementById("social-reputation");
  if (repEl) {
    const reps = data.reputation || [];
    repEl.innerHTML = reps.length ? reps.slice(0, 16).map(r => {
      const value = Number(r.value) || 0;
      return `<div class="social-card">
        <div class="social-card-head"><span class="social-title">${escapeHtml(r.subject_name || r.subject_entity_id)}</span><span class="social-tag">${escapeHtml(r.audience_name || r.audience_entity_id)}</span></div>
        <div class="social-axis-line"><span>${escapeHtml(r.axis || "声望")}</span><span class="${socialValueClass(value)}">${formatSigned(value)}</span></div>
        ${socialMeter(value)}
        <div class="social-row-meta">confidence ${formatNum((Number(r.confidence) || 0) * 100)}%</div>
      </div>`;
    }).join("") : '<div class="empty-state">无声望记录</div>';
  }

  const evalEl = document.getElementById("social-evaluations");
  if (evalEl) {
    const evaluations = data.evaluations || [];
    evalEl.innerHTML = evaluations.length ? evaluations.slice(0, 14).map(e => {
      const score = Number(e.score) || 0;
      return `<div class="social-card">
        <div class="social-card-head"><span class="social-title">${escapeHtml(e.subject_name || e.subject_entity_id)}</span><span class="social-tag">${escapeHtml(e.evaluator_name || e.evaluator_entity_id)}</span></div>
        <div class="social-axis-line"><span>${escapeHtml(e.axis || "评价")}</span><span class="${socialValueClass(score)}">${formatSigned(score)}</span></div>
        ${e.reason ? `<div class="social-desc">${escapeHtml(e.reason)}</div>` : ""}
        <div class="social-row-meta">${escapeHtml(e.truth_layer || "social_perception")} · ${escapeHtml(e.visibility || "known")}</div>
      </div>`;
    }).join("") : '<div class="empty-state">无社会评价</div>';
  }

  const reqEl = document.getElementById("social-requests");
  if (reqEl) {
    const requests = data.requests || [];
    reqEl.innerHTML = requests.length ? requests.slice(0, 14).map(r => {
      const actors = [r.requester_name || r.requester_entity_id, r.target_name || r.target_entity_id].filter(Boolean).join(" → ");
      const links = [
        r.linked_event_id ? `event ${r.linked_event_id}` : "",
        r.linked_occurrence_id ? `occ ${r.linked_occurrence_id}` : "",
      ].filter(Boolean).join(" · ");
      return `<div class="social-card request-card">
        <div class="social-card-head"><span class="social-title">${escapeHtml(r.topic || r.summary || "请求")}</span><span class="social-tag">${escapeHtml(r.request_type || "request")}</span></div>
        ${actors ? `<div class="social-row-meta">${escapeHtml(actors)}</div>` : ""}
        ${r.summary ? `<div class="social-desc">${escapeHtml(r.summary)}</div>` : ""}
        <div class="request-metrics">
          <span>${escapeHtml(r.status || "open")}</span><span>${escapeHtml(r.privacy_level || "local")}</span>${links ? `<span>${escapeHtml(links)}</span>` : ""}
        </div>
        ${["open","accepted","in_progress"].includes(r.status || "open") ? `<div class="request-actions">
          ${(r.status || "open") === "open" ? `<button class="world-mini-btn create" onclick="socialRequestAction(${escapeJsArg(r.id)}, ${escapeJsArg("accept")})">接受</button><button class="world-mini-btn danger" onclick="socialRequestAction(${escapeJsArg(r.id)}, ${escapeJsArg("reject")})">拒绝</button>` : ""}
          ${["accepted","in_progress","open"].includes(r.status || "open") ? `<button class="world-mini-btn" onclick="socialRequestAction(${escapeJsArg(r.id)}, ${escapeJsArg("complete")})">完成</button><button class="world-mini-btn" onclick="socialRequestAction(${escapeJsArg(r.id)}, ${escapeJsArg("expire")})">过期</button>` : ""}
        </div>` : ""}
      </div>`;
    }).join("") : '<div class="empty-state">无请求/愿望</div>';
  }

  const rumorEl = document.getElementById("social-rumors");
  if (rumorEl) {
    const rumors = data.rumors || [];
    rumorEl.innerHTML = rumors.length ? rumors.slice(0, 18).map(r => {
      const heat = Math.max(0, Math.min(100, (Number(r.heat) || 0) * 100));
      const cred = Math.max(0, Math.min(100, (Number(r.credibility) || 0) * 100));
      return `<div class="social-card rumor-card">
        <div class="social-card-head"><span class="social-title">${escapeHtml(r.subject_name || r.channel || "流言")}</span><span class="social-tag">${escapeHtml(r.channel || "")}</span></div>
        <div class="social-desc">${escapeHtml(r.content || "")}</div>
        <div class="rumor-metrics">
          <span>heat ${formatNum(heat)}%</span><span>cred ${formatNum(cred)}%</span><span>${escapeHtml(r.truth_layer || "rumor_unverified")}</span><span>${formatNum(r.exposure_count || 0)} heard</span>
        </div>
      </div>`;
    }).join("") : '<div class="empty-state">无流言</div>';
  }
}

function socialMeter(value) {
  const pct = Math.max(0, Math.min(100, (Number(value) + 100) / 2));
  return `<div class="social-meter"><div class="social-meter-center"></div><div class="social-meter-fill ${socialValueClass(value)}" style="width:${pct}%"></div></div>`;
}

function socialValueClass(value) {
  const v = Number(value) || 0;
  if (v >= 20) return "positive";
  if (v <= -20) return "negative";
  return "neutral";
}

function formatSigned(value) {
  const v = Number(value) || 0;
  return `${v > 0 ? "+" : ""}${formatNum(v)}`;
}

// ── 心相 / 内境面板 ────────────────────────────
function renderInnerLife() {
  const inner = snapshotData.inner_life || {};
  const narEl = document.getElementById("self-narrative");
  if (narEl) {
    const nar = inner.self_narrative;
    narEl.innerHTML = (nar && nar.content)
      ? `<blockquote class="narrative-quote">“${escapeHtml(nar.content)}”<cite>${formatTime(nar.at)}</cite></blockquote>`
      : '<div class="empty-state">还没有形成自我叙事</div>';
  }
  const opEl = document.getElementById("opinions-list");
  if (opEl) {
    const ops = inner.opinions || [];
    const typeLabel = { like: "喜欢", dislike: "不喜欢", concern: "在意", value: "看重", discovery: "发现" };
    opEl.innerHTML = ops.length ? ops.map(o => {
      const v = Number(o.strength) || 0;
      const pct = Math.max(0, Math.min(100, (v + 1) / 2 * 100));
      const cls = Math.abs(v) < 0.2 ? "" : (v > 0 ? "high" : "low");
      return `<div class="opinion-row">
        <div class="opinion-head"><span class="opinion-target">${escapeHtml(o.target)}</span><span class="opinion-type">${typeLabel[o.opinion_type] || escapeHtml(o.opinion_type || "")}</span></div>
        <div class="persona-trait-track"><div class="persona-trait-center"></div><div class="persona-trait-fill ${cls}" style="width:${pct}%"></div></div>
        ${o.reason ? `<div class="opinion-reason">${escapeHtml(o.reason)}</div>` : ""}
      </div>`;
    }).join("") : '<div class="empty-state">还没有形成什么看法</div>';
  }
  const relEl = document.getElementById("relationship-list");
  if (relEl) {
    const notes = snapshotData.relationship || [];
    const now = Date.now() / 1000;
    relEl.innerHTML = notes.length ? notes.map(n => {
      const due = n.follow_up_due_ts && !n.followed_up_at && Number(n.follow_up_due_ts) <= now;
      return `<div class="relationship-card ${n.sentiment === "concern" ? "concern" : ""}">
        ${n.topic ? `<span class="rel-topic">${escapeHtml(n.topic)}</span>` : ""}
        <div class="rel-content">${escapeHtml(n.content)}</div>
        ${due ? '<span class="rel-due">想问问你</span>' : ""}
      </div>`;
    }).join("") : '<div class="empty-state">还没记下关于你的事</div>';
  }
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
    else if (action === "world") showToast("世界本体已更新", "ok");
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
