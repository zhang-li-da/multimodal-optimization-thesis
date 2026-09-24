"""Analyze v1.2 mechanism screening without confirmatory inference."""
from __future__ import annotations

import argparse
import csv
from collections import Counter
import json
from pathlib import Path
import statistics

import matplotlib.pyplot as plt

METHODS=("niche","niche_fixed_dev","relational_branch")
LABELS={"niche":"Niche","niche_fixed_dev":"Niche + fixed development",
    "relational_branch":"Relation-guided branch"}


def _percentile(values,p):
    values=sorted(values)
    if len(values)==1: return values[0]
    position=(len(values)-1)*p
    low=int(position); high=min(len(values)-1,low+1)
    return values[low]+(position-low)*(values[high]-values[low])


def paired_interval(pairs):
    diffs_by_model={}
    for model,block,value in pairs:
        diffs_by_model.setdefault(model,{})[block]=value
    models=sorted(diffs_by_model)
    samples=[]
    def visit(index,means):
        if index==len(models):
            samples.append(statistics.fmean(means))
            return
        values=list(diffs_by_model[models[index]].values())
        for a in values:
            for b in values:
                for c in values:
                    visit(index+1,means+[statistics.fmean((a,b,c))])
    visit(0,[])
    return statistics.fmean(v for _,_,v in pairs),_percentile(samples,.025),_percentile(samples,.975)


def analyze(root:Path):
    manifest=json.loads((root/"manifest.json").read_text(encoding="utf-8"))
    verification=json.loads((root/"analysis"/"verification.json").read_text(encoding="utf-8"))
    if not verification["all_checked_pass"] or verification["checked_runs"]!=18:
        raise SystemExit("Full deterministic v1.2 replay is required before analysis.")
    results=[]
    for job in manifest["jobs"]:
        path=root/"runs"/job["job_id"]/"result.json"
        result=json.loads(path.read_text(encoding="utf-8"))
        summary=result["summary"]
        branches=[e for e in result["events"] if e.get("branch_parent_development")]
        classes=Counter(e.get("branch_classification") for e in result["events"]
            if e.get("branch_classification"))
        results.append({"job_id":job["job_id"],"model":job["model"],"controller":job["method"],
            "block":job["block"],"test_gap":summary["validation_selected_test_loss"],
            "validation_best_gap":summary["best_validation_loss"],"generated":summary["generated"],
            "valid_generated":summary["valid_generated"],"model_calls":summary["model_calls"],
            "tokens_used":summary["tokens_used"],"usage_complete":summary["usage_complete"],
            "request_errors":summary["request_errors"],"branch_admissions":summary["branch_admissions"],
            "branch_attempts":len(branches),"valid_branch_children":sum(e["valid"] for e in branches),
            "parent_improving_branch_children":sum(e["parent_improved"] for e in branches),
            "branch_max_depth":summary["branch_development_max_depth"],
            "branch_classifications":dict(classes),"modes":summary["common_gate_test_behavior_modes"],
            "initial_best_seed_test_gap":summary["shared_seed_best_test_loss"],
            "test_oracle_gain_upper_bound":summary["test_instance_oracle_gain_upper_bound_only"]})
    lookup={(row["model"],row["controller"],row["block"]):row for row in results}
    contrasts=[]
    for model in sorted({row["model"] for row in results}):
        for treated_method,control in (("niche_fixed_dev","niche"),
                                       ("relational_branch","niche"),
                                       ("relational_branch","niche_fixed_dev")):
            for block in range(3):
                treated=lookup[(model,treated_method,block)]
                base=lookup[(model,control,block)]
                contrasts.append({"model":model,"block":block,"contrast":"relational_branch_minus_"+control,
                    "treated":treated_method,
                    "test_gap_difference":treated["test_gap"]-base["test_gap"],
                    "branch_attempt_difference":treated["branch_attempts"]-base["branch_attempts"],
                    "token_difference":treated["tokens_used"]-base["tokens_used"]})
                contrasts[-1]["contrast"]=treated_method+"_minus_"+control
    relation_pairs=[(c["model"],c["block"],c["test_gap_difference"]) for c in contrasts
        if c["contrast"]=="relational_branch_minus_niche_fixed_dev"]
    niche_pairs=[(c["model"],c["block"],c["test_gap_difference"]) for c in contrasts
        if c["contrast"]=="relational_branch_minus_niche"]
    fixed_pairs=[(c["model"],c["block"],c["test_gap_difference"]) for c in contrasts
        if c["contrast"]=="niche_fixed_dev_minus_niche"]
    rel_stats=paired_interval(relation_pairs)
    niche_stats=paired_interval(niche_pairs)
    fixed_stats=paired_interval(fixed_pairs)
    candidates=Counter()
    for row in results: candidates.update(row["branch_classifications"])
    dev_rows=[row for row in results if row["controller"]!="niche"]
    opportunities=sum(row["branch_attempts"] for row in dev_rows)
    valid_by_model={model:sum(row["valid_branch_children"] for row in dev_rows if row["model"]==model)
        for model in sorted({row["model"] for row in dev_rows})}
    gates={"at_least_four_branch_followup_evaluations":opportunities>=4,
        "at_least_one_valid_child_per_model":all(count>=1 for count in valid_by_model.values()),
        "valid_child_counts_by_model":valid_by_model,"branch_followup_evaluations":opportunities,
        "passed":opportunities>=4 and all(count>=1 for count in valid_by_model.values())}
    analysis={"study_id":manifest["study_id"],"source_commit":manifest["source_commit"],
        "source_fingerprint":manifest["source_fingerprint_sha256"],"manifest_sha256":manifest["manifest_sha256"],
        "verification_pass":verification["all_checked_pass"],"run_count":len(results),
        "candidate_count":sum(row["generated"] for row in results),
        "valid_candidate_count":sum(row["valid_generated"] for row in results),
        "model_calls":sum(row["model_calls"] for row in results),
        "recorded_tokens":sum(row["tokens_used"] for row in results),
        "incomplete_usage_runs":sum(not row["usage_complete"] for row in results),
        "api_request_errors":sum(row["request_errors"] for row in results),
        "mechanism_gates":gates,"classification_counts":dict(candidates),
        "relational_minus_fixed_development_test_gap":{
            "mean":rel_stats[0],"descriptive_95_low":rel_stats[1],"descriptive_95_high":rel_stats[2],
            "lower_gap_runs":sum(d<0 for _,_,d in relation_pairs),"paired_model_block_n":len(relation_pairs)},
        "relational_minus_niche_test_gap":{
            "mean":niche_stats[0],"descriptive_95_low":niche_stats[1],"descriptive_95_high":niche_stats[2],
            "lower_gap_runs":sum(d<0 for _,_,d in niche_pairs),"paired_model_block_n":len(niche_pairs)},
        "fixed_development_minus_niche_test_gap":{
            "mean":fixed_stats[0],"descriptive_95_low":fixed_stats[1],"descriptive_95_high":fixed_stats[2],
            "lower_gap_runs":sum(d<0 for _,_,d in fixed_pairs),"paired_model_block_n":len(fixed_pairs)},
        "run_level":results,"paired_contrasts":contrasts,
        "interpretation":"Three blocks per model are a feasibility/mechanism screen. Intervals are descriptive and not confirmatory."}
    output=root/"analysis"
    output.mkdir(parents=True,exist_ok=True)
    (output/"analysis.json").write_text(json.dumps(analysis,ensure_ascii=False,indent=2),encoding="utf-8")
    fields=list(results[0])
    with (output/"run_level.csv").open("w",newline="",encoding="utf-8-sig") as stream:
        writer=csv.DictWriter(stream,fieldnames=fields,extrasaction="ignore"); writer.writeheader()
        for row in results: writer.writerow({**row,"branch_classifications":json.dumps(row["branch_classifications"],ensure_ascii=False)})
    with (output/"paired_contrasts.csv").open("w",newline="",encoding="utf-8-sig") as stream:
        writer=csv.DictWriter(stream,fieldnames=list(contrasts[0])); writer.writeheader();writer.writerows(contrasts)
    report=_report(analysis)
    (output/"TECHNICAL_REPORT_ZH.md").write_text(report,encoding="utf-8")
    _plot(results,output)
    return analysis




def _report(a):
    fixed = a["fixed_development_minus_niche_test_gap"]
    rel_fixed = a["relational_minus_fixed_development_test_gap"]
    rel_niche = a["relational_minus_niche_test_gap"]
    gates = a["mechanism_gates"]
    lines = [
        "# 第六章 v1.2：有限分支开发机制筛查报告",
        "",
        f"实验批次 `{a['study_id']}`；冻结源码提交 `{a['source_commit']}`；源码指纹 `{a['source_fingerprint']}`。本报告纳入 18 次 r2 搜索。因 niche 基线错误建立开发池，r1 批次已作废且不参与统计。",
        "",
        "## 结论",
        "",
        f"本轮完成 {a['run_count']} 次搜索、{a['candidate_count']} 次候选生成，其中 {a['valid_candidate_count']} 个候选通过执行；记录 {a['model_calls']} 次有 usage 的模型调用，共 {a['recorded_tokens']:,} tokens。API 请求错误 {a['api_request_errors']} 次，用量不完整的运行 {a['incomplete_usage_runs']} 次。",
        "",
        f"开发池产生 {gates['branch_followup_evaluations']} 次实际分支后续评价，按模型统计的有效子代数为 `{gates['valid_child_counts_by_model']}`；其中有效子代优于父代的次数为 {sum(row['parent_improving_branch_children'] for row in a['run_level'])}。预注册机制触发门槛{'通过' if gates['passed'] else '未通过'}。这证明分支有机会被实际开发，不证明测试质量提高。",
        "",
        f"关系引导分支相对强简单基线“niche + 固定开发”的平均测试 gap 差为 {100*rel_fixed['mean']:+.3f} 个百分点（关系臂更低为负），按模型分层重采样区块得到的描述性 95% 区间为 [{100*rel_fixed['descriptive_95_low']:+.3f}, {100*rel_fixed['descriptive_95_high']:+.3f}]；{rel_fixed['lower_gap_runs']}/{rel_fixed['paired_model_block_n']} 个配对中关系臂更低。当前数据不支持关系机制优于固定开发基线。",
        "",
        "本研究是每模型三个配对区块的机制可行性筛查，不是确认性性能实验；区间只作描述，不作显著性结论。",
        "",
        "## 研究问题与方法",
        "",
        "研究问题是：将局部改进转化为有预算上限的后续分支开发后，关系证据调度是否比相同规模的固定开发机会带来额外价值？输出档案 A 与开发池 B 分开维护。只有质量达标、相对父代有实质改进、且未精确重现已知 probe 行为和验证损失向量的候选，才有资格进入 B；已知规则重现不重置分支额度，低质量新行为与无收益重复不入池。",
        "",
        "比较三个控制器：`niche`、`niche_fixed_dev`（实际按当前可用集合的索引轮转，并非协议所写 FIFO）和 `relational_branch`（依据有限策略族证据与局部收益安排分支机会）。固定开发臂的普通步骤调用 niche，关系臂调用 relational；因此两臂没有隔离 B 内排序效应。关系路径还继承了 W 引用。每个控制器在两个模型端点上分别运行三个配对区块；每次 8 个 proposal slots。任务为 14 城市 TSP；probe、validation、test 各有 12、36、60 个互不重叠实例。提示使用共同 JSON schema 并隐藏组名。",
        "",
        "主指标为 validation 选出的单一程序在 test 上相对精确最优解的平均 gap。旧分析表中的 initial_best_seed_test_gap 是按 test 取最小的种子上界，不能作为 validation 选出的可部署种子规则；本次不改写旧数值。test oracle 同样只是集合潜力上界。",
        "",
        "## 逐运行结果",
        "",
        "| 模型 | 区块 | 控制器 | Test gap (%) | 种子 test 最小值上界 (%) | B 入池 | 后续提案 | 有效子代 | 优于父代 | 旧深度字段† | Tokens |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in sorted(a["run_level"], key=lambda x: (x["model"], x["block"], x["controller"])):
        lines.append(
            f"| {row['model']} | {row['block']} | {LABELS[row['controller']]} | {100*row['test_gap']:.3f} | {100*row['initial_best_seed_test_gap']:.3f} | {row['branch_admissions']} | {row['branch_attempts']} | {row['valid_branch_children']} | {row['parent_improving_branch_children']} | {row['branch_max_depth']} | {row['tokens_used']:,} |"
        )
    lines += [
        "",
        "## 配对比较",
        "",
        f"- 固定开发减 niche：平均差 {100*fixed['mean']:+.3f} 个百分点，描述性 95% 区间 [{100*fixed['descriptive_95_low']:+.3f}, {100*fixed['descriptive_95_high']:+.3f}]；{fixed['lower_gap_runs']}/{fixed['paired_model_block_n']} 个配对的固定开发 gap 更低。",
        f"- 关系分支减固定开发：平均差 {100*rel_fixed['mean']:+.3f} 个百分点，描述性 95% 区间 [{100*rel_fixed['descriptive_95_low']:+.3f}, {100*rel_fixed['descriptive_95_high']:+.3f}]；{rel_fixed['lower_gap_runs']}/{rel_fixed['paired_model_block_n']} 个配对的关系分支 gap 更低。",
        f"- 关系分支减 niche：平均差 {100*rel_niche['mean']:+.3f} 个百分点，描述性 95% 区间 [{100*rel_niche['descriptive_95_low']:+.3f}, {100*rel_niche['descriptive_95_high']:+.3f}]；{rel_niche['lower_gap_runs']}/{rel_niche['paired_model_block_n']} 个配对的关系分支 gap 更低。",
        "",
        "bootstrap 以模型分层、在模型内重采样三个配对区块；本轮只有两个模型和三个区块，区间精度有限。所有性能比较均为探索性，未进行确认性假设检验或多重比较校正。",
        "",
        "![各配对区块的测试质量](https://raw.githubusercontent.com/zhang-li-da/multimodal-optimization-thesis/chapter6-v1.2-screening-20260924/experiments/chapter6/v12/results/screening-20260924-r2/analysis/v12_outcomes.png)",
        "",
        "## 机制诊断",
        "",
        "分支机会、有效执行、父代改进和开发深度分别计数，避免把候选入池误当成实际开发成功。r2 中开发池后续提案确实执行，故识别、保留和继续开发这条执行链在原型里已被触发。有效子代不必然改进父代；父代改进也不必然改善全局档案或测试表现。",
        "",
        f"分支候选分类计数：`{json.dumps(a['classification_counts'], ensure_ascii=False, sort_keys=True)}`。精确重现判定基于固定 probe 行为与验证逐实例损失向量，不构成程序语义等价证明。",
        "",
        "当前关系控制器整体未优于固定开发。由于普通调度及 W 引用没有对齐，本轮不能独立识别关系分支排序的效果。†旧深度字段混入失败续开发的尝试层级，最大值为 4；按连续成功入池的改进链重算，最大成功深度为 3。旧 CSV/JSON 保留原字段，新版事后审计提供分开的尝试/成功深度，不能将旧字段称为成功深度。",
        "",
        "## 局限与下一步",
        "",
        "1. 每臂只有 6 次运行（两个模型×三个区块），随机性和区块敏感性仍高；需预注册更多独立区块后再作确认性评价。",
        "2. 仅使用 14 城市 TSP 与两个 API 模型/端点，外部效度有限；应增加任务类型、规模和独立模型。",
        "3. 关系调度在本轮没有超过固定机会基线，下一轮应先分析分支机会的选择质量与预算消耗，再考虑更复杂的关系图或 witness。",
        "4. 算法集合的跨实例用途尚未验证：缺少独立选择器训练集、初始规则集合选择器对照，以及计入选择成本的冻结部署测试。",
        "5. 本轮没有完成多模态算法发现的一般性或博士创新性证明；当前贡献仍是一个可检验机制假设和可复核的原型结果。",
        "",
        "## 复现与归档",
        "",
        "`manifest.json` 固定运行、任务拆分、源码指纹与配置；`analysis/verification.json` 记录确定性重放，`analysis/analysis.json` 和 CSV 保存统计结果。原始 prompts、responses、候选源码、usage、checkpoint 与日志按模型保存在本目录的归档 ZIP 中。复核方式见同目录 `README.md`。",
        "",
        "结果状态：r2 是唯一纳入的 v1.2 结果批次。r1 因 niche 组错误创建开发池而废弃；保留它仅为审计与版本管理，不合并统计。",
        "",
        "## 2026-09-24 事后复核与范围锁定",
        "",
        "本次修订没有新增模型调用。18 次搜索、144 个候选及其质量数值均来自原 r2。原协议的合并触发门槛保持原定义；新版按模型×控制器拆分诊断，不以事后门槛替换原门槛。关系臂共 25 次重启，其中 23 次指向未尝试标签，28 次参考对象来自 W 且不在 A 中；3/6 次运行无 B 入池，仅 4 个开发时隙有至少两个可选分支。",
        "",
        "v1.2.1 在独立模块中统一普通 niche 调度、关闭 W 决策、采用明确的 FIFO 对照并分开深度口径。它仅完成本地夹具和同历史决策探测，尚无新模型搜索结果。后续 2×2×4=16 次针对性搜索是独立协议草案，使用全新区块；本轮不启动，也不扩至 40 次。见仓库 experiments/chapter6/v12_1/ 下的报告和复核脚本。",
        "",
        "归档回放记录只说明原运行环境的复现。新增数值复核器要求路径、行为、选择和计数完全一致，仅对损失/路线长度等连续量允许明确的小误差，并逐项保存差异。另一操作系统的验证须单独运行，不能依据约 2.2e-16 的抽查误差宣称全部跨平台通过。",
    ]
    return "\n".join(lines) + "\n"


def _plot(results,output):
    models=sorted({row["model"] for row in results})
    fig,axes=plt.subplots(1,2,figsize=(12,4.6))
    colors={"niche":"#536779","niche_fixed_dev":"#2f7f83","relational_branch":"#c35a3d"}
    for mi,model in enumerate(models):
        ax=axes[mi]
        for method in METHODS:
            rows=sorted((row for row in results if row["model"]==model and row["controller"]==method),key=lambda row:row["block"])
            xs=[row["block"] for row in rows]
            ys=[100*row["test_gap"] for row in rows]
            ax.plot(xs,ys,marker="o",linewidth=1.7,color=colors[method],label=LABELS[method])
        ax.set_title(model)
        ax.set_xlabel("Paired block")
        ax.set_ylabel("Validation-selected test gap (%)")
        ax.set_xticks([0,1,2])
        ax.grid(alpha=.25)
    axes[1].legend(frameon=False,fontsize=8,loc="best")
    fig.suptitle("Quality outcome by paired block")
    fig.tight_layout()
    fig.savefig(output/"v12_outcomes.png",dpi=180,bbox_inches="tight")
    fig.savefig(output/"v12_outcomes.pdf",bbox_inches="tight")
    plt.close(fig)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root")
    args=parser.parse_args()
    result=analyze(Path(args.root))
    print(json.dumps({"runs":result["run_count"],"mechanism_gates":result["mechanism_gates"],
        "relation_minus_fixed_mean":result["relational_minus_fixed_development_test_gap"]["mean"]},ensure_ascii=False))


if __name__=="__main__": main()
