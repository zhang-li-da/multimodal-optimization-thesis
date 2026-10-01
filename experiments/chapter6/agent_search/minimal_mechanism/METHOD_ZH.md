# 第六章候选方法：G/P/F 的离线可执行定义

状态：2026-10-01 离线修订。e40ce10 已执行一个暂停的 SP 公共前缀批次（manifest 29cc55e3），不是 G/P/F 对照。此版本没有新增模型调用，不追溯更改该批次，不授权续开发或 C/D/E。

## 科学问题与边界

研究问题是：在有限生成与评价预算下，智能体如何提出潜在有价值的外层策略方向，给方向有限试用，并根据已成熟的进展和替代行动的机会成本决定继续、暂存或退出？

外层决策空间是智能体能选择的生成、改写、分支开发、策略假设和预算动作。由这些决策产生的程序、计划和父子轨迹是过程记录。TSP 路线及其启发式参数属于任务内部空间；路线行为簇只能作为行为证据，不能直接当作不同外层决策方向。

本候选实现对应 A/B/M 与 G/P/F：A 维护方向档案，B 管理有预算承诺的投资项目，M 记录决策、进展、失败、成本和成熟收益；G 是有历史信息的方向生成，P 是项目身份下的有限试用和条件续期，F 是搜索时收益与机会成本规则。名称本身不构成贡献。历史 E1/E2/S3 与短程组件研究没有给出本实现的性能证据。

## 假设及判定

- H1：相同模型、接口、评价器和输出契约下，历史信息组 EG 比无历史、无父代组 E0 更常生成有效且接近 incumbent 的起点，并提高后续收益。阶段 B 只估计 EG−E0 的首个有效起点差距、可开发近 incumbent 起点率、4/8 步 Test 结果和成本；阶段 C 是自然搜索消融，不产生匹配的 EG−E0 诊断，也不能据此判“支持 H1”。只有阶段 D 中 G 保留在 M*，且独立比较同时显示 G 开启提高首个探索起点“有效且 validation loss 不高于 incumbent + 0.035”的区块平均率（预注册区间下限大于 0），以及 `TestGap(M*−G) − TestGap(M*)` 的区块级、组件比较 Holm 校正区间下限至少为 +0.3 pp，才支持 H1。Test 效应始终按四类区间规则单独分类；若其区间完全位于 `(-0.3, +0.3) pp`，判为实际收益不足，若可靠负效应达到 -0.3 pp 伤害门槛，判为有害，其余为证据不足。起点率过程门是支持 H1 的必要条件，不覆盖 Test 效应分类。若 G 在 C 被删除，H1 保持证据不足且没有独立确认比较；删除本身不证明 G 无效。
- H2：项目身份、有限试用和有证据续期保留的落后项目，带来的成熟增量须超过同等资源替代行动的机会成本。阶段 B/C 的触发、续期及局部收益只作诊断/筛查。阶段 D 中 P 保留在 M* 且独立 P 消融时，只有 `TestGap(M*−P) − TestGap(M*)` 的区块级、组件比较 Holm 校正区间下限至少 +0.3 pp，并且续期 tranche 的成熟收益率减去同一时点最佳已观测替代行动收益率的区块级区间下限大于 0，才支持 H2。Test 效应始终按四类区间规则单独分类：区间完全位于 `(-0.3, +0.3) pp` 判为实际收益不足，可靠负效应达到 -0.3 pp 判为有害，其余为证据不足。机会成本过程门是支持 H2 的必要条件，不覆盖 Test 效应分类。若 P 在 C 被删除，H2 保持证据不足且没有独立确认比较。
- H3：依据搜索时可观测的成熟收益和预算约束分配，在相同冻结资源上限下提高最终单程序 Test 质量，相对强简单固定分配 FIX。唯一确认口径是 `TestGap(M*−F) − TestGap(M*)` 的区块级、组件比较 Holm 校正区间下限至少 +0.3 pp。阶段 B/C 只作诊断/筛查。token、请求、评价次数和时间单列报告，不组成可事后切换的预算效用，也不据此单独宣称预算效率；如要作预算效率主张，需另行预注册终点、实际门槛和确认实验。组件效应区间完全位于 `(-0.3, +0.3) pp` 为实际收益不足；可靠负效应达到 -0.3 pp 为有害。若 F 在 C 被删除，H3 保持证据不足且没有独立确认比较。
- H4：只有开发阶段选出的最小方法在独立确认中达到预注册支持标准后，才检验它在独立数据、较宽外层决策空间和另一任务载体上的作用。对每个范围分别判定：预注册比较可靠且达到该范围事先冻结的实际收益门槛为支持；区间仍覆盖有意义收益及无效/有害区域为证据不足；精度排除实用收益门槛且不显示有害效应为实际收益不足；可靠负效应达到预设伤害量级为有害。未进入阶段 E 的范围标为未检验，不能归为支持、无效或有害；只有三个范围各自有独立数据和测试边界时，才可陈述三者均获支持。结论只适用于实际覆盖的任务与接口。

正式判定区间为区块级配对差的 Holm 校正区间。对方向已转成“正值有利于方法”的确认终点，预注册实用门槛为 `δ`：本章 TSP Test gap 的 `δ=0.3 pp`，H4 每个新任务/范围须在进入 E 前另定其质量单位和实际门槛。支持要求区间下限 `>= +δ`；有害要求区间上限 `<= -δ`；实际收益不足要求区间完全落在 `(-δ, +δ)`；其余为证据不足。H1/H2 要分别报告 Test 效应的四类结果和联合假设结论：联合假设只有在过程门与 Test 效应都满足支持条件时才支持；Test 效应达到有害门槛时判有害，区间完全位于实际收益不足范围时判实际收益不足；其余情形（包括过程门失败/不可估但 Test 结果指向正收益，或 Test 精度不确定）判证据不足。这样过程门不能改写 Test 效应分类，也不能在缺少机制证据时把正向 Test 结果升级为联合支持。若 M* 未保留某组件，该组件假设仍记证据不足且不做独立确认，不把开发筛除当作零效应结论。“未显著”不等同无效；“未显著变差”不等同等效。阶段 B/C 的点估计不升级为支持性证据。最终 Test 不参与方向、项目、门槛或参数选择。

## 状态和身份

每个已评价程序有三个分离标识：`behavior_cell_id` 是任务评价器给出的行为簇；`investment_id` 是有限资源承诺的项目；`lineage_id` 是项目分叉后仍共享总预算的祖先谱系。程序哈希由规范化可执行程序得到，结构指纹由任务适配器产生。项目进展始终相对该项目的历史最好 validation loss，而不是当步父代：

```text
local_gain = max(0, project_best_before - candidate_loss)
global_gain = max(0, incumbent_before - candidate_loss)
```

只有 loss 严格低于对应最好值至少 `gain_epsilon=0.0001` 才更新最好值、计进步并清零停滞计数。重复程序哈希不计为进步；退步后回到旧最好值不计新进步；行为簇改变不重置项目或额度。行为簇合并只把来源标签别名到目标，保留项目与谱系。合并需有可审计证据。项目退出后留在档案，不占活跃池；后续 incumbent 开发只有在产生新的、超过 epsilon 的项目最好进步且通过续入条件时才可重新申请额度。

不同策略假设的操作性分叉规则是：候选不是重复程序、四个规范化假设字段都完整、假设字段哈希改变，并且任务适配器结构指纹不同于项目最好程序。通过时创建子 `investment_id`，沿用父项目 `lineage_id`，共享谱系额度。行为簇变化单独不足以分叉。假设文本仍由模型提供，因此该规则只能作为可审计代理；不能仅凭自报名称宣称发现了真正的新策略，需检查程序差异和轨迹证据。

## G：有历史信息的方向生成

E0 和 EG 都是无父代探索，使用同一模型、系统指令、程序接口、输出长度边界、温度和评价规则。两组都必须输出相同四字段：`target_failure`、`mechanism`、`expected_behavior_change`、`falsifiable_prediction`，再给出可执行候选；字段是待检验假设，不作为质量证据。E0/EG 生成器与控制器的规范输出名一致；控制器仅为历史输入兼容 `expected_behavior`→`expected_behavior_change` 和 `prediction`→`falsifiable_prediction` 两个旧名，live 输出仍要求规范名。EG 唯一新增的生成提示信息是截至当前 proposal 的搜索/validation 历史上下文，E0 的 history 为空。不得把 Test、未来 proposal 或测试选优信息写进上下文。

方向档案上下文采用确定性本地序列化：按 validation 最好值及 `behavior_cell_id` 排序，最多取 `context_cards=6` 张卡；卡片含行为簇、规范化假设、最好 validation loss、程序/结构指纹、失败记录和成员数。没有独立摘要模型调用；真实上下文 token 按 planner 返回的实际输入 token 记账，序列化 CPU/wall 时间单列。输入字段和 schema 本身不得因 EG/E0 而异。

## P：有限试用、续期和退出

默认控制器参数为 proposal 总预算 64、活跃项目池容量 2、初始试用 4 槽、续期 2 槽、单谱系槽上限 8、质量差距容忍 `0.035`、停滞上限 2 次、探索间隔 8 槽、历史卡片 6 张。阶段 B 的任务适配器可以显式冻结不同的 task-level 额度；每次 live study 必须在协议中单独登记实际值。

P 开启时，一个有效 E 起点仅在程序非重复、loss 不高于当时 incumbent loss 加 `0.035`、且未重复已有项目的假设哈希与结构指纹组合时建立投资项目。根 proposal 本身消耗一个谱系槽，随后申请初始试用槽。获得额度前须同时满足：

```text
global_free = proposal_budget - proposals_used - all_reserved_slots
lineage_free = lineage_cap - lineage_spent - lineage_reserved
```

初始试用/续期请求数不得超过 `global_free` 和 `lineage_free`；新承诺还须有活跃池位置，并在授予后至少留下 1 个全局未承诺 proposal 槽：`global_free - requested_slots >= 1`。续期和退出项目重新进入使用同一保留槽规则。预留立即记入全局与谱系账本，执行一个 B proposal 时同步扣减预留并增加实际消耗。无可兑现未来预算时拒绝承诺；该槽保留至少一次不受项目承诺保护的全局搜索行动。

每笔承诺维护 `awarded_slots`、`redeemed_slots` 和 `forfeited_slots`。在每个 proposal 边界必须满足：

```text
outstanding = awarded_slots - redeemed_slots - forfeited_slots
sum(outstanding) = active_project_reserved_slots
active_project_reserved_slots = global_reserved_slots = lineage_reserved_slots
```

若普通的未承诺搜索行动已消耗保留的自由预算，剩余承诺槽必须优先按最早创建时间兑现（同序时按 `investment_id`）；不得让其他行动占用已预留槽。冻结的服务暂停等提前停止条件下，不执行这些承诺；调用 `forfeit_unredeemed_commitments(reason)` 明确将未兑现槽记为 forfeiture，释放全局与谱系预留并归档项目，不制造成熟收益。停止前须先解决 pending proposal；空原因、自动重试或把未执行承诺记作零收益均不允许。

试用结束后成熟收益为本 tranche 开始时项目最好 loss 与结束时项目最好 loss 之差；项目 ROI 是本 tranche 项目进步除以完整、已知 token 成本。对 `entry_kind=exploration` 的首次试用 tranche，成本从项目创建时开始累计，包含 E 起点的 planner/coder 实际 prompt token 与首次 B 试用成本；该 tranche 的收益率归入一个 E→开发过程，不再计入 B 收益率。后续 renewal tranche 的增量收益率归入 B。由已有分支上的策略分叉创建的首次试用归入 B。成本未知或 tranche 含 `sent_unknown` 时 ROI 为未知，不填零。候选项目只有同时满足以下条件才可续期：成熟项目进步大于 epsilon；当前项目最好值与 incumbent 差距不超过 `0.035`；连续无进展不超过 2 次；完整测得的 ROI 严格高于机会成本；剩余谱系额度至少可兑现 2 个 proposal，且全局未预留额度至少为 `2 + 1` 个 proposal，其中 1 个保持未承诺。机会成本是已观测 incumbent ROI 和其他项目的成熟 E→开发或 B tranche ROI 的最大值；当前被判断的项目不与自身历史收益比较。没有成熟替代收益时使用 0 的冷启动比较下限，但账本仍将其标为未观测而非实测零。局部变好本身不构成续期理由。

达到 tranche 末端后，项目先退出活跃池再决定是否重新承诺。失败、无效输出和连续停滞会计入无进展；重复程序不提升项目最好值。额度耗尽、质量差距过大、停滞或 ROI 不胜机会成本都会归档项目。续入也适用相同谱系、全局预算和机会成本检查。

P 关闭时完全不创建投资项目、谱系和试用/续期承诺。项目式 B 改为从方向档案中取 validation 最好、程序哈希不同于 incumbent、loss 在质量容忍范围内的代表程序；固定 B 槽无可用代表时转给 I。该路径仍可记录行为档案，但没有项目身份或承诺额度。这是 FULL−P 相对 FULL 的消融定义。

为区分 P 的触发与即时行动变化，在每个自然搜索 proposal 前，对当前同一搜索状态做一次局部 masked counterfactual：保留已有方向档案、incumbent、已成熟收益和探索时钟，临时关闭 P 并移除所有活跃投资池分支/预留，再用相同 F 状态重选 action。分别记录 `outer_action_changed`、`parent_changed` 与 `investment_target_changed`。这是单步条件对照，不是 P 从搜索开始关闭的轨迹性能反事实；长期轨迹和测试质量只能通过预注册消融搜索比较。P 的项目 admission、续期资格和成功承诺分别计数；资格触发不能与真正获批混报。FIX 在无项目身份的 P-off 基线中取静态档案代表，所以 FULL 与 P-off 都选 B 时仍可能只改变 parent 或 investment target。

## FIX 和 F：行动选择

**阶段 C 前待冻结事项：跨行动收益尺度。** 下述代码仍是阶段 A 的候选启发式：I 使用全局改善/token，而部分项目使用自身追赶进步/token；两者直接比较可能偏爱起点较差的项目，尚不能解释为可靠机会成本。项目进步只作为续期的辅助证据；跨 I/B/E 投资比较必须面向相同的全局搜索目标。若用局部进步预测未来全局收益，需在独立开发证据上验证映射，明确成熟窗口、费用归属、删失和不重复归因。本轮不改成未经定义的预测器，不执行或验证新的 F；阶段 C live 前必须另行解决和冻结。

强简单固定基线按 proposal 序号冻结为 `I, I, B, E0` 四槽循环，即 64 槽中的 I/B/E 比例为 32/16/16。I 开发当前 incumbent；B 在可用项目中选最好 validation 代表，或在 P 关闭的固定多分支基线中选档案代表；E0 无历史探索。没有合格 B 时该槽给 I，不挪用其它槽、不改变长程比例。每个 action 的最好代表随 validation 更新。

F 的当前低参数选择规则为：`last_explore_step` 初始为 0，因此首个强制探索不早于 proposal 8；此后当当前 proposal 与最近 E proposal 相距至少 8 时强制探索。若活跃项目承诺已占满剩余预算，则必须兑现最早的活跃承诺；所有可选行动均无成熟收益时回到固定规则。否则，对已观测行动计算 I 最近最多 8 个“全局增益/token”均值，对 E 使用最近最多 8 个已成熟 E→开发过程 ROI 的均值，对 B 使用当前项目最近成熟的 renewal ROI，选择已知 ROI 最大者；同值按 I、E、B 的选项顺序。未知成熟收益不作为零，不参与最大值选择，但未成熟/未知 E 仍受强制探索下限保护。续期机会成本会纳入其他项目已成熟的 E→开发 ROI 与 B renewal ROI，以及 I ROI；一个项目的首次 E→开发增益只归属 E，后续 renewal 的边际增益只归属 B，避免把同一 tranche 收益重复放入两个 action 家族。

决策顺序固定如下；强制诊断任务绕过自然调度：

| 条件（按顺序） | FULL 的选择 | 对照规则 |
| --- | --- | --- |
| 强制诊断槽 | 执行冻结的 I、B、E0 或 EG | 不计入自然策略 action-change |
| 活跃承诺占满所有未来槽 | 按创建时间、再按 `investment_id` 兑现最早承诺的 B | P-off 无活跃承诺，继续走下列选择 |
| 到达探索下限，距最近 E 至少 8 槽 | E；G 开启时提供确定性历史上下文 | G-off 使用 E0；F-off 仍用 FIX，现存已满额承诺仍优先兑现 |
| 所有可选行动都没有成熟、已测 ROI | 固定 `I,I,B,E0` 循环 | FIX 同规则；无可用 B 时转给 I |
| 其余状态 | 在已知 I/E/B 收益率中取最大值；并列按 I、E、B 顺序 | F-off 直接按 FIX 循环 |

G 的 `triggered` 是本槽选择 E 且存在可用历史卡；`request_context_changed` 表示该上下文确实进入请求，外层 action 改变不适用。P 分开报告探索项目准入、续期资格和获批承诺；单步 mask 还分别报告 action、parent 和 investment target 的变化。F 的 `triggered` 是调度规则有至少两个 action 可选，`actual_action_changed` 是 FULL 的 action/投资目标与同状态 FIX 选择不同。触发数描述规则适用情况，行动变化数描述这个状态下开关是否改了决定；二者都不是收益证据。

固定的收益账本包括全局 incumbent 增益，以及项目/tranche 的项目最好进步、成本完整性和成熟 ROI。延迟收益成熟前标为未成熟/未知。一个全局增益只记一次，不把同一收益完整复制到多个 action 的全局账本。候选过程同时记录生成 proposal、起点资格、后续开发和成本，使 E→开发可以作为有起止的投资过程分析。

## 决策伪代码

```text
for each proposal t:
    if forced diagnostic action: execute its frozen I/B/E0/EG action
    else if F disabled: action = FIX[t mod 4], route unavailable B to I
    else if active commitments use every future slot: redeem oldest commitment
    else if t - last_exploration >= 8 (initial last_exploration = 0): action = E
    else if no action has a mature measured return: action = FIX[t mod 4]
    else: action = eligible action with maximum observed return/token

    if action == I: parent = incumbent
    if action == B: parent = selected project's best node, or static archive representative if P off
    if action in {E0, EG}: parent = none; use same four-field hypothesis schema
    if action == EG: attach only search/validation-visible deterministic archive context

    generate one candidate; persist request, response, program, parent, decision and costs
    evaluate on search feedback; do not read Test
    update global incumbent only for a valid improvement beyond epsilon
    update project best against its own prior best; behavior-cell change never resets it
    account known costs; keep unknown costs and sent_unknown outcomes missing
    at tranche end: mature once, compare progress, quality, stagnation, ROI and redeemable budget
    renew only if every frozen P condition passes; otherwise archive the project
```

## 实验解释边界

离线测试和历史轨迹回放只能证明代码对指定状态作出规定动作，并正确维护身份、额度和账本。历史回放不是新提示词、父代或调度器的反事实性能估计；真实 Test 只属于已经完成的旧筛查，不能用作新方法确认数据。阶段 B 是起点与开发期限诊断，阶段 C 是自然搜索消融筛查，阶段 D 才能支持确认性方法主张。若阶段 B/C 未支持某组件，则按预定规则删除或降级，不靠追加区块、切片或参数搜索找显著结果。
