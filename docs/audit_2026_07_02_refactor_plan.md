# LifeEngine 全系统审计与迭代方案

日期：2026-07-02 ｜ 基线：main `b784762`（schema v66，PLUGIN_VERSION 0.18.0）

**审计方法**：10 个领域并行深读（runtime / 生成层 / 世界社会 / 数据层 / WebUI 前后端 / 命令面 / 人审机器 / 文档概念 / 测试），共产出 127 个问题；其中 critical/high 共 56 个逐条做了对抗式验证（打开源码核对证据、顺调用链查补偿机制）——**49 个确认成立、7 个被推翻剔除**（本文只引用确认项）。另外用演示数据实跑了 WebUI 做视觉审计（见 §6）。

---

## 0. 总评

引擎的**底座是真的**：LifeOps 的 validate→savepoint→receipt→journal 哈希链、资源账本恒等式、v0.18 把 LLM 调用隔离在写事务外的 authoring 隔离带——这些都是 grep 可验证的高水平设计。**LifeAuthor seam 是全系统最干净的抽象**。

但系统当前处于「**地质层**」状态：52 个版本、31 轮 nightly 自动迭代**只叠不并、只加不删**，每个新概念都长在旧概念旁边。四条自立的铁律里有三条没有物理载体、已被打穿；生成通道建好了却没接到最机械的路径；数据写强读弱、闭环断裂；WebUI 是「RPG 皮肤 + 数据库后台内核」。你的四条不满（WebUI 差 / 模块不清晰 / 概念不清晰 / 哲学没体现）全部能落到可指认的结构根因上——而且都可修，不需要推倒重来。

---

## 1. 设计哲学审计：六条原则，三条被打穿

| # | 原则 | 现状 |
|---|------|------|
| 1 | 确定性可审计底座（一切走 LifeOps） | ✅ 成立，但被 partial tick 重复结算侵蚀（见 §3-1），且四套并行审计+60 张 QA 仪式表让「可审计」变成「到处在记、哪里都看不清」 |
| 2 | **角色无关**：引擎不硬编码角色/世界观，内容只从 Canon 生成 | ❌ **被 6 个域打穿**（见 §3-2） |
| 3 | 两层生命架构：底座 + LifeAuthor 生成层，降级即安全 | ✅ seam 本身成立且契约被执行；❌ 但没接到最需要它的路径（见 §3-4） |
| 4 | 真值分层（dream_symbolic / rumor_unverified / user_life 需确认） | ❌ 三套同名不同义词表互不贯通，无统一枚举、无升降级状态机，除梦外 truth_layer 是自由文本 |
| 5 | 「不被编译器/目录/grep 强制的规则必被违背」（项目自己的格言） | ❌ 被自己系统性应验：37 个 gate 键 18 个死键、五处命令表面手工同步已漂移、doctor 清单停在 v39 |
| 6 | 北极星：各有各的生活、互相讲述、互相陪伴、自洽自发 | ❌ 在引擎（单 agent 自闭宇宙）、数据（user_id 分裂）、UI（单主角+无叙事流）三层都没有支点 |

哲学「没有体现出来」的直接原因：它散落在一个埋在 v0.11.18 历史补丁包里的 12 条原则文档、v0_18_0.md 的缘起段和代码 docstring 里——**没有任何现行载体**（无常青架构文档、无 GLOSSARY、无 schema/注册表/一致性测试，UI 更无从展示）。

---

## 2. 概念地图（现状）与概念债

全系统概念可收敛为五簇：

1. **确定性生活底座**：LifeOps/收据/journal 链、资源账本、Event/ScheduleBlock 状态机、heartbeat tick（16 个子流程）、睡眠/唤醒、realtime state、module_gates、recurring(营生)/daily_rhythm/campaign 三套日程物化、execution 盖章、serendipity。
2. **生成式内心生活（LifeAuthor）**：author() seam、heartbeat_authoring 预生成包、梦、companion、proactive intent→outbox→delivery、campaign 资料片、reflection→opinions、persona 六维、mood、memory。
3. **世界与社会**：Canon（真源）、world_model 八种记录+地图、social_world 九件套（实体/关系/声望/评价/流言）、social_projector、relationship_notes/user_life。
4. **治理与人审**：ReplyGate、FinalGate+receipts、review 收件箱（+五层 managed 元塔）、doctor/invariants、behavior_mapping、时间仲裁、confirmations。
5. **表面层**：WebUI（server+reader+app.js）、41 个 life_* 工具、CLI/slash 五面、plugin.yaml、SKILL.md、50 份 delta 文档。

**核心概念债**（重构时必须收敛，全部已验证）：

- **长期意图 ×4**：`goals` / `life_arcs`（文档自认惰性容器）/ `campaigns` / `recurring_activities` 三套心跳物化、三套幂等 occurrence 表互不复用；「营生」一物四名（recurring_activities 表 / Venture 概念 / life_activity 工具 / occupation 注释）。
- **一名多物**：「资料片」= campaigns 又 = world_chronicle 的 expansion_key；「reflection」= goals.py 的 life_reflections（proposed_ops 从未应用）又 = v0.18 观点反思循环；「review」既指人类收件箱又指 managed 自动循环。
- **态度 ×3**：persona_traits / agent_opinions / social_evaluations 并存无分工；「世界」×3（Canon worldview / world_model / social_world）边界只活在一句 docstring；地点双重身份（world_places vs world_entities venue 互不引用）。
- **truth_layer 同名三义**：truth_sources 词表 / 社会层词表 / dream_symbolic，无统一枚举。
- **所有权双轨**：全库 (owner_kind, owner_id)，但恰好 7 张「活人感核心表」（proactive/relationship/opinions…）用独立 agent_id/user_id；user_id 默认值在记录方（anonymous-user）与消费方（default_target_user_id）之间分裂。
- **可观测性 ×4**：life_journal / audit_log / trace_runs+spans / heartbeat_runs.output_json 存同一份 tick 输出三遍；trace_spans 只写不读。
- **module_gates 杂物抽屉**：37 键中 **18 键无任何运行期读取**；数值配置（`context_budget_chars="5200"`）混进门控值域；startup_check 会静默把用户设置翻回默认（constants.py:37-91）。
- **命名误导**：DreamAudit 实为夜间系统自检却挂梦名下；execution「模拟器」只盖章不模拟；observatory 名为只读实有 8 种写操作；behavior_mapping 守护的是 `private://` 占位 URI（无执行器，纯剧场）。
- **验收剧场**：`upgrade.acceptance_suite` 硬编码全 `passed` 写进永久审计表（upgrade.py:626-677，output 自认 `"synthetic": True`）；约 60 张 QA 仪式表 ≈ 1/3 schema 混在生活库里——与「梦里禁止出现自检」的产品追求自相矛盾。

---

## 3. 六大系统性根因（跨域归纳，全部有已验证证据）

### 3-1. 追加式演化、从不收敛
66 版线性迁移无 baseline（v5 建 inventory、v46 又 DROP）；191 张表近 1/3 是仪式表；50 份「总设计」全是 delta、最后一张全景架构图停在 schema v18 时代（代码已 v66）；60+ 个按版本号命名的测试文件、22 处 `PLUGIN_VERSION == "0.18.0"` 精确断言每发版群改。**这是「概念不清晰」的第一根因**，并形成「越乱越不敢动」的死循环。

### 3-2. 规则无物理载体：角色无关铁律被 6 个域打穿
- `living.py:135-186`：`GUIMINGGUAN_RESOURCES`（灵铢）+ 日常节律模板「归明观晨巡」「**写一张给 Ringo 的小纸条草稿**」——**用户真名进了引擎源码**；`rhythm_templates` 的 preset 参数是假旋钮，传任何值都返回归明观模板（已验证）。
- `companion.py:511/111`：idle prompt 硬编码「可以自然叫他"师兄"」、去重词表硬编码 ("师兄","Ringo",…)。
- `receipts.py:371-376`：通用证据匹配器焊死「符纸/朱砂/雨棚巷/第七城/咖喱饭」词表。
- `social_projector.py:28-43`、`social_world.py:133-171`：投影关键词与默认社会槽含「归明观」「香客口碑」。
- `webui/reader.py:520,708`：默认角色名「明灯」；index.html 标题「归明观 WASTELAND OBSERVATORY」。
- 实测佐证：用「现代插画师」Canon 建新库，tick 一次即冒出「傍晚记账与灵铢收支整理」修仙日程。**第二个 agent 天生是同一个人。**

### 3-3. 上帝门面 + 平行清单：代码没有模块边界，所以任何表面都画不出模块边界
`runtime.py` 5806 行 / 95 方法，新增一个心跳模块要手工同步 4 处；`reader.py` 1489 行是手写的第二套 SQL 读者；argparse/slash/tools/DOMAINS/SKILL.md 五面各写一份 action 清单且已互相漂移（plugin.yaml 缺 v0.18 全部 7 个新工具）；383 个测试全走 runtime 门面把 108 个方法签名焊死。**「模块展示不清晰」的技术根因在此**：引擎没有可枚举的模块注册表，UI/CLI/文档只能各自手抄世界观然后各自漂移。

### 3-4. 生成通道建好了，却没接到最机械的路径（机械感总根因）
- 执行收尾永远是 `执行完成：{title}`、记忆永远是 `完成了『{title}』。`（execution.py:602-607）。
- serendipity 是 7 条硬编码标题的确定性映射，canon 里的 `dailyMinorEventProbability/dramaLevel` 配置**全库零读取**（execution.py:482-505）。
- **最自主的路径——heartbeat auto_send——从不走 LifeAuthor**，落到 `_fallback_outbox_text` 的词面替换手术（proactive.py:541-564）。
- 梦是生成的，但分享语每天被同一句固定前缀二次包装：「我刚醒，梦到了一点和最近生活有关的东西：…」（dream.py:532, sleep_reply_dream_policy.py:79）。
- 社会「事实」由关键词嗅探生成：声望恒 +2.5/-2.0、流言只有两条固定句式、heat 恒 0.24（social_projector.py:190-296）。
- 「语义记忆」是 blake2b 哈希词袋伪向量（embeddings.py:26-38 自注 "not a semantic model"），联想只剩字面匹配。

### 3-5. 写强读弱、闭环断裂：生活被记录了，但没被活出来、也没被展示
- dream/companion/reflection/autonomy 对 world_model/social_world/rumor 的引用 **grep 为 0**——3700 行世界基建是死布景；世界自身也不流动（condition 永不过期、rumor 永冻、声望机械封顶）。
- opinions 只反哺「说什么」（prompt 素材），从不反哺「做什么」（plan_autonomy 的目标选择与 agent_opinions 完全无关）；persona 漂移了也无行为差异。
- 引擎在写、reader 零引用的「活着的证据」：diary_entries、goals/life_arcs/reflections、life_author_runs、serendipity_events、persona_drift_log、campaign occurrence、recurring/venture 结算、sleep_sessions、memories 主体——**191 张表只暴露约 40 张**；查询失败还静默渲染成「她今天什么都没做」（reader.py:103-113 吞错；doctor_runs 幽灵表事故已实际发生）。

### 3-6. 单 agent 单用户假设焊死在每一层
social_world 数据模型无法表达另一个真 agent（实体全是 NPC 投影，跨 owner 引用被拒）；**user_id 身份分裂**：relationship 记录方默认 `anonymous-user`（relationship.py:22），dream/reflection/companion 消费方默认 `default_target_user_id`（companion.py:190-197）——生产部署下「你讲你的生活→她惦记回访/入梦」这条北极星回路**大概率静默读空**（critical，已验证）；383 个测试零多 agent 用例。好消息：owner 已全线参数化，`tick(owner_id)` 天然可循环多 agent——差距是数据模型与通道，不是推倒重来。

---

## 4. 数据正确性缺陷（critical，修复不等重构）

1. **partial tick 重复结算**：`_minutes_since_last_tick` 只认 `status='done'`（runtime.py:1617），而 partial tick（16 个子系统任一报错即 partial）已经结算过资源——下一个 tick 从更早的 done 水位重复结算 energy/mood/fatigue，账本失真级联污染睡眠/自治/执行决策。
2. **失败 wake_job 是终态**：due_wake_jobs 只取 pending（events.py:376-382），失败无重试、reaper 只回收 running、fallback sweep 明确跳过 sleep 块——**一次瞬时失败 = 无限沉睡**直到人工 `/life call`。
3. **user_id 分裂**（见 §3-6）。
4. **WebUI review「忽略」按钮实际执行动作**：app.js:2297 忽略→`choice:'dismiss'`，server.py:288 却一律映射 `rt.review("apply")`——与用户意图**相反**；且 review 条目按 5 分钟心跳无限重复堆积、dismiss 不持久（review.py:1024-1037 每次全量 INSERT）。
5. **LLM 文本未转义直插 innerHTML**（app.js:709,771,2294,2388…）：存储型 XSS + 排版破坏。
6. **master 测试已红且无 CI**：test_lifeengine_v018_companion.py:296 写死 `2026-06-24` 撞 `datetime('now','-7d')` 窗口，7/2 起确定性红灯；仓库无 CI/pytest 配置，全量 12 分钟串行（每用例重放 66 版迁移）。

---

## 5. 「活人感」差距排序

**引擎侧**（按影响力）：
1. 生活内容全是模板盖章（§3-4）——生成层接管是**投入产出比最高**的改造：隔离带/预算/降级契约已付清，缺的只是消费端接线。
2. 经历不改变行为：opinions/persona/社会账本对「做什么」零反哺；campaign 收尾后生活退化回零散小事。
3. 世界是死布景：不进 prompt、不演化、不发酵。
4. 「互相」无支点：单 agent 宇宙 + 身份分裂。
5. 生命体征不可信：重复结算、无限沉睡、伪向量。
6. 生成上下文没有时空地基：不知道今天是冬天周日深夜还是夏天工作日清晨。

**展示侧**：
1. 生命的第一手证据不可见（§3-5）——引擎明明在生产叙事，观测台只给日程块和资源条。
2. 页面在抽搐而不是呼吸：snapshot_hash 把 `updated_at` 算进哈希（reader.py:1393-1395），SSE 去重彻底失效 → 每 2 秒 25 个子查询全量重建 + 前端 16 个面板 innerHTML 全量重渲染，表单被清、折叠被重置、SSE 断线永不重连——**一切「活着」的动效在此之前都不可能**。
3. 数据库后台感：世界/社会面板是 window.prompt 链驱动的 CMS（一次编辑 13 连弹窗）、trace 详情是 5 段 JSON.stringify、UUID/英文枚举直出、人类命令面被 managed_stress/acceptance 词汇占据。
4. 哲学与概念无可读载体：新读者（包括 nightly agent 自己）3 分钟内无法理解「它如何活着」。

**实跑 WebUI 佐证**（演示库 + 逐 tab 截图）：舞台/立绘/日程栏的美术底子其实不错（国风赛博废土主题用心），但——冷启动全是「还没有/无数据」空墙无任何引导；`busy / immediate / soft_interruptible / planned · finance` 等原始枚举直出；「阵盘」就是引擎内参裸倒（`引擎状态active`、37 个英文 gate 键值表）；persona 六维显示 0.00 裸数字；标题栏焊死「归明观」。

---

## 6. 迭代方案：五轴

> 顺序逻辑：轴一是保险网（不动概念不动 UI）；轴二、轴三可并行（共享心跳注册表改造）；轴四的 P0 可立即做、主体依赖轴一/二/三的产出；轴五收官。**必要的重构就是轴二——但它是「收敛式重构」，不是重写。**

### 轴一：安全网与生命体征（1 周量级）
**目标**：账本重新可信、agent 不再「死机」、全套件回到可信绿灯且快到敢每次全量跑。

1. 修 partial 重复结算：`_minutes_since_last_tick` 改 `status IN ('done','partial')` 起步，随后引入独立 `last_settled_ts` 水位与 tick 成败解耦。
2. wake_job 加 `failed→pending` 重试 + attempt_count/backoff（尤其 sleep_plan_wake）；删掉与单事务现实脱节的 claim/reap 协议。
3. recurring/campaign 物化补偿：`record_occurrence` 移入同一 LifeOps 事务（daily_rhythm 已有正确范式可抄）。
4. 修 companion 时间炸弹测试（注入 now）；建共享 conftest（fresh_home/engine/fake_llm fixture），消灭 51 份样板。
5. session 级 DB 模板缓存（迁移一次、每测试 copy 文件）+ pytest-xdist：12 分钟 → 1-2 分钟；加最小 CI（哪怕 pre-commit 钩子）。
6. 缩短锁窗口：纯读门面改只读连接，tick 拆每子流程短事务——心跳不再阻塞对话。

### 轴二：概念收敛与结构拆分（还债，2-3 周量级）
**目标**：把地质层收敛成**可枚举的模块注册表**与**单一术语真值源**——直接回应「概念不清晰、模块展示不清晰、哲学没体现」。

1. **拆 runtime.py**：ops_engine（OP_REGISTRY 字典分发）/ heartbeat_orchestrator（`HeartbeatModule{name,gate,runner}` 注册表，tick 变 for 循环，消灭 4 处平行清单）/ facades / context_builder / diagnostics。门面方法签名保持薄委托，383 个测试不用群改。
2. **gate/truth schema 化**：GateSpec（kind/allowed/default/consumer），删 18 个死键、迁出数值配置、废除 startup_check 静默覆盖；truth_layer 唯一枚举 + rumor→confirmed_fact 升级 op，validators 强制。
3. **删验收剧场**：acceptance_suite/concurrency_smoke 等合成部分（约 2600 行 + ~60 张仪式表）迁出生活库；同步删死表死代码（inventory 残留、doctor_runs 幽灵、owner_scope 重复定义、3.8MB 死资产）。
4. **概念合并**（需你拍板语义）：life_arcs 并入 goals 或 campaigns；营生统一叫 venture；「资料片」只留给 campaign；DreamAudit 改名 nightly_check 迁出梦域；7 张 agent_id 表迁到 owner 双键。
5. **schema squash**：生成 v67 baseline 一次建全（新库不再重放 66 步），旧链只服务存量库；doctor 合并两套实现、REQUIRED_TABLES 从 schema 自动推导、补北极星层不变式。
6. **单一命令注册表**生成五面（argparse/slash/tools/schemas/SKILL/plugin.yaml）+ 一致性测试；写 ≤500 行**常青现状总设计** + GLOSSARY.md（用测试 pin 住枚举一致），50 份旧文档移入 `docs/history/`。

### 轴三：生成层接管机械路径 + 世界活化（活人感引擎侧，2-3 周量级）
**目标**：消灭机械感的引擎根源，让经历真正改变行为。

1. `prepare_heartbeat_authoring` 扩面：执行完成叙事、serendipity 文案（终于消费 canon 的概率/戏剧度）、**auto_send outbox 草稿**、社会投影措辞、日记草稿——canned 字符串一律降级为无宿主兜底；删梦分享固定前缀（authored share_text 直接落库）。
2. `resolve_primary_user` 统一四个消费方身份，接通「你讲生活→入梦/回访」回路。
3. 所有 authoring context 注入**时空地基**：local_now/季节/天气/地点/距上次对话间隔/上个梦的 symbols 残留 + effective_context 的流言/声望/conditions。
4. **观点反哺行为**：plan_autonomy 读 salient_opinions 偏置目标选择；心跳检测「无 active campaign 已 N 天」自动 seed（top opinions+goals 为 brief）；persona 维度接 companion 频率/题材偏好。
5. **世界演化 tick**：condition 按 ends_at 过期、rumor 衰减+一步传播、声望沉寂回归——全走 LifeOps 保持可审计；memory_vec 换宿主真 embedding 或诚实删除只留 FTS。
6. **角色内容全部迁出引擎**：师兄/Ringo/归明观/明灯/符纸词表/Asia-Tokyo → Canon/世界观数据包，兑现角色无关（轴五前提）。

风险：生成扩面 = token 预算与延迟上升——沿用 kind 注册表统一预算、保住降级契约；社会投影改生成后需重新设计幂等（同一事件重跑不得产出不同事实）；观点反哺先以偏置权重灰度。

### 轴四：WebUI 重做（你点名的轴，2-4 周量级）
**目标**：从数据库后台变成**生命观察站**——增量呼吸的舞台 + 按时间流动的生活叙事 + 只读游戏化世界；工程/QA 面整体退到管理员区。技术栈**不换重框架**：保留 Python server + SSE，前端拆 ES modules + 轻量响应式做增量渲染（群像舞台阶段可选 PixiJS 只接管 stage 画布）。

**P0（零依赖，立即）**：
- snapshot_hash 剔除易变字段；SSE 断线自动重连；
- LLM 文本统一强制转义（`h```` tagged template）；
- review「忽略」按钮改调 dismiss、needs_choice 展开真实选项；
- 删 doctor_runs 幽灵查询、吞错改 degraded 标记上报。

**读层重建**：reader 直接复用引擎 list_*/read model（删 ~450 行复制 SQL），按轴二的模块注册表生成 per-domain 端点 + cursor 分页；**补齐「活着的证据」端点**（diary/goals/reflections/life_author_runs/persona 漂移史/ledger 历史）；建 schema↔endpoint 覆盖契约测试。

**推送重建**：以 life_journal 为 changefeed 按 rowid 游标增量推事件 → 前端定向动效（新日记浮现、资源跳动、小人走位）——这是一切游戏化动效的前置条件。

**信息架构收敛为四域**（替代现在的 12 tab）：
1. **舞台**——HUD 状态条、下次心跳倒计时环、自动节奏可视化（手动 tick 降级进 debug 菜单）、场景/sprite 由 Canon 驱动；
2. **生活日志**——**LifeFeed 叙事卷轴**：日记/梦/反思/观点变化/偶遇/campaign 节点/主动消息合并成按时间流动的「她的一天/一周」，把「可审计」翻译成日记而非 JSON——这是游戏界面的天然骨架，也是设计哲学第一次可被看见的地方；review 收件箱改成「她想跟你说的事」第一人称叙事（review.py 已证明可行）；
3. **世界**——只读游戏化：接上闲置的 guiming-outskirts-map.svg 思路但数据驱动、流言热度气泡叠加地点、声望徽章、编年史时间轴；
4. **系统**——明确标注管理员区：世界 CMS（window.prompt 链换真表单）、gate 面板、trace、doctor。

**表现层归位**：sprite/scene 映射、中文标签、角色名全部由 Canon/identity 驱动；枚举值走统一文案表；空态给引导文案（冷启动第一课：「她还没醒来——点这里给她立一个 Canon」）；删 3.8MB 死资产。

**测试网先行**：渲染函数抽纯函数做 node 断言 + 一条 Playwright 冒烟（seed → 舞台/卷轴/世界渲染可见）。建议先出四域静态原型让你确认审美方向，再动代码。

### 轴五：多 agent 小世界（北极星轴，收官）
**目标**：第二个 agent 真实存在——各有各的 Canon 与作息、互为对方社会世界里的实体、互相讲述与陪伴，WebUI 群像。

1. agent registry + cron 循环 tick 所有活跃 agent（owner 已参数化，改造面小；依赖轴三角色外置）。
2. social_world 加 `entity_kind=peer_agent` + 跨 owner 引用；最小 inter-agent outbox：A 的讲述投递为 B 世界里的 `rumor_unverified`/relationship note——真值分层照旧。
3. companion/proactive 的 target 扩展为另一 agent（复用 delivery.py 的原子 claim/静默时段——这是全系统工程质量最高的模块）。
4. WebUI 群像：per-connection owner 流、同屏多小人、agent 间讲述以气泡对话呈现。
5. `tests/constellation/` 多 agent 验收先行立起，当作轴二重构的导航。

风险：轴二（所有权统一）与轴三（角色外置、身份统一）未完成就上线 = 两个 agent 互相复读模板。所以先做数据通道与测试，再开生成面。

---

## 7. 建议的落地节奏

| 里程碑 | 内容 | 验收信号 |
|---|---|---|
| **M0（第 1 周）** | 轴一全部 + 轴四 P0 | CI 绿灯 <2min；账本对账在 partial 场景成立；WebUI 不再每 2 秒抽搐、忽略按钮语义正确 |
| **M1** | 轴二 1-3（拆 runtime/gate schema 化/删剧场）+ 常青总设计与 GLOSSARY | `tick()` 是注册表 for 循环；`grep -c 'CREATE TABLE'` 显著下降；新读者拿一份文档能画出系统 |
| **M2** | 轴三 1-3（生成接管+身份统一+时空地基）∥ 轴四读层/推送重建 | 连续 7 天日志无逐字重复句；「你讲的事」出现在她的梦里；WebUI 出现增量动效 |
| **M3** | 轴三 4-6（反哺+世界活化+角色外置）+ 轴四四域信息架构 | 换一个全新 Canon 建库，零修仙残留；LifeFeed 卷轴可读成「她的一周」 |
| **M4** | 轴五 | constellation 测试绿：两个 agent 互相出现在对方的流言与梦里 |

**先做 M0**：它不动任何概念，纯粹止血，且立刻能感知（页面不抽搐、收件箱不撒谎、测试敢跑）。M1 的概念合并（arc/campaign/营生命名）需要你对语义拍板，其余都可直接执行。

---

## 附：本次审计的已验证结论索引

- 运行时：partial 重复结算（runtime.py:1617）、wake_job 终态（events.py:376）、5806 行上帝门面、单 BEGIN IMMEDIATE 包全 tick、执行层橡皮图章（execution.py:602）、gate 崩坏（18/37 死键）。
- 生成层：user_id 分裂（relationship.py:22 vs companion.py:190）、角色硬编码（companion.py:511）、梦分享固定前缀（dream.py:532）、auto_send 不走生成（proactive.py:541）、观点不反哺行为（autonomy.py:510-632）。
- 世界社会：生成层与世界零连接（grep 0）、投影=关键词+固定数值（social_projector.py:190-296）、单 owner 宇宙、truth 三义、世界无演化。
- 数据层：191 表 1/3 仪式表、66 步迁移无 baseline、doctor 三份漂移清单停在 v39、伪向量（embeddings.py:26）、schemas.py 130 别名 enum。
- WebUI 后端：40/191 表暴露率、SSE 伪推送（reader.py:1393）、吞错渲染空生活、表现层混进 reader（明灯/场景映射）。
- WebUI 前端：全量重渲染、忽略按钮反义（app.js:2297）、XSS、window.prompt CMS、raw JSON/UUID 直出。
- 命令面：五面漂移、plugin.yaml 缺 7 工具、QA 词汇占据人类命令面。
- 人审：review 无限堆积（review.py:1024）、「官网/杂志」被静默改写成「逛街买衣服」（behavior_mapping.py:406）、FinalGate 关键词袋测谎仪、behavior_mapping 纯剧场。
- 文档：无现状架构文档、schema 66 vs 文档 61、constants 与文档五处矛盾。
- 测试：master 红灯+无 CI、验收剧场（upgrade.py:626）、金字塔倒置、前端零覆盖。

被对抗验证**推翻**而未采信的 7 项（示例）：「campaign 无任何自发起点」（有补偿机制）、「API 全局单 owner」（12 个读端点带 owner 参数）、「时间流逝全靠手动推进」（cron 心跳存在，UI 只是没展示节奏）、「北极星层人类命令面完全缺席」（/life interface 可达，只是不直觉）。
