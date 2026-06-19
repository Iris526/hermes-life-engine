# LifeEngine v0.18.0 — 活人感：生成式内心生活（LifeAuthor）+ 资料片 + 关系记忆 + 反思循环

## 缘起

审计（2026-06-19）的结论：引擎是**两层**——

- **确定性底座**（资源账本 `apply_delta`、日程块、睡眠债、心跳 tick、persona 漂移、recurring/venture 结算）：扎实，保留。
- **生活内容层**（她做什么 / 梦什么 / 闲下来说什么）：**100% 写死的 Python f-string 模板**。引擎从不自调模型，人格只在聊天回合复述模板行 → **机械感的结构来源**。

五条病灶都指向同一根因：
1. **日程**：`autonomy.plan_autonomy`（[autonomy.py:306](../autonomy.py)）每 tick 挑一个 goal 塞一句 `f"推进目标：{title}"`，无时间/细节/理由，无长弧。
2. **主动**：`proactive.py` 仅 5 个事件驱动触发，默认 `pending_only` / `max_per_day=1` / 冷却 180min（[proactive.py:97](../proactive.py)）；**无 idle/陪伴路径**，**无"你的生活"模型**。
3. **做梦**：`dream._compose_dream_text`（[dream.py:242](../dream.py)）字面写着"像 LifeEngine 自检一样的梦"，还把夜间审计（没结算的资源）织进梦里——**梦的是工程**。
4. **RPG**：无 campaign/arc 引擎。`life_arcs` 是惰性容器，recurring/venture 是平的循环，**没有资料片/大事件**。
5. **成长**：persona 只变 `tone_hint`、memory 只写不读、`life_reflections.proposed_ops` 从不应用、无持久 opinions、世界观=静态 Canon → **经历不反哺行为**。

**北极星**：各有各的生活，互相讲述、互相陪伴；她**自洽自发**，不需用户干预。

**本轮做法**：在确定性底座之上引入一个**生成式内心生活层（LifeAuthor）**，引擎在心跳里**可自调 LLM**（用户拍板：她在后台真的在长），并补上 campaign 长弧、owner-life 关系记忆、反思→观点循环。四阶段实现，全部由心跳保证，不靠提示词/记忆。

版本：0.17.0 → **0.18.0**；schema 56 → **60**（P1=57, P2=58, P3=59, P4=60）。
铁律：所有资源变动仍走 `apply_delta()`，doctor 对账不变式不破；**角色无关**（内容只从 Canon 生成，新机制都是可注册数据）；**无 debug/快进**；**无网络也能跑**（无 key 自动降级回模板）。

---

## 架构决定：引擎可自调 LLM（LifeAuthor 边界 + 降级契约）

引擎此前从不调模型（只有 `pre_llm_call`/`transform_llm_output` 两个 host 钩子，[hooks.py:13](../hooks.py)）。本轮新增**唯一**的模型出口 `life_author.py`，把"生成生活内容"这件事收敛成一个**有契约、可降级、可预算、可测**的薄层。

**契约（不可破）：**
1. **LifeAuthor 只产出"内容/措辞/结构"，绝不直接改资源。** 资源效果仍走 `create_event` → `event_costs` → 完成时 `apply_delta`，与今天一致。作者产出的是"事件草案 / 梦境文本 / 一句主动话 / campaign 蓝图 / 反思要点"，再经现有的 op 路径落地。
2. **真值分层照旧。** 梦=`dream_symbolic`；agent 生活走 `agent_life_policy`（可叙事虚构）；**user_life 仍 `requiresOwnerConfirmation`、不能虚构**（[constants.py:139](../constants.py)）。作者的 system 提示里写入 Canon 的真值规则，越界内容在落地层被拒。
3. **网络在事务之外。** 心跳已是原子 LifeOps（commit `4ef5ee1`）。作者调用（网络 I/O）发生在**不持 DB 写锁**时：`author() → 拿到结构化内容 → 在 tick 事务内用现有 op 落地`。
4. **降级即默认安全。** 宿主模型不可用（无 `host_model` 入口 / 脱离 turn 且 Hermes 不支持无 agent 调用）/超时/失败/超预算/门控关 → `author()` 返回 `None`，调用方**回退到（清理过的）模板**。因此：开发机（无宿主）/测试环境行为≈今天，doctor 全绿，离线测试可跑。
5. **预算与限速。** Canon `life_author.daily_token_budget`（默认例如 200k tok/天）；超了当天只降级。`client.with_options(timeout=8.0, max_retries=1)`。高频调用对 Canon/persona 这段**稳定前缀做 prompt caching**（`cache_control:{type:"ephemeral"}`）压成本。

**模型来源（用 Hermes 的 `PluginLlm` 门面，不自建客户端）**：宿主提供官方插件 LLM 门面 `agent.plugin_llm.PluginLlm`（挂在 `ctx.llm` 上，模块文档明示其用途即"带外/无 agent 的插件模型调用——例如把昨天的活动打分成状态板一行的 scheduled job"）。`life_author` 的 `host_model` 适配器（仿 [paths.py](../paths.py) 导 `hermes_constants` 的 `try/except`）：

```python
def _host_llm():
    try:
        from agent.plugin_llm import PluginLlm     # 宿主包；开发/CI 无宿主时 ImportError
        return PluginLlm(plugin_id="lifeengine")    # 等价 ctx.llm（hermes_cli/plugins.py:314）；无 ctx 的 cron 进程可直接构造
    except Exception:
        return None                                 # → None → 回退干净模板
```

**不 new `anthropic.Anthropic()`、不带 key**；provider 路由 / 鉴权 / 超时 / fallback / 成本估算全由宿主持有，插件永不见原始 token。

✅ **时序已定（B：后台自主）**：Hermes 暴露了"无 agent 也可调"的注册模型客户端（用户确认 2026-06-19）。心跳 cron 虽是 `--no-agent`（[heartbeat.py:49](../heartbeat.py)），`life_author` 仍可在 tick 里**同步**调 `PluginLlm(plugin_id="lifeengine").complete_structured(...)` 生成 → **你不在她也在长**（门面就是为这种 scheduled/无 agent 场景设计的）；无 ctx 的 cron 进程直接构造即可。宿主不在的开发/CI 环境照常降级回模板。turn 内路径（经 `pre_llm_call`）保留作投递/兜底，不作主生成路径。

---

## LifeAuthor 数据模型 & 接口

```python
# life_author.py
def author(conn, owner_kind, owner_id, *, kind, context, schema,
           model=None, effort="low", trace_id=None) -> dict | None:
    """生成一段结构化生活内容；失败/降级返回 None（调用方回退模板）。"""
```

- `kind` ∈ `{daily_plan, dream, idle_share, ask_about_user, campaign_seed, campaign_beat, reflection}`。
- **Prompt 组装（全部来自 Canon，角色无关）**：
  - *稳定 system（带 cache_control）*：Canon `identity`+`worldview`+`behavior_rules`、persona 6 维当前值（[persona.py](../persona.py)）、真值规则、"你在写自己的生活，不是写系统日志"的写作纪律。
  - *易变 user*：本次调用的具体生活状态（心情/精力/钱/季节/时段、近期**生活域**记忆与事件、owner-life 关系记忆、当前 campaign 阶段…按 `kind` 取）。
- **结构化输出（门面自带）**：`llm.complete_structured(instructions=<kind 指令>, input=[{"type":"text","text":<易变状态 JSON>}], system_prompt=<Canon/persona 稳定前缀>, json_schema=SCHEMA, model=<档位>, max_tokens=, timeout=, purpose=f"life_author:{kind}")`，返回 `PluginLlmStructuredResult`，直接取 `.parsed`（已按 `json_schema` 校验的 dict）；`.usage`（tokens + `cost_usd`）落 `life_author_runs` 做预算/审计。同步 API 正合心跳（tick 同步）。
- **可观测**：每次调用写 `life_author_runs`（kind、model、input/output tok、cost、`request_id`、status、trace_id）+ trace；当天 tok 累计用于预算闸。
- **测试缝**：`life_author` 模块门控（`DEFAULT_MODULE_GATES`，默认 `auto`，无宿主 `PluginLlm` 时运行期解析为 `off`）；测试注入确定性桩、不联网——`PluginLlm(plugin_id="lifeengine", sync_caller=fake)` 或 `agent.plugin_llm.make_plugin_llm_for_test`。

**Canon 新增策略块** `life_author`（[constants.py](../constants.py) `DEFAULT_CANON_TEMPLATE`）：
```jsonc
"life_author": {
  "enabled": true,
  "daily_token_budget": 200000,
  "timeout_seconds": 8,
  "models": {            // 你来定档位；默认给安全值，建议按成本降配
    "default": "claude-opus-4-8",
    "idle_share": "claude-haiku-4-5",
    "daily_plan": "claude-haiku-4-5",
    "dream": "claude-sonnet-4-6",
    "reflection": "claude-sonnet-4-6",
    "campaign_seed": "claude-opus-4-8"
  }
}
```

## 模型与成本

| 用途 | 频率 | 建议档位 | 价格（输入/输出 /MTok） |
|---|---|---|---|
| idle 闲聊 / 日程润色 | 高 | `claude-haiku-4-5` | $1 / $5 |
| 做梦 / 周期反思 | 中（每晚/每周） | `claude-sonnet-4-6` | $3 / $15 |
| campaign 起弧（蓝图） | 低（数周一次） | `claude-opus-4-8` | $5 / $25 |

**默认用用户当前活跃模型。** `complete_structured(model=...)` 的覆盖是 **fail-closed**：要走上表分档，须在**宿主** `config.yaml` 开 `plugins.entries.lifeengine.llm.allow_model_override: true` + `allowed_models: [...]`（[plugin_llm.py 模块文档](agent/plugin_llm.py)）；不开则忽略 `model=`、统一用用户活跃模型，`life_author.models` 失效。无论哪种，**成本与鉴权走宿主账**，引擎不持 key；高频项是否降到 Haiku/Sonnet 是你的成本决定（在宿主侧配）。

---

## P1 — 做梦改造 + LifeAuthor 地基（schema 57）

**最小改动、最高象征收益**：先把"梦到工程"治掉，并把 LifeAuthor 地基立起来。

- 新增 `life_author.py` + `life_author_runs` 表 + Canon `life_author` 块 + 门控 `life_author`。
- **重写做梦**（[dream.py:242](../dream.py) `_compose_dream_text` → `_author_dream`）：
  - **素材只取生活域**：记忆按 `memory_type IN (episodic, reflection, dream)` 且过滤系统/审计类；事件过滤 `event_category` 的系统类；**叠加 owner-life 关系记忆**（P2 落地后接入，P1 先留空位）。
  - **作者生成**：`kind="dream"`，schema=`{content, share_text, symbols[], mood_delta, residue_opinion?}`，让人格写一段**超现实、有情绪、象征性的、关于她的亲历与你俩共处**的梦。
  - **删掉所有引擎自指**："LifeEngine 自检""账页""温和的自检"全部移除。
  - **夜间审计拆出去**：审计（`DreamAudit`）保留，但只进内部 trace / `dream_runs`，**永不作为梦内容**。
  - **降级模板**：作者不可用时，回退到一段**清理过的、生活化的**模板（无任何引擎措辞）。
- **梦留残留**：`mood_delta` 经 `MOOD_REACTION` 落地；`residue_opinion` 写入 P4 的 opinions（P1 先存草稿）；分享 intent 复用现有 `self_reflection_share`。
- 真值层仍 `dream_symbolic`、`privacy=safe_to_share`。

**验证**：`test_lifeengine_v018_dream_authoring`——离线（fake author）下梦文本不含"LifeEngine/自检/账页"、含生活意象；审计发现不入梦内容；无 key 时回退模板且 doctor 绿。

---

## P2 — 主动陪伴（idle/companion outreach）+ owner-life 关系记忆（schema 58）

让她"没事也会找你"，并记住、回访**你的**生活。

- **idle 触发**（在现有 `_run_proactive_for_tick`，[runtime.py:2012](../runtime.py)）：当"距上次主动/对话已久"且（心情好 / 刚有有意思的瞬间 / 到了有意义的时段）且预算允许 → `author(kind="idle_share" | "ask_about_user")` 生成**自由的一句分享**或**对你生活的追问**。新增 intent 类型 `idle_share`、`companion_checkin`、`ask_about_user`。
- **companion 模式**（扩展 `_gate_policy`，[proactive.py:94](../proactive.py)）：idle 配额与事件配额**分离**，给 idle 单独的日上限与更短冷却，且**真的推送**（不再一律 pending_only）。默认仍温和（可在 Canon 调）。
- **owner-life 关系记忆**：激活休眠的 `user_life` scope（[owner_scope.py:60](../owner_scope.py)，默认 `off` → `auto`）+ 新表 `relationship_notes`（你告诉过她的关于你生活的事：人、事、在意的点、未了的悬念）。作者据此追问（"你上次说的那个面试…"）。**写 user_life 仍需确认、不可虚构**（沿用 `user_life_policy`）。
- 做梦（P1）此时接入 owner-life 素材 → 她也会梦到你讲过的生活。

**验证**：`test_lifeengine_v018_companion`——闲置+好心情下产出 idle intent；产出引用 relationship_notes 的追问；companion 模式配额/推送正确；无 key 时不产 idle（纯降级，不报错）。

---

## P3 — Campaign / 资料片引擎（schema 59）

RPG 感最大的杠杆：跨周长弧、阶段升级、自动铺事件。

- 新表 `campaigns`：
  ```jsonc
  { id, owner_kind, owner_id, title, theme_json, status,        // planned/active/escalating/climax/resolved
    phases_json,        // [{phase, title, kind:预兆|升温|高潮|收尾, duration_days, daily_spawns, spawn_template, one_time_events}]
    current_phase, progress, goal_id, arc_id,
    start_date, expected_end_date, created_at, updated_at }
  ```
  幂等围栏 `campaign_phase_occurrences UNIQUE(campaign_id, phase, date_key)`（仿 `recurring_activity_occurrences`）。
- **tick 物化** `_materialize_campaigns_for_tick`（与 [_materialize_recurring_for_tick:1542](../runtime.py) 并列，置于其后）：按当前阶段用**正常 `create_event` + `apply_delta`** 铺主题事件；按时间/目标完成度**自动推进阶段**；逐阶段**加密/加注**（density/importance 递增）；末段**收尾**并 `resolved`。**不分身**：复用 `impromptu._next_free_slot` 冲突裁决。
- **起弧来源**：
  - 作者自发：`author(kind="campaign_seed")`——给定她的状态/persona/季节，她自己决定"最近想张罗一件大事"，产出 `{title, theme, phases[...], linked_goal}` 蓝图。
  - 生活事件：你讲了一件事 → 她围绕它起一个**个人项目** campaign。
  - 手动：工具 `life_campaign`（register/list/get/advance/cancel）。
- **autonomy 倾斜**：`plan_autonomy` 在有 active campaign 时，优先为当前阶段铺事件，并用 `author(kind="daily_plan")` 给事件**质地与时段**（替代 `f"推进目标：X"`；作者不可用则回退原模板）。
- 角色无关：campaign 是注册数据，主题/措辞全部作者从 Canon 生成；引擎不硬编码任何具体剧情。

**验证**：`test_lifeengine_v018_campaign`——阶段按天推进、事件密度递增、收尾 resolved；不双占（与 recurring/venture 同槽冲突时顺延）；goal/arc 进度联动；无 key 时引擎仍能跑预置阶段模板。

---

## P4 — 反思→观点循环 + 世界观成长（schema 60）

让经历真正改变她，并被看见。

- 新表 `agent_opinions`：`{ id, owner_kind, owner_id, target, opinion_type, // like/dislike/concern/value/discovery
  strength[-1,1], confidence[0,1], formed_from_event_id, reflection_id, last_reinforced_at }`。
- **反思执行器**：`life_reflections.proposed_ops`（[goals.py:343](../goals.py)，今天从不应用）真正落地；insights → opinions/preferences 更新 + persona baseline **提案**（仍**不**强写 Canon，沿用 `propose_consolidation` 的人审，[persona.py:277](../persona.py)）。
- **周期反思** `_run_reflection_for_tick`（置于 persona_drift 附近，低频：每周/重大事件/persona 漂移≥阈值时触发）：`author(kind="reflection")` 把近期经历**综合成**改变了的观点 + 一句**自我叙事**（"这季度我好像变得更爱往外跑了"）。
- **观点复现**：autonomy 规划、proactive、做梦都读 `agent_opinions` → 经历改变行为**可见**。她能给你惊喜："我最近想明白一件事…"。

**验证**：`test_lifeengine_v018_growth`——完成某类事件后形成对应 opinion；opinion 影响后续 autonomy 选择与主动话题；反思产出自我叙事并存档；persona 提案走人审不自动改 Canon。

---

## 工具 & 接线

- **心跳子流程顺序**（[runtime.py:1285](../runtime.py) `tick()`）插入：
  `… recurring → **campaigns** → supply_chain → opportunity → …`；`persona_drift` 附近加 `**reflection**`（低频内部判频）；`proactive` 内部加 **idle/companion** 分支；做梦在 wake 时走作者。
- **新工具**：`life_campaign`（P3，register/list/get/advance/cancel）。其余复用现有工具与 op 路径。
- **门控**（[constants.py:37](../constants.py) `DEFAULT_MODULE_GATES`）新增：`life_author`(auto)、`campaigns`(auto)、`companion`(默认随 `proactive`)、`agent_opinions`(auto)；`user_life` 由 `off`→`auto`（写仍需确认）；`relationship_memory` 已存在（auto）。

## 降级、真值与安全

- **无网络/无 key**：`life_author` 解析为 `off`，所有 `author()` 返回 `None`，全链路回退模板；doctor 绿；CI 离线跑。
- **真值**：梦 `dream_symbolic`；agent 生活 `agent_life_policy`（可虚构）；**user_life 不可虚构、需确认**。作者越界内容在落地层被拒。
- **隐私/最终面**：`behavior_mapping`/FinalGate 在聊天面照旧（[constants.py:138](../constants.py)）；不暴露内部诊断、私有真值源、审计发现。
- **预算**：超 `daily_token_budget` 当天降级；`life_author_runs` 可审计成本。
- **原子**：作者调用在 tick 写事务之外；内容用现有 op 在事务内落地，`apply_delta` 不变式不破。

## 验证

- 新增 `test_lifeengine_v018_dream_authoring / _companion / _campaign / _growth`（均含**离线 fake author** 路径与**无 key 降级**路径）。
- 版本号在 ~21 个 `PLUGIN_VERSION == "0.18.0"` 测试 + `test_lifeengine_v01210` schema=60 同步上调。
- 全量 fast 套件通过（`--ignore` v092/v094/v095 large_smoke）；doctor 资源对账保持绿。

## 铁律 & 角色无关（复述）

- 资源变动一律 `apply_delta()`；LifeAuthor 不碰资源。
- 引擎**不依赖明灯**：内容只从 Canon 生成；campaign/opinion/relationship 都是可注册数据。
- **无 debug/快进**：campaign 按真实心跳节奏推进。
- **无网络可运行**：降级回模板是默认安全态。

## 待办 / 后续

- P1 之后即可见到"做梦不再梦工程"——建议先落 P1（最快见效），再 P2/P3/P4。
- campaign 自然随机性（per-phase 波动）可在确定性掷点上叠加（仿 venture P3 的 hash 掷点，无 RNG）。
- relationship_notes 可接入向量检索（复用现有 FTS+vec）做更准的追问召回。
- 模型档位与 `daily_token_budget` 上线后按真实成本回调。

## 实现状态（2026-06-19，已落地并推到 main）

全部离线验证（fake author + 降级路径），全量 fast 套件 288 passed；**真·调宿主模型那条路只能在 iris 上实跑确认**（开发机无 Hermes）。

| 提交 | 内容 | schema |
|---|---|---|
| `7de662e` | **P1** LifeAuthor 地基（`life_author.py`，经 `PluginLlm` 调宿主模型、无宿主降级）+ 做梦改写（梦生活、不梦工程） | 57 |
| `f204934` | **P2** 主动陪伴（`companion.py`，idle/好心情/到期回访）+ owner-life 关系记忆（`relationship.py` + `life_relationship`） | 58 |
| `dd66c24` | **P3** campaign/资料片 引擎（`campaigns.py` + `_run_campaigns_for_tick` + `life_campaign`，含 seed 自发起弧） | 59 |
| `01b432b` | **P4** 反思→观点循环 + 自我叙事（`opinions.py` + `life_opinion`） | 60 |
| `57f8846` | autonomy 每日 goal-step 生成式化（`_author_goal_step`，接 campaign/opinion）+ 观点喂做梦/规划 | — |
| `ecb926f` | 内心生活注入聊天上下文（`_inner_life_capsule` → `inner_life`：自我叙事/观点/在忙的弧/该问的事） | — |

新工具：`life_relationship`💞 `life_campaign`📜 `life_opinion`🌱（SKILL.md 已加 v0.18 使用指南）。新门控：`life_author` / `companion` / `campaigns` / `reflection`（默认 auto，无宿主全部降级）。

**上线 iris 步骤**：① 部署本插件到 iris 的 `~/.hermes/plugins/lifeengine`；② 可选在宿主 `config.yaml` 设 `plugins.entries.lifeengine.llm.allow_model_override: true` + `allowed_models` 启用 Haiku/Sonnet/Opus 分档（不设则用明灯当前活跃模型）；③ 触发一次做梦/tick/`life_opinion reflect`，确认 `life_author_runs` 出现 `status='ok'` 行即生成式链路打通。

**仍遗留**：应用既有 `life_reflections.proposed_ops`；真·推送（你不在时也发，属宿主投递侧）。
