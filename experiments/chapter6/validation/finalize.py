"""Create the self-contained handoff from finalized experiment evidence."""
import json
from pathlib import Path


def main():
    root = Path("chapter6_validation")
    a = json.loads((root / "results/analysis.json").read_text(encoding="utf-8"))
    if a["missing"]:
        raise RuntimeError("Do not finalize before all predeclared runs have result files.")
    totals = a["totals"]
    checks = [json.loads((root / f"results/verification_{model}.json").read_text(encoding="utf-8")) for model in ("qwen", "minimax")]
    if not all(c["available_runs_pass"] and c["complete"] for c in checks):
        raise RuntimeError("Every predeclared block must be present and all auditable runs must pass.")
    aggregate_audit = {"pass": True, "available_runs_pass": True,
                       "all_runs_cost_eligible": all(c["all_runs_cost_eligible"] for c in checks),
                       "complete": all(c["complete"] for c in checks),
                       "runs": sum(c["runs"] for c in checks),
                       "candidate_attempts": sum(c["candidate_attempts"] for c in checks),
                       "model_calls": sum(c["model_calls"] for c in checks),
                       "tokens": sum(c["tokens"] for c in checks),
                       "replay_evaluations": sum(c["unique_program_split_replays"] for c in checks),
                       "replay_count_definition": "distinct within each audit batch; some equivalent programs can recur across models or incremental batches, so not a global distinct-program count",
                       "infrastructure_ineligible": [e for c in checks for e in c["infrastructure_ineligible"]],
                       "failures": [], "audit_files": ["verification_qwen.json", "verification_minimax.json"],
                       "scope": "source/split/recorded token admission and selected-program replay; unknown API error usage is excluded from cost inference"}
    assert aggregate_audit["runs"] == totals["runs"]
    assert aggregate_audit["model_calls"] == totals["model_calls"]
    assert aggregate_audit["tokens"] == totals["tokens"]
    (root / "results/verification.json").write_text(json.dumps(aggregate_audit, indent=2), encoding="utf-8")
    eligible = {(g["model"], g["task"], g["method"]): g for g in a["eligible_pair_aggregates"]}
    usable_cells = sum(c["cost_valid"] and c["complete"] for c in a["comparisons"])
    if a["broad_stability_gate"]:
        outcome = "冻结原型通过了本轮预定稳定性门槛，但最近邻框架增量与博士创新仍未证实。"
    else:
        outcome = "原完整 A/W/M 方法的稳定优势仍未建立，当前不宜直接定为已验证有效的博士第六章新算法。"
    criterion_note = "两个模型的完整结果应以这里的全部配对表和预定检验为准。"
    if not a["broad_stability_gate"]:
        criterion_note = "Qwen 分类的上一轮局部改善没有在这 10 个新划分上重复；Qwen 装箱质量接近，完整方法保留的测试模式更少。这些完整单元本身已不支持广泛稳定收益，结论不只是因为接口中断。"
    lines = ["# 第六章追加验证：交付与研究决策", "", "**" + outcome + "**", "",
             "本轮已完成真实模型追加验证、最近邻与代码复核、机制反例、标准分类器参照，并形成技术路线图和完整实验设计。"
             "路线中的下一版机制标为待验证方案，不能继承原型实验作为其效果证明。", "",
             "入口：[交互证据页](index.html) · [实测报告](results/measured_report.md) · [创新性复核](NOVELTY_AUDIT.md) · "
             "[技术路线](TECHNICAL_ROUTE.md) · [完整实验设计](EXPERIMENT_DESIGN.md) · [运行说明](README.md)", "",
             "## 这次增加了什么证据", "",
             f"两种模型 × 三类任务 × 两个控制器 × 十个新划分，保存全部 **{totals['runs']} 次运行结果**。"
             f"其中 {totals['runs']-totals['cost_invalid_runs']} 次完成并有完整使用量，{totals['cost_invalid_runs']} 次因服务错误缺少部分使用量；"
             f"相应的 {6-usable_cells} 个模型/任务单元按事前规则不用于完整确认检验。", "",
             f"共有 **{totals['candidate_attempts']} 个已评价候选槽**（含无效代码），{totals['valid_candidates']} 个有效；"
             f"记录 **{totals['model_calls']} 次调用、{totals['tokens']:,} token**。因预算停止且已计费的仅规划末次迭代共"
             f" {totals['partial_budget_iterations_with_charge']} 个；有费用时均计入。实际开始的迭代与无 usage 错误请求另列于实测报告。", "",
             "每次真实输入+输出上限 60,000 token，按调用前的保守估计准入。控制器、提示与原型源码不变，新增独立划分和共同质量门槛。"
             "实际消耗及未用预算不同，因此结论限定为同预算上限，不称完全相同实际费用。", "",
             f"MiniMax 首批在高并发下大量限流，整个提供方批次的 {len(a['infrastructure_excluded'])} 个已有结果（含成功结果）均独立保留，"
             "随后降低并发按全批次重新运行。该变更在效果分析之前登记，未择优补齐。首批额外已记录 "
             f"{totals['infrastructure_prior_batch_recorded_tokens']:,} token；无 usage 的服务端消耗无法推知。", "",
             "## 稳定性结果", "",
             f"**预定广泛稳定性门槛：{'通过' if a['broad_stability_gate'] else '未通过'}。** "
             f"{usable_cells} 个完整可检验单元中 {a['passed_cells']} 个达到“质量优效且模式非劣，或模式优效且质量非劣”的校正门槛；"
             "包含基础设施中断的单元不计入通过。", "",
             "下表仅展示两方法均完成且费用可核验的同划分配对均值。9 对的单元仅作描述，不能替代预定 10 对确认检验。", "",
             "| 模型 / 任务 | 完整配对 | niche 测试损失 | relational 测试损失 | niche 测试模式 | relational 测试模式 |", "|---|---:|---:|---:|---:|---:|"]
    for model in a["protocol"]["models"]:
        for task in a["protocol"]["tasks"]:
            left, right = eligible[model, task, "niche"], eligible[model, task, "relational"]
            lm, rm = left["metrics"], right["metrics"]
            lines.append(f"| {model} / {task} | {left['n']} | {lm['validation_selected_test_loss']['mean']:.2%} | {rm['validation_selected_test_loss']['mean']:.2%} | {lm['anchored_test_modes']['mean']:.2f} | {rm['anchored_test_modes']['mean']:.2f} |")
    lines += ["", "TSP/装箱为 gap，分类为三数据集等权错误率，不能跨任务直接平均。模式为共同质量门槛下、固定行为度量的代表数。", "",
              criterion_note + "完整差值、区间、24 项 Holm 校正与胜平负见实测报告和 analysis.json。", "",
              "10 个配对块的检验能力有限，不能排除小幅或条件性收益；未通过也不证明方法等效或所有多模态方法无效。"
              "当前结论应是“原完整组合未获得足够支持”，而不是“多模态优化不适合 agent”。", "",
              "## 科学上更有价值的发现", "",
              "原 probe 会把有益开发标为重复：一个已重新执行的 TSP 例子中，加入 regret 项后 probe 行为距离仍为 0，"
              "验证 gap 却从 7.32% 降至 6.22%。旧 Qwen/TSP/niche 的 17 次质量改进中有 12 次同时被标为重复，"
              "而 relational 在 60 次生成中重启 47 次。计数是关联证据，下一版仍需消融才能证明因果。", "",
              "在可枚举的 256 状态机制检查中，稀有分歧程序可被 6 个随机 probe 大量误合并；独立 witness 区间可减少错判，"
              "但明显增加采样量。这个小域直接穷举还可能更便宜。因此不能把该检查包装成新 agent 的性能优势。", "",
              "常规分类器参照使用相同 10 个 fit/validation/test 划分：按验证选择的六种固定分类器，平均测试错误率约 3.41%。"
              "它不属于同语言、同训练成本的搜索控制器比较，但说明当前受限评分规则尚不能支撑先进 AutoML 的性能主张。", "",
              "机制记录：[重复与开发收益](results/mechanism_diagnostics.html)、[probe/test 关系诊断](results/descriptor_diagnostic.html)、"
              "[有限域辨识检查](results/witness_mechanism.html)、[分类器参照](results/classifier_context.html)。", "",
              "## 创新性判断与建议路线", "",
              "MLEvolve 已有跨分支引用、记忆和规划/编码分离；FunSearch 已有执行分数 signature 聚类；SeaEvo 已用验证成功向量检索互补策略；"
              "AdaEvolve 已做质量多样性档案与资源分配；GEPA 已有轨迹反思与跨谱系合并。补充测试还需对照程序综合/反例引导脉络，"
              "收益率分配需对照资源约束 bandit。当前组件组合不能据此宣布博士创新已成立。", "",
              "推荐把下一版收窄为：**质量保护下、具有辨识置信度的意图—操作—终端关系搜索**。其核心是保护有质量收益的同模式开发，"
              "对关系不确定的候选补充独立证据，只惩罚缺少质量与用途收益的重复；W 需证明额外作用后才使用。", "",
              "已经完成的技术路线包含程序与行为空间定义、三态关系、A/W/M 分工、条件转移图、成本决策、有限 witness 误差界与适用限制。"
              "完整实验设计包含受控真值基准、真实算法发现任务、最近邻来源要求、消融、数据隔离、统计功效和停止条件。"
              "这些是可执行的研究方案，尚未形成经验证有效的 v2 方法。", "",
              "路线图：[PDF](results/technical_route.pdf) · [SVG](results/technical_route.svg)。", "",
              "## 第六章是否可以继续", "",
              "可以继续作为研究方向，但应调整核心机制与主张，不能直接把原 demo 定稿为正式新算法章节。下一步先在开发任务上验证最小 A/M 的质量保护和关系辨识，"
              "在独立确认任务通过后再进入完整 MLEvolve/SeaEvo/AdaEvolve 比较。若简单基线或近期方法加同一描述符已取得相同结果，应进一步缩小贡献。", "",
              "本轮没有通过最近邻全框架复现来宣称优越；也没有为了追求正结果在看过新划分后修改原控制器。论文可使用这些结果作为问题动机、原型评估和失效分析，"
              "新方法的有效性需要新冻结版本和新独立数据证明。", "",
              "## 交付核验", "",
              f"新增协议/统计/机制测试 15 项通过；两模型共 **{aggregate_audit['replay_evaluations']} 次记录的程序—划分回放核验**通过，"
              "源指纹、数据划分、已记录调用准入、最终选择与程序执行均匹配。回放计数在各批次内去重，不能解释为全局不同算法数量。", "",
             f"程序回放和可计量日志审计：{aggregate_audit['replay_evaluations']} 个程序—划分评估通过。"
             f"另有 {len(aggregate_audit['infrastructure_ineligible'])} 个 API 错误区块，包含 {totals['unknown_usage_calls']} 次无 usage 请求；其远端消耗未知，"
             "因此总体不是“所有运行均可审计”，但预注册的区块完整且所有可审计程序回放通过。", "",
             "原始 API prompts/responses、usage、失败记录、程序、checkpoint 和随机顺序完整保留。"
              "[核验结果](results/verification.json) · [逐次 CSV](results/runs.csv) · [冻结协议](PREREGISTRATION.md) · [文献证据清单](artifacts/evidence_manifest.json)。"]
    (root / "DELIVERY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"runs": totals["runs"], "usable_cells": usable_cells, "passed_cells": a["passed_cells"],
                      "replay_evaluations": aggregate_audit["replay_evaluations"]}))


if __name__ == "__main__":
    main()
