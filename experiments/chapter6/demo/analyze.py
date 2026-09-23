"""Summarize complete runs, task-wise uncertainty, costs and held-out results."""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import random
import statistics

from .benchmarks import behavior_distance, descriptor_hash
from .discovery import report_archive


def interval(values,seed=4096,reps=4000):
    if not values:
        return {"mean":None,"low":None,"high":None,"n":0}
    avg=statistics.fmean(values)
    if len(values)==1:
        return {"mean":avg,"low":None,"high":None,"n":1}
    rng=random.Random(seed)
    draws=sorted(statistics.fmean(rng.choices(values,k=len(values))) for _ in range(reps))
    return {"mean":avg,"low":draws[int(.025*reps)],"high":draws[int(.975*reps)],"n":len(values)}


def metrics(result):
    summary=result["summary"].copy()
    nodes={n["id"]:n for n in result["nodes"]}
    arch=[nodes[i] for i in result["archive_ids"]]
    test=result["test"]
    best_id=result["summary"]["validation_selected_best_id"]
    picked=test.get(str(best_id),{})
    valid_test=[test[str(n["id"])] for n in arch if test.get(str(n["id"]),{}).get("valid")]
    tolerance=result["config"]["quality_tolerance"]
    # How many validation-selected alternatives keep near-best quality on the
    # hidden instances? This is a post-search set diagnostic, never feedback.
    competitive_test=[r for r in valid_test if picked.get("loss") is not None and r["loss"]<=picked["loss"]+tolerance]
    separated=[]
    for r in sorted(competitive_test,key=lambda r:r["loss"]):
        if not separated or min(behavior_distance(r["behavior"],s["behavior"]) for s in separated)>result["config"]["behavior_radius"]:
            separated.append(r)
    distances=[behavior_distance(a["behavior"],b["behavior"])
               for i,a in enumerate(valid_test) for b in valid_test[i+1:]]
    summary.update(
        test_quality_constrained_modes=len(separated),
        test_pairwise_behavior_distance=statistics.fmean(distances) if distances else 0.0,
        test_evaluator_cpu_seconds=sum(r["cpu_seconds"] for r in test.values()),
        total_tokens=summary["input_tokens"]+summary["output_tokens"],
        source=result["config"]["source_fingerprint"],
        generated_exact_behavior_signatures=len({descriptor_hash(n["evaluation"]["behavior"]) for n in result["nodes"] if n["source"]=="live_llm" and n["evaluation"]["valid"]}),
    )
    summary["useful_per_10k_tokens"]=summary["useful_generated"]*10000/max(1,summary["total_tokens"])
    curve=[c["best_validation_loss"] for c in result["curve"] if c["best_validation_loss"] is not None]
    summary["mean_best_validation_loss_over_candidates"]=statistics.fmean(curve) if curve else None
    seeds=[n["evaluation"]["loss"] for n in result["nodes"] if n["source"]=="handwritten_seed" and n["evaluation"]["valid"]]
    summary["improvement_over_seed_validation"]=min(seeds)-summary["best_validation_loss"] if seeds else None
    return summary


def matched_token_prefixes(results):
    """Post-hoc replay diagnostic, explicitly separate from on-policy runs.

    Retain only completed planner+coder iterations whose cumulative realized
    token cost stays below the smallest total among the compared controllers.
    No unseen result is used to choose a prefix; no future candidate is moved
    into the prefix. This cannot recover new trajectories at other budgets.
    """
    groups={}
    for path,result in results:
        c=result["config"]
        groups.setdefault((c["model"],c["task"],c["seed"],c["steps"]),[]).append(result)
    output=[]
    for (model,task,seed,steps),runs in sorted(groups.items()):
        if len(runs)<2:
            continue
        budget=min(sum(u["input_tokens"]+u["output_tokens"] for u in r["usage"]) for r in runs)
        for r in runs:
            cumulative=0
            included=[]
            for iteration in range(steps):
                uses=[u for u in r["usage"] if u["iteration"]==iteration]
                if not uses:
                    continue
                amount=sum(u["input_tokens"]+u["output_tokens"] for u in uses)
                if cumulative+amount>budget:
                    break
                cumulative+=amount
                included.append(iteration)
            initial=[n for n in r["nodes"] if n["source"]=="handwritten_seed"]
            generated=[n for n in r["nodes"] if n["source"]=="live_llm"]
            prefix=initial+[generated[i] for i in included if i<len(generated)]
            archive=report_archive(prefix,task)
            ids={n["id"] for n in prefix if n["source"]=="live_llm"}
            events=[e for e in r["events"] if e["node_id"] in ids]
            output.append({"model":model,"task":task,"seed":seed,"method":r["config"]["method"],
                "steps":steps,"token_cap":budget,"used_tokens":cumulative,
                "completed_candidates":len(ids),"quality_constrained_modes":len(archive),
                "best_validation_loss":min((n["evaluation"]["loss"] for n in prefix if n["evaluation"]["valid"]),default=None),
                "terminal_collision_rate":sum(e["terminal_collision"] for e in events)/max(1,len(events)),
                "label":"posthoc_prefix_diagnostic_not_new_budget_controlled_experiment"})
    return output


METRICS=("best_validation_loss","validation_selected_test_loss","quality_constrained_modes",
         "test_quality_constrained_modes","terminal_collision_rate","useful_generated",
         "valid_fraction","total_tokens","feature_calls","local_checks","evaluator_cpu_seconds",
         "test_evaluator_cpu_seconds","mean_best_validation_loss_over_candidates","useful_per_10k_tokens",
         "improvement_over_seed_validation")


def summarize(roots,output):
    out=Path(output)
    out.mkdir(parents=True,exist_ok=True)
    results=[]
    incomplete=[]
    for directory in roots:
        root=Path(directory)
        for p in sorted(root.glob("*/result.json")):
            data=json.loads(p.read_text(encoding="utf-8"))
            results.append((p,data))
        incomplete.extend(str(p.parent) for p in root.glob("*/checkpoint.json") if not (p.parent/"result.json").exists())
    rows=[]
    groups={}
    for path,r in results:
        cfg=r["config"]
        row={"task":cfg["task"],"method":cfg["method"],"model":cfg["model"],"seed":cfg["seed"],
             "steps":cfg["steps"],"run":str(path.parent),**metrics(r)}
        rows.append(row)
        key=(row["model"],row["task"],row["method"],row["steps"])
        groups.setdefault(key,[]).append(row)
    aggregated=[]
    for key,group in sorted(groups.items()):
        aggregated.append({"model":key[0],"task":key[1],"method":key[2],"steps":key[3],
            "runs":len(group),"metrics":{m:interval([r[m] for r in group if r[m] is not None]) for m in METRICS}})
    comparisons=[]
    for model,task,steps in sorted({(r["model"],r["task"],r["steps"]) for r in rows}):
        by_method={m:{r["seed"]:r for r in rows if r["model"]==model and r["task"]==task and r["steps"]==steps and r["method"]==m}
                   for m in ("quality","niche","terminal","relational","relational_no_w")}
        for baseline in ("quality","niche","terminal","relational_no_w"):
            common=sorted(set(by_method[baseline])&set(by_method["relational"]))
            if not common:
                continue
            comparisons.append({"model":model,"task":task,"steps":steps,"comparison":"relational minus "+baseline,
                "runs":len(common),"paired_by":"replicate index; API sampling is not reproducible from a seed",
                "difference":{m:interval([by_method["relational"][s][m]-by_method[baseline][s][m] for s in common
                    if by_method["relational"][s][m] is not None and by_method[baseline][s][m] is not None]) for m in METRICS}})
    factorial=[]
    for model,task,steps in sorted({(r["model"],r["task"],r["steps"]) for r in rows}):
        lookup={(r["seed"],r["method"]):r for r in rows if r["model"]==model and r["task"]==task and r["steps"]==steps}
        common=[s for s in sorted({r["seed"] for r in rows}) if all((s,m) in lookup for m in ("quality","niche","terminal","relational"))]
        if not common:
            continue
        effects={}
        for metric in METRICS:
            mem=[];niche=[];interaction=[]
            for s in common:
                q,n,t,r=[lookup[(s,m)][metric] for m in ("quality","niche","terminal","relational")]
                if any(v is None for v in (q,n,t,r)):
                    continue
                mem.append(((t-q)+(r-n))/2)
                niche.append(((n-q)+(r-t))/2)
                interaction.append(r-n-t+q)
            effects[metric]={"memory_main":interval(mem),"niching_main":interval(niche),"interaction":interval(interaction)}
        factorial.append({"model":model,"task":task,"steps":steps,"runs":len(common),"effects":effects})
    analysis={"runs":rows,"aggregated":aggregated,"comparisons":comparisons,"factorial":factorial,
              "token_matched_prefix_diagnostic":matched_token_prefixes(results),
              "incomplete":incomplete,
              "uncertainty":"Exploratory 95% percentile bootstrap over independent search runs on fixed data splits. Three to five runs give unstable intervals; these intervals do not quantify dataset or split uncertainty. Replicate-index pairing does not imply common API randomness. Three small task types do not establish broad generalization; no multiple-testing adjustment or candidate-level significance test.",
              "selection":"Every method uses identical quality-gated, behavior-deduplicated final readout. Test metrics are computed only for validation-selected archive programs.",
              "cost":"Equal candidate/call caps, not equal realized token or CPU costs. Actual costs are reported; statistical superiority at exactly equal monetary budget is not inferred."}
    (out/"analysis.json").write_text(json.dumps(analysis,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
    columns=["model","task","method","seed","steps"]+list(METRICS)+["run"]
    with (out/"runs.csv").open("w",encoding="utf-8-sig",newline="") as f:
        writer=csv.DictWriter(f,fieldnames=columns,extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)
    generate_text(analysis,out)
    generate_plots(analysis,out)
    print(json.dumps({"complete_runs":len(rows),"incomplete_runs":len(incomplete),"output":str(out)}))
    return analysis


def generate_text(a,out):
    lines=["# Agent demo 实测报告", "", "这里汇总真实模型生成、受限程序执行和独立测试实例上的结果。", "",
           "比较的是本 demo 内的控制器机制，不是 MLEvolve、SeaEvo 或 AdaEvolve 的完整复现。", "",
           "候选数量和单次调用输出上限相同；输入 token 和运行时间不同，完整成本见 runs.csv。", "",
           "| 模型 | 任务 | 控制器 | 次数 | 最佳测试损失↓ | 验证模式数↑ | 测试模式数↑ | 重复比例↓ | 总 token |",
           "|---|---|---|---:|---:|---:|---:|---:|---:|"]
    def mean(g,k): return g["metrics"][k]["mean"]
    for g in a["aggregated"]:
        lines.append(f"| {g['model']} | {g['task']} | {g['method']} | {g['runs']} | {mean(g,'validation_selected_test_loss'):.4f} | {mean(g,'quality_constrained_modes'):.2f} | {mean(g,'test_quality_constrained_modes'):.2f} | {mean(g,'terminal_collision_rate'):.1%} | {mean(g,'total_tokens'):.0f} |")
    lines += ["", "TSP 损失为 Held–Karp 精确最优路线长度的相对 gap；装箱损失为箱数相对体积下界的 gap，该下界不一定可达到；classification 为 Iris/Wine/Breast Cancer 三个数据集错误率的平均。不同任务的损失不能直接横向平均。", "",
              "## 完整方法相对小生境基线的增量", "",
              "以下区间是逐任务、运行级 bootstrap 的探索性 95% 区间。区间包含 0 时不能宣称稳定增量；也没有进行跨指标多重检验控制。", ""]
    for c in a["comparisons"]:
        if c["comparison"]!="relational minus niche":
            continue
        lines.append(f"- {c['model']} / {c['task']}，{c['runs']} 次运行：")
        for metric,label in [("validation_selected_test_loss","测试损失差（负值有利）"),("quality_constrained_modes","验证模式数差（正值有利）"),("terminal_collision_rate","重复比例差（负值有利）")]:
            d=c["difference"][metric]
            ci=f"[{d['low']:.4f}, {d['high']:.4f}]" if d["low"] is not None else "样本不足"
            lines.append(f"  - {label}：{d['mean']:.4f}，区间 {ci}。")
    lines += ["", "## 同 token 上限的前缀诊断", "",
        "从每组已完成日志中取所有控制器实际总 token 的最小值作为共同上限，只保留累计 token 未超限的完整迭代。它是事后轨迹前缀分析，不是重新运行的等预算实验；没有使用后续候选补入前缀，也不用于声称改变预算后的搜索轨迹仍相同。", "",
        "| 模型 | 任务 | 控制器 | 保留候选均值 | 验证模式数均值 | 最佳验证损失 |",
        "|---|---|---|---:|---:|---:|"]
    prefix_groups={}
    for row in a["token_matched_prefix_diagnostic"]:
        prefix_groups.setdefault((row["model"],row["task"],row["method"]),[]).append(row)
    for key,rows in sorted(prefix_groups.items()):
        values=[statistics.fmean(r[m] for r in rows) for m in ("completed_candidates","quality_constrained_modes","best_validation_loss")]
        lines.append(f"| {key[0]} | {key[1]} | {key[2]} | {values[0]:.2f} | {values[1]:.2f} | {values[2]:.4f} |")
    lines += ["", "## 证据边界", "",
        "1. 模式由固定 probe 上的执行决策关系定义；它是可操作的行为分组，不是已经枚举的全局算法最优模式。",
        "2. 相同程序在固定实例上确定性执行；变量重命名和单调分数变换不会生成虚假模式。",
        "3. 生成空间是有界 Python 启发式评分函数；这是程序生成，尚未覆盖任意模型训练管线或完整通用 agent。",
        "4. 真实模型抽样即使固定本地 seed 仍不保证逐字重现；prompt、response、usage、程序和 checkpoint 已保存。",
        "5. 若只优于 quality 而未稳定优于 niche，证据支持多模态搜索原型可运行，不能支持关系记忆独立有效。",
        "6. 三天内的 demo 是可行性和初步机制证据，不能代替博士章节所需的最近邻复现、更多任务、严格成本控制和完整创新性论证。", ""]
    if a["incomplete"]:
        lines += [f"尚有 {len(a['incomplete'])} 个未完成运行，以上结果为中间快照。", ""]
    (out/"measured_report.md").write_text("\n".join(lines),encoding="utf-8")


def generate_plots(a,out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    order=["quality","niche","terminal","relational","relational_no_w"]
    models=sorted({g["model"] for g in a["aggregated"]})
    for model in models:
        tasks=[t for t in ("tsp","binpack","classification") if any(g["model"]==model and g["task"]==t for g in a["aggregated"])]
        fig,axes=plt.subplots(len(tasks),3,figsize=(13,3.5*len(tasks)),constrained_layout=True,squeeze=False)
        for row,task in enumerate(tasks):
            groups=[g for m in order for g in a["aggregated"] if g["model"]==model and g["task"]==task and g["method"]==m]
            for col,(metric,title) in enumerate([("validation_selected_test_loss","Hidden-test loss (lower better)"),("quality_constrained_modes","Quality-constrained probe modes"),("terminal_collision_rate","Repeated terminal behavior rate")]):
                ax=axes[row,col]
                for x,g in enumerate(groups):
                    d=g["metrics"][metric]
                    values=[r[metric] for r in a["runs"] if r["model"]==model and r["task"]==task and r["method"]==g["method"]]
                    ax.scatter([x+(.08*(i-(len(values)-1)/2)) for i in range(len(values))],values,alpha=.55,s=22)
                    if d["low"] is not None:
                        ax.errorbar(x,d["mean"],yerr=[[d["mean"]-d["low"]],[d["high"]-d["mean"]]],fmt="k_",capsize=5)
                ax.set_xticks(range(len(groups)),[g["method"] for g in groups],rotation=24,ha="right")
                ax.set_title(task.upper()+": "+title,fontsize=10)
                ax.grid(axis="y",alpha=.2)
        fig.suptitle(model+" | independent runs; bootstrap intervals are exploratory")
        safe=model.replace("/","_")
        fig.savefig(out/f"{safe}_pilot.png",dpi=180)
        fig.savefig(out/f"{safe}_pilot.pdf")
        plt.close(fig)


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("roots",nargs="+")
    p.add_argument("--output",default="chapter6_demo/results")
    args=p.parse_args()
    summarize(args.roots,args.output)
