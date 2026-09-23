"""Render a Chinese technical report from completed, audited screening evidence."""
from __future__ import annotations
import argparse
from collections import Counter
import json
from pathlib import Path
import statistics


METHODS=("niche","relational","relational_qp","relational_rr","relational_qp_rr")
LABELS={"niche":"niche","relational":"00：原关系规则","relational_qp":"10：质量保护",
        "relational_rr":"01：重启修正","relational_qp_rr":"11：完整修订"}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root")
    args=parser.parse_args()
    root=Path(args.root)
    analysis=root/"analysis"
    manifest=json.loads((root/"manifest.json").read_text(encoding="utf-8"))
    measured=json.loads((analysis/"analysis.json").read_text(encoding="utf-8"))
    mechanisms=json.loads((analysis/"mechanism_audit.json").read_text(encoding="utf-8"))
    verification=json.loads((analysis/"verification.json").read_text(encoding="utf-8"))
    sensitivity=json.loads((analysis/"protocol_sensitivity.json").read_text(encoding="utf-8"))
    deployment=json.loads((analysis/"deployment_cost.json").read_text(encoding="utf-8"))
    historical=json.loads((root.parent/"historical_motivation.json").read_text(encoding="utf-8"))
    if measured["results_present"]!=200 or verification["checked_runs"]!=200 or not verification["all_checked_pass"]:
        raise SystemExit("A completed technical report requires all 200 results and their deterministic replay.")
    results=[json.loads((root/"runs"/j["job_id"]/"result.json").read_text(encoding="utf-8")) for j in manifest["jobs"]]
    summaries=[r["summary"] for r in results]
    groups=measured["group_summary"]
    contrasts=measured["paired_contrasts"]
    def group(model,task,budget,method,metric):
        return next(g for g in groups if (g["model"],g["task"],g["regime"],g["method"],g["metric"])
                    ==(model,task,budget,method,metric))
    def contrast(model,task,budget,name,metric):
        return next(c for c in contrasts if (c["model"],c["task"],c["regime"],c["contrast"],c["metric"])
                    ==(model,task,budget,name,metric))
    def interval(row,scale=1):
        value=row.get("mean_difference",row.get("mean"))
        if value is None:return "不适用"
        return f"{value*scale:.3f} [{row['bootstrap_95_low']*scale:.3f}, {row['bootstrap_95_high']*scale:.3f}]"
    cells=sorted({(g["model"],g["task"],g["regime"]) for g in groups})
    full=[contrast(*cell,"relational_qp_rr_minus_niche","validation_selected_test_loss") for cell in cells]
    full_better=sum(c["mean_difference"]<0 for c in full)
    full_low=sum(c["bootstrap_95_high"]<0 for c in full)
    full_high=sum(c["bootstrap_95_low"]>0 for c in full)
    totals=Counter()
    for r in mechanisms["runs"]: totals.update(r["counts"])
    api_errors=[(j["job_id"],r["llm_errors"]) for j,r in zip(manifest["jobs"],results) if r["llm_errors"]]
    invalid_types=Counter(n["evaluation"].get("failure_type") for r in results for n in r["nodes"]
                          if n["source"]=="live_llm" and not n["evaluation"]["valid"])
    known_calls=sum(s["model_calls"] for s in summaries)
    generated=sum(s["generated"] for s in summaries)
    valid=sum(s["valid_generated"] for s in summaries)
    token_total=sum(s["tokens_used"] for s in summaries)
    selectors=[r["selector"] for r in results if r["config"]["task"]=="tsp"
               and r.get("selector",{}).get("selection_seconds_per_test_instance") is not None]
    selector_cells=[g for g in groups if g["task"]=="tsp" and g["metric"]=="selector_gain_vs_single"]
    selector_positive=sum(g["mean"] is not None and g["mean"]>0 for g in selector_cells)
    fixed=[s for r,s in zip(results,summaries) if r["config"]["token_budget"] is not None and s["usage_complete"]]
    text=[
        "# 第六章 v1.1 技术报告：质量保护、重启修正与多算法用途",
        "",
        f"研究批次：`{manifest['study_id']}`。实验源码：`{manifest['source_commit']}`。本报告对应 200 次新模型搜索；此前 120 次追加验证只作历史动机，不并入本轮效果统计。",
        "",
        "## 1. 结论与证据等级",
        "",
        f"200/200 次预定运行均已有结果，完整回放核验 {verification['checked_runs']} 次通过。本轮使用真实 Qwen3.7-Plus 和 MiniMax-M3 API，生成 {generated:,} 个候选，其中 {valid:,} 个有效。累计记录 {known_calls:,} 次有 usage 的调用和 {token_total:,} 个输入加输出 tokens。",
        "",
        f"完整修订版 11 相对 niche 的测试损失均值在 {full_better}/8 个“模型×任务×预算”单元更低；配对 bootstrap 区间完全低于零的单元为 {full_low}/8，完全高于零的单元为 {full_high}/8。这里没有预注册通过门槛或多重比较校正，不能把区间不跨零称作确认性显著。五个区块的机制筛查只支持限定条件下的方向判断，不能单独确立稳定优势或博士创新。",
        "",
        f"目标问题确有真实依据：旧日志中 49 次未获信用的合格父代改进已重新计数，TSP 相同行为探针但验证质量改善的反例也已回放。本轮发现 {totals['local_only_collisions']} 次局部改善碰撞；其中 {totals['local_only_retained_in_search_archive']} 次在更新后仍位于搜索档案 A，{totals['local_only_later_used_as_parent']} 次后来被用作父代。因而，识别进步、给调度信用、保留可开发的程序、兑现测试收益是四个需要分开检验的环节。",
        "",
        f"对旧 49 次事件还需纠正一项解释：其中 {historical['validation_redundancy_audit']['same_probe_and_validation_loss_vector']} 次已有规则的 probe 行为与验证逐实例损失完全相同；{historical['validation_redundancy_audit']['weakly_dominated_by_one_existing_validation_rule']} 次在验证损失向量上被某个已有规则弱支配。它们优于父代，却不一定增加了已有解集的用途。固定验证支配仍不能证明目标分布上的永久无用或不存在后续潜力，但不能把 49 次都称为被错误放弃的有效新分支。",
        "",
        "## 2. 本轮如何承接前五章",
        "",
        "第三章的机制综述与联合空间 DWD 指标提供质量和分布同时评价的原则；第四章 MSLS-MA 提供结构多解、模式内开发和重启的动机，RMC-CMSA 提供按实际起点—过程—终端关系决定后续资源的直接来源；第五章 HDADE 提供质量空间与行为空间分别维护的依据。第六章将对象提升为能处理多个实例的程序规则，继承问题思想，重新验证相似性、轨迹和预算的含义。",
        "",
        "本轮没有把 DWD 当作开放程序空间的真实模式召回；没有加入未经验证的高维投影；没有声称复现前章完整算法。外层轨迹是父程序→代码修改→子程序，内层轨迹是某程序构造路线或装箱的执行过程。W 仍保存内层证据，在四个关系控制器中保持相同，以便考察两个修正因素；它尚未获得独立贡献验证。",
        "",
        "## 3. 实际系统架构",
        "",
        "```mermaid",
        "flowchart TD",
        "  T[任务接口、共同种子规则、固定预算] --> S[外层控制器选择策略标签、父代和操作]",
        "  S --> P[Planner 提出评分规则假设]",
        "  P --> C[Coder 生成 priority 函数]",
        "  C --> E[有界 AST 解释器：validation 与 probe]",
        "  E --> Q[全局、父代、邻域进步与碰撞记录]",
        "  Q --> M[A 质量行为档案 / W 内层证据 / M 事件记忆]",
        "  M --> S",
        "  M --> F[预算结束，冻结共同门槛档案]",
        "  F --> V[TSP：验证拟合选择器；装箱：单规则]",
        "  V --> U[独立 test 评价及 oracle 上界审计]",
        "```",
        "",
        "程序语言为数值评分函数，允许标量运算与条件分支，最多 320 AST 节点；模型提示同时要求不超过 25 行，但解释器实际硬约束为字符数/AST/操作数，不能把行数要求当作已验证的执行保证。TSP 从城市 0 开始逐步构造路线，各方法统一执行 24 次确定性 2-opt delta 检查；装箱只在当前可行箱中评分，不能读取未来物品。",
        "",
        "TSP 行为距离为路线边集 Jaccard 距离在 probe 实例上的平均；装箱行为为前 24 个物品共箱关系的差异。阈值固定为 0.08，档案容量 10。它定义有限证据下的行为代表集，不是一般程序语义等价类或真实吸引域。",
        "",
        "### 3.1 两个实验因素",
        "",
        "对候选 p′ 分别记录 Δglobal = Lbest − L(p′)、Δparent = L(parent) − L(p′)、Δneighbor = L(neighbor) − L(p′)。父代/邻域进步必须大于 1e−4，候选还须处于当时最好验证损失 + 0.035 的范围；邻域只在 probe 距离≤0.08 时存在，最近邻平局按质量再按 ID 选择。",
        "",
        "QP 开启后，合格局部进步可以获得开发信用，且相应碰撞不扣调度分。每个已分配标签最多两次局部信用，只有全局进步或合格的新行为增量才重置额度。RR 开启后，饱和统计只计没有全局/合格局部进步的碰撞；最近两次候选中的有效局部信用可暂时阻止 no-growth 重启。全局停滞、标签饱和与尚未尝试是不同触发条件；RR 没有取消“新标签→restart”。",
        "",
        "重要实现边界：v1.1 仍按八类策略标签汇总信息，没有实现父模式×操作×意图×终端的完整条件转移模型；局部信用没有独立的父程序继续队列。修正改变了调度统计和提供给模型的 evidence 提示，因此实验估计的是整组修正的系统效果，不能独立归因于某个标量公式。",
        "",
        "### 3.2 搜索门槛与读出门槛",
        "",
        "内部 A 以当前最好验证质量为参照；最终读出统一改为最好共同手写种子的验证质量 + 0.035。独立 test 审计再用最好共同种子的 test 损失 + 0.035 过滤，并重新计数测试行为代表。test 门槛不进入搜索或选择器拟合。所有方法使用同一读出规则，避免某方法自身质量较差而获得更宽松的比较标准。",
        "",
        "## 4. 冻结实验设计和版本",
        "",
        "| 维度 | 冻结设置 |",
        "|---|---|",
        "| 模型 | alibaba-token-plan-cn/qwen3.7-plus；minimax-cn-coding-plan/MiniMax-M3 |",
        "| 任务 | 12 城市 TSP；64 个物品的在线一维装箱 |",
        "| 方法 | niche；00、10、01、11 四个关系控制器 |",
        "| 资源 | 固定 8 proposal slots；30,000 输入+输出 token 上限且最多 32 slots |",
        "| 区块 | 5 个独立实例区块，各方法、模型和预算使用相同区块；本地搜索 seed = block ID |",
        "| 数据 | 每区块 3 个分布族；probe 9、validation 24、test 36；与旧实验种子不重叠 |",
        "| 模型参数 | temperature = 0.7；planner/coder 输出上限分别 1800/2000 tokens |",
        "| 总量 | 2×2×5×2×5 = 200 次预定搜索 |",
        "| 并发 | 每提供方 1 条顺序队列；提供方之间并发 |",
        "",
        "模型 API 未设置可控的随机种子；区块配对控制数据和控制器随机数，不能保证不同提示共享生成噪声。五个区块同时改变实例与生成随机性；同一区块候选和 36 个测试实例不能当作独立算法重复。各预算内比较使用相应完整搜索，不能拿固定槽数结果作事后 token 截断来替代预算实验。",
        "",
        "初始准备提交 fb5bb6f 只有代码和干跑清单，未开展搜索。7b4f9d2 在搜索前修订了局部信用上限、TSP 选择器范围和恢复保护，并发布到公开 GitHub；真正的运行清单锁定该版本。后加的无 API 回放、机制审计、成本敏感性和图表工具与冻结核心分开版本化，不能回写核心或改变原始结果。",
        "",
        "## 5. 完成状态、失败和成本",
        "",
        "| 项目 | 实测 |",
        "|---|---:|",
        f"| 预定 / 保存结果 | 200 / {len(results)} |",
        f"| 已评价候选 / 有效 / 无效 | {generated:,} / {valid:,} / {generated-valid:,} |",
        f"| 有 usage 的模型调用 | {known_calls:,} |",
        f"| 已记录输入+输出 tokens | {token_total:,} |",
        f"| 用量不完整运行 | {sum(not s['usage_complete'] for s in summaries)} |",
        f"| 预留违规条数 | {sum(len(r['reservation_violations']) for r in results)} |",
        f"| 已付 planner、未能准入 coder 的尝试 | {sum(s['partial_attempts'] for s in summaries)} |",
        f"| 验证最优规则 test 执行失败 | {sum(not s['validation_selected_test_valid'] for s in summaries)} |",
        f"| 无 API 回放 validation / test 程序次数 | {verification['validation_programs_replayed']:,} / {verification['test_programs_replayed']:,} |",
        "",
        f"无效候选按实际槽位计入，类型汇总为 `{dict(invalid_types)}`。错误请求可能被服务端计费但没有返回 usage，因此 {token_total:,} tokens 是已记录成本总和，不能在有缺失用量时称为精确全成本。coding plan 的边际现金成本不从 token 数推定。基础设施排除详情见 `protocol_sensitivity.json`；主结果包含这些截断运行，成本完整配对另见 `sensitivity_complete_cost_pairs.csv`。",
        "",
        f"有完整 usage 的 token 上限运行平均实际使用 {statistics.fmean(s['tokens_used'] for s in fixed):,.1f} / 30,000 tokens。准入预留以 system/prompt UTF-8 字节数 + 512 余量 + 输出上限估算，通常保守，剩余预算不是零。相同上限并不等于相同实际消耗；本轮只能回答该准入实现下的预算鲁棒性，不能把差异全部归因于关系记忆质量。",
        "",
        "## 6. 主实验逐单元结果",
        "",
        "测试损失为百分比，TSP 相对于精确最优值，装箱相对于体积下界；后者不是相对于已知最优装箱数。每格为五个配对区块均值，完整区块值与区间在 `group_summary.csv`。生成数、成本、行为模式和质量需要联合阅读。",
        "",
        "| 模型 | 任务 | 预算 | 方法 | test 损失 % | test 模式数 | 生成候选数 | 已记录 tokens |",
        "|---|---|---|---|---:|---:|---:|---:|",
    ]
    for model,task,budget in cells:
        for method in METHODS:
            chosen=[r for r in results if (r["config"]["model"],r["config"]["task"],r["config"]["method"],
                    "tokens30000" if r["config"]["token_budget"] else "slots8")== (model,task,method,budget)]
            loss=group(model,task,budget,method,"validation_selected_test_loss")["mean"]
            modes=group(model,task,budget,method,"common_gate_test_behavior_modes")["mean"]
            text.append(f"| {model} | {task} | {budget} | {LABELS[method]} | {100*loss:.3f} | {modes:.2f} | {statistics.fmean(r['summary']['generated'] for r in chosen):.2f} | {statistics.fmean(r['summary']['tokens_used'] for r in chosen):,.0f} |")
    text.extend(["","![逐区块测试损失](paired_test_loss.png)","",
        "### 6.1 完整修订版 11 与 niche 的配对差","",
        "损失差 = 11 − niche，负值有利于 11；单位为百分点。模式差为 11 − niche，正值有利于 11。方括号是五区块配对 bootstrap 的描述性 95% 区间，不是校正后的确认性结论。","",
        "| 模型 | 任务 | 预算 | test 损失差 pp [95% 区间] | test 模式差 [95% 区间] |",
        "|---|---|---|---:|---:|"])
    for cell in cells:
        loss=contrast(*cell,"relational_qp_rr_minus_niche","validation_selected_test_loss")
        modes=contrast(*cell,"relational_qp_rr_minus_niche","common_gate_test_behavior_modes")
        text.append(f"| {cell[0]} | {cell[1]} | {cell[2]} | {interval(loss,100)} | {interval(modes)} |")
    text.extend(["","### 6.2 两因素主效应和交互","",
        "QP 主效应 = [(10−00)+(11−01)]/2；RR 主效应 = [(01−00)+(11−10)]/2；交互 = 11−10−01+00。先在每个区块内构造对比，再重采样整个区块。不能把生成候选当重复样本增加显著性。","",
        "| 模型 | 任务 | 预算 | QP 效应 pp [95% 区间] | RR 效应 pp [95% 区间] | 交互 pp [95% 区间] |",
        "|---|---|---|---:|---:|---:|"])
    for cell in cells:
        effects=[contrast(*cell,key,"validation_selected_test_loss") for key in
                 ("quality_protection_main_effect","restart_correction_main_effect","quality_by_restart_interaction")]
        text.append("| "+" | ".join([*cell,*(interval(e,100) for e in effects)])+" |")
    text.extend(["","![因子效应](factorial_effects.png)","",
        "## 7. 多算法集合是否有实际用途","",
        "TSP 用验证集的逐实例损失拟合标准化 Ridge 多输出预测器（alpha=10），输入只有城市几何特征；预测最低损失规则后才读取对应测试损失。每实例只需选择一个规则，不用测试标签寻找正确算法。装箱不开展这一实验，避免完整序列特征泄露未来输入。","",
        f"20 个 TSP“模型×预算×方法”单元中，学习选择器的均值收益为正的单元为 {selector_positive}/20。这个计数只是描述；需要结合每格五个区块和区间判断一致性。{('所有具备选择器结果的 TSP 运行中，选择特征与预测时间均值为 '+format(1000*statistics.fmean(s['selection_seconds_per_test_instance'] for s in selectors),'.3f')+' ms/实例。') if selectors else '本批次没有可用的 TSP 选择器结果。'}该时间是本机运行测量，不等同硬实时保证或跨机器效率排名。","",
        f"另对 {deployment['tsp_runs']} 次 TSP 运行回放冻结选择和单一规则：选择器特征/预测时间加被选算法执行时间的平均开销为 {deployment['mean_selector_ms_per_instance']:.3f} ms/实例，单规则为 {deployment['mean_single_ms_per_instance']:.3f} ms/实例；逐运行时间比均值为 {deployment['mean_end_to_end_time_ratio']:.3f}。逐实例选择损失回放一致。精确参考最优值预先计算，不计入部署时间；特征/预测时间来自原始 36 实例批处理平均，规则执行时间来自之后一次本机回放，两者相加为描述性成本估计，不等同同时测得的端到端时延保证。详见 `deployment_cost.csv`。","",
        "| 模型 | 预算 | 方法 | 单规则损失 % | 学习选择器损失 % | 选择收益 pp [95% 区间] | oracle 潜力 pp（不可部署） |",
        "|---|---|---|---:|---:|---:|---:|"])
    for model,task,budget in cells:
        if task!="tsp":continue
        for method in METHODS:
            chosen=[r["selector"] for r in results if (r["config"]["model"],r["config"]["task"],r["config"]["method"],
                    "tokens30000" if r["config"]["token_budget"] else "slots8")== (model,task,method,budget)]
            gain=group(model,task,budget,method,"selector_gain_vs_single")
            text.append(f"| {model} | {budget} | {LABELS[method]} | {100*statistics.fmean(s['validation_selected_single_test_loss'] for s in chosen):.3f} | {100*statistics.fmean(s['learned_selector_test_loss'] for s in chosen):.3f} | {interval(gain,100)} | {100*statistics.fmean(s['oracle_gain_upper_bound_only'] for s in chosen):.3f} |")
    text.extend(["","![选择器效用](selector_utility.png)","",
        "oracle 按每个测试实例事后取最好规则，因此使用了部署时不可得的答案，只表示集合潜力。可部署选择器和 oracle 之间的差距说明“发现互补算法”与“以低成本正确选择算法”是不同研究问题。","",
        "## 8. 机制诊断与当前缺陷","",
        f"新搜索中累计 {totals['restarts']} 次 restart，触发“尚未尝试标签”{totals['restart_trigger_untried']} 次、“连续无收益”{totals['restart_trigger_no_growth']} 次、“饱和”{totals['restart_trigger_saturation']} 次；原因可以重叠，不相加为互斥分解。目标 local-only 碰撞 {totals['local_only_collisions']} 次、信用资格 {totals['local_only_credit_eligible']} 次、额度耗尽 {totals['local_only_credit_exhausted']} 次。详见 `MECHANISM_AUDIT.md`。","",
        f"本轮 local-only 事件中，{totals['local_only_identical_to_prior_observed_rule']} 次与某已有程序的 probe 行为及验证逐实例损失完全相同，{totals['local_only_weakly_dominated_on_validation']} 次被某已有验证损失向量弱支配。这些事件不自动代表应继续保护的模式内潜力，也不能仅因计入 useful_gain 就认为产生了用途增量。","",
        "固定历史回放按相同已保存程序分别模拟 00/10/01/11，报告下一步父代、标签和操作是否变化；这种诊断没有重新生成模型输出，不是反事实性能实验。日志能够揭示机制是否触发和信息是否进入决策，无法证明改变提示后的最终收益。","",
        "| 缺陷 | 本轮能检验的内容 | 尚未解决的部分 |",
        "|---|---|---|",
        "| 局部信用与父程序生存分离 | 局部进步是否得信用、是否入 A、以后是否被使用 | 未建立小容量、有限期限的分支继续队列；模式内潜力可能仍被代表去重掩盖 |",
        "| 八标签调度粒度过粗 | untried、stalled、saturation 的触发频数 | 未估计真实父分支×操作的收益；短搜索中标签轮转可能先消耗预算 |",
        "| 关系上下文昂贵 | 同候选槽位与 token 上限分别比较，保留实际 token | 没有紧凑关系充分统计、tokenizer 原生计数或净收益/成本调度 |",
        "| 质量—行为关系仍有限 | 固定探针相似与质量进展并行记录 | 没有按需 witness；不能断言已辨认真实算法模式 |",
        "| 选择器与算法互补性分离 | TSP 验证拟合选择器及 oracle 潜力 | 固定小样本 Ridge 不是强选择器，不足以验证分布移位的实用性 |",
        "| 语言与任务小 | 12 城市 TSP/64 物品在线装箱的闭环可审计 | 无大规模任务、任意代码、学习管线、运行时竞争或外部作者版对照 |",
        "| API 不确定性 | 原始请求失败、完整成本和缺失成本分层 | 无 usage 的失败成本不可恢复，不应宣称精确预算保证 |",
        "| 机制主效应不能等同创新 | 两因素整组作用与结构性缺口 | MLEvolve/SeaEvo/AdaEvolve/FunSearch/GEPA 的相同信息强基线尚未复现 |",
        "",
        "## 9. 下一阶段技术路线和实验设计","",
        "以下是本轮诊断形成的后续设计，不是本轮已经实现或验证的结果。建议先处理父程序继续开发的执行链，再加入复杂关系图或 witness。","",
        "1. 定义外层分支为起始程序及其改进链。把输出档案 A 与可开发分支池 B 分开：A 维持质量/行为代表；B 只给质量合格、有实质父代增益且尚未被已有观测完全解释的子程序有限继续机会。比较父代进步、已有代表进步与真实互补性；对于重新找到已知规则的情形，不把它当作新用途收益。过期依据尝试次数和累计成本，不能靠重复比较较差父代无限续期。",
        "2. 将 RMC-CMSA 的关系记忆迁移到父分支→修改操作→子程序的外层，而不是把内层路径前缀当作全部关系。先用少量操作类别和质量/行为状态的共享统计，样本不足时回退，避免稀疏四维关系表。",
        "3. 冻结短提示与当前长提示的对照，以及等上下文长度的记忆打乱对照；调度成本分别记录 planner、coder、失败、评估和辨识。独立比较策略质量、上下文长度和预算准入。",
        "4. 先在可控程序邻域的机制集测试：相似且真进步、相似且无进步、父代进步但仍差于全局最好、新行为但低质量。输出继续概率、有效父代留存、开发深度、预算成本和最终持出质量；程序邻域的真值只用于诊断，不进入模型提示。",
        "5. 再做 live 2×2：分支继续池开/关 × 紧凑成本调度开/关，另加 niche、当前 11、同信息质量调度对照。至少固定候选槽位和真实 token 上限两种预算；开发与确认使用新的不重叠种子和更长搜索，实例族和模型响应重复分层。",
        "6. 在开发方差上预先规定最小有意义收益和功效目标，锁定一个主比较后确定确认区块数量，避免用五区块区间直接给出最终样本数。确认应至少覆盖 TSP 更大规模/新分布、一个异质算法发现任务和第二模型；不得把同一任务反复划分当作大量独立任务。",
        "7. 只有模式误判实际影响保留、替换或重启时才增补 witness；对照同执行成本扩大固定 probe，未影响决策的关系允许保持未决。需要在搜索收益上验证，不能只凭分类误判率下降宣布有效。",
        "8. 多算法用途将验证集选择的单规则、冻结实例选择器、oracle 上界分开。确认阶段计入选择器特征/预测、算法执行时间和内存；对在线任务另行设计只读已到达前缀的选择协议。",
        "",
        "推荐章节题目仍可收窄为《面向自动算法发现的质量保护与执行关系驱动多模态搜索方法》。章节应围绕“前章迁移与问题定义—v1 失效—v1.1 机制筛查—分支开发与紧凑关系方法—多算法用途—独立确认和失效条件”组织。当前可写成方法探索及证据链，最终新方法贡献须由后续相同信息强对照和独立实验支持。",
        "",
        "## 10. 文件索引、复现与公开版本管理",
        "",
        "- `manifest.json`：200 个工作单元、模型、数据种子/指纹、源码版本和依赖版本。",
        "- `run_level.csv`、`group_summary.csv`、`paired_contrasts.csv`：逐运行数据、均值/中位数/区间和因子对比。",
        "- `verification.json`：全部节点验证评价、全部 test 程序、控制器决策、档案与 usage 的回放。",
        "- `MECHANISM_AUDIT.md`、`mechanism_audit.json`：局部进步暴露、后续使用及固定历史决策诊断。",
        "- `protocol_sensitivity.json`：成本和失败敏感性；原始异常从未按结果好坏删除。",
        "- `historical_motivation.json`：旧 49 次父代进步与 TSP 反例的独立复核。",
        "- `qwen-raw-runs.zip`、`minimax-raw-runs.zip`：所有新运行的提示、响应、候选、checkpoint、结果、usage 和控制台记录。",
        "- `preregistered-source.zip`：冻结源码；`EVIDENCE_MANIFEST.json` 和 `SHA256SUMS.txt`：归档及逐原始文件哈希。",
        "",
        "公开发布采用实验分支、预注册标签、结果提交与 screening 结果标签分离；旧 d7fd074 资料保留。读取结果不需要 API 密钥。重新搜索依赖接收电脑自己的 OpenCode 模型凭据；确定性回放不调用模型。请把两个 raw ZIP 解压到同一数据目录，并把 `preregistered-source.zip` 解压到独立源码目录。源码指纹包含冻结文档，因此必须从该源码快照导入评估器：从仓库根目录将 `PYTHONPATH` 指向冻结源码目录，再运行 `python experiments/chapter6/v11/verify_screening.py <数据目录>`。统计和图表也可由相应模块重新生成。",
        "",
        f"实验清单哈希：`{manifest['manifest_sha256']}`。源码指纹：`{manifest['source_fingerprint_sha256']}`。",
    ])
    (analysis/"TECHNICAL_REPORT_ZH.md").write_text("\n".join(text)+"\n",encoding="utf-8")
    print(json.dumps({"report":str(analysis/"TECHNICAL_REPORT_ZH.md"),"full_vs_niche_mean_better_cells":full_better,
                      "full_vs_niche_ci_below_zero_cells":full_low,"full_vs_niche_ci_above_zero_cells":full_high,
                      "tsp_selector_positive_mean_cells":selector_positive},ensure_ascii=False))


if __name__=="__main__":
    main()
