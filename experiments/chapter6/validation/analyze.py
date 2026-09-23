"""Analyze every predeclared run, controlling the planned family of comparisons."""
from __future__ import annotations

import argparse
import csv
from itertools import product
import json
from pathlib import Path
import statistics

import numpy as np
from scipy.stats import t

from chapter6_demo.benchmarks import behavior_distance
from .protocol import PROTOCOL


def paired_interval(values):
    values = np.asarray(values, dtype=float)
    n = len(values)
    if n == 0:
        return {"mean": None, "low": None, "high": None, "n": 0}
    mean = float(values.mean())
    if n == 1:
        return {"mean": mean, "low": None, "high": None, "n": 1}
    half = float(t.ppf(.975, n - 1) * values.std(ddof=1) / np.sqrt(n))
    return {"mean": mean, "low": mean - half, "high": mean + half, "n": n}


def sign_flip(values, alternative):
    """Enumerate signs; exact under the stated block sign-exchangeability null."""
    values = np.asarray(values, dtype=float)
    if not len(values):
        return 1.0
    observed = float(values.mean())
    count = 0
    for signs in product((-1, 1), repeat=len(values)):
        changed = float(np.mean(values * signs))
        if ((alternative == "less" and changed <= observed + 1e-12) or
                (alternative == "greater" and changed >= observed - 1e-12)):
            count += 1
    return count / 2 ** len(values)


def holm(pvalues):
    ordered = sorted(range(len(pvalues)), key=lambda i: pvalues[i])
    adjusted = [None] * len(pvalues)
    previous = 0
    for rank, index in enumerate(ordered):
        value = min(1.0, max(previous, pvalues[index] * (len(pvalues) - rank)))
        adjusted[index] = value
        previous = value
    return adjusted


def descriptor_consistency(result):
    """Post-search diagnostic on test-evaluated pairs; not all-candidate recall."""
    nodes = {n["id"]: n for n in result["nodes"]}
    ids = [int(i) for i, e in result["test"].items() if e["valid"]]
    radius = result["config"]["behavior_radius"]
    counts = {"pairs": 0, "probe_same": 0, "probe_different": 0,
              "probe_same_test_different": 0, "probe_different_test_same": 0}
    for i, left in enumerate(ids):
        for right in ids[i + 1:]:
            counts["pairs"] += 1
            same_probe = behavior_distance(nodes[left]["evaluation"]["behavior"], nodes[right]["evaluation"]["behavior"]) <= radius
            same_test = behavior_distance(result["test"][str(left)]["behavior"], result["test"][str(right)]["behavior"]) <= radius
            counts["probe_same" if same_probe else "probe_different"] += 1
            counts["probe_same_test_different"] += int(same_probe and not same_test)
            counts["probe_different_test_same"] += int(not same_probe and same_test)
    return counts


def analyze(root, output, recovery_root=None):
    root, output = Path(root), Path(output)
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest["protocol"] != PROTOCOL:
        raise ValueError("Analysis protocol does not match the frozen experiment.")
    paths = sorted(root.glob("*/result.json"))
    excluded_infrastructure = []
    if recovery_root:
        recovery_root = Path(recovery_root)
        recovery_manifest = json.loads((recovery_root / "manifest.json").read_text(encoding="utf-8"))
        if recovery_manifest["protocol"] != PROTOCOL or recovery_manifest["source_fingerprint"] != manifest["source_fingerprint"]:
            raise ValueError("Infrastructure recovery changed scientific protocol.")
        provider_model = recovery_manifest["whole_provider_recovery"]
        retained = []
        for path in paths:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data["config"]["model"] == provider_model:
                excluded_infrastructure.append({"run": str(path.parent), "summary": data["summary"], "errors": data["errors"]})
            else:
                retained.append(path)
        paths = sorted(retained + list(recovery_root.glob("*/result.json")))
    results = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
    rows = [{"run": str(p.parent), **{k: r["config"][k] for k in ("model", "task", "method", "partition")},
             **r["summary"], "stop_reason": r["stop_reason"]} for p, r in zip(paths, results)]
    expected = {(model, task, method, part) for model in PROTOCOL["models"] for task in PROTOCOL["tasks"]
                for method in PROTOCOL["methods"] for part in PROTOCOL["partitions"]}
    lookup = {(r["model"], r["task"], r["method"], r["partition"]): r for r in rows}
    if len(lookup) != len(rows):
        raise ValueError("Duplicate run blocks.")
    missing = sorted(expected - set(lookup))
    extras = sorted(set(lookup) - expected)
    aggregates = []
    metrics = ["validation_selected_test_loss", "anchored_validation_modes", "anchored_test_modes",
               "terminal_collision_rate", "generated", "total_tokens", "budget_utilization",
               "evaluator_cpu_seconds", "elapsed_seconds"]
    for model in PROTOCOL["models"]:
        for task in PROTOCOL["tasks"]:
            for method in PROTOCOL["methods"]:
                group = [r for r in rows if (r["model"], r["task"], r["method"]) == (model, task, method)]
                aggregates.append({"model": model, "task": task, "method": method, "n": len(group),
                                   "metrics": {m: paired_interval([r[m] for r in group]) for m in metrics}})
    eligible_pair_aggregates = []
    comparisons, hypotheses = [], []
    for model in PROTOCOL["models"]:
        for task in PROTOCOL["tasks"]:
            parts = [p for p in PROTOCOL["partitions"] if (model, task, "niche", p) in lookup
                     and (model, task, "relational", p) in lookup]
            pairs = [(lookup[model, task, "niche", p], lookup[model, task, "relational", p]) for p in parts]
            eligible_pairs = [(a, b) for a, b in pairs if a["cost_valid"] and b["cost_valid"]]
            for method, idx in (("niche", 0), ("relational", 1)):
                members = [pair[idx] for pair in eligible_pairs]
                eligible_pair_aggregates.append({"model": model, "task": task, "method": method,
                     "n": len(members), "label": "descriptive matched complete pairs; not a replacement for the frozen full-cell test",
                     "metrics": {m: paired_interval([r[m] for r in members]) for m in metrics}})
            delta_loss = [b["validation_selected_test_loss"] - a["validation_selected_test_loss"] for a, b in pairs]
            delta_mode = [b["anchored_test_modes"] - a["anchored_test_modes"] for a, b in pairs]
            margin = PROTOCOL["loss_noninferiority_margin"][task]
            tests = {
                "loss_superiority": (delta_loss, "less"),
                "mode_superiority": (delta_mode, "greater"),
                "loss_noninferiority": ([v - margin for v in delta_loss], "less"),
                "mode_noninferiority": ([v + PROTOCOL["mode_noninferiority_margin"] for v in delta_mode], "greater"),
            }
            entry = {"model": model, "task": task, "n": len(parts), "partitions": parts,
                     "complete": len(parts) == len(PROTOCOL["partitions"]),
                     "cost_valid": all(a["cost_valid"] and b["cost_valid"] for a, b in pairs),
                     "loss_difference": paired_interval(delta_loss), "test_mode_difference": paired_interval(delta_mode),
                     "loss_win_tie_loss": [sum(v < -1e-12 for v in delta_loss), sum(abs(v) <= 1e-12 for v in delta_loss), sum(v > 1e-12 for v in delta_loss)],
                     "mode_win_tie_loss": [sum(v > 0 for v in delta_mode), sum(v == 0 for v in delta_mode), sum(v < 0 for v in delta_mode)],
                     "delta_loss": delta_loss, "delta_modes": delta_mode, "tests": {}}
            entry["complete_pair_diagnostic"] = {
                "n": len(eligible_pairs),
                "loss_difference": paired_interval([b["validation_selected_test_loss"]-a["validation_selected_test_loss"] for a,b in eligible_pairs]),
                "test_mode_difference": paired_interval([b["anchored_test_modes"]-a["anchored_test_modes"] for a,b in eligible_pairs]),
                "label": "descriptive cost-eligible pairs only; exclusion can bias effects, no confirmatory p value",
            }
            for name, (values, direction) in tests.items():
                # An interrupted/unknown-usage block cannot support superiority;
                # retain its result for accounting but mark the confirmatory cell
                # unavailable instead of treating an API failure as poor search.
                p = sign_flip(values, direction) if len(parts) == len(PROTOCOL["partitions"]) and entry["cost_valid"] else 1.0
                entry["tests"][name] = {"p_raw": p}
                hypotheses.append((entry, name, p))
            comparisons.append(entry)
    for (entry, name, raw), adjusted in zip(hypotheses, holm([x[2] for x in hypotheses])):
        entry["tests"][name]["p_holm"] = adjusted
    for entry in comparisons:
        tests = entry["tests"]
        favorable = ((tests["loss_superiority"]["p_holm"] < .05 and tests["mode_noninferiority"]["p_holm"] < .05) or
                     (tests["mode_superiority"]["p_holm"] < .05 and tests["loss_noninferiority"]["p_holm"] < .05))
        entry["gate_pass"] = bool(entry["complete"] and entry["cost_valid"] and favorable)
        entry["analysis_status"] = "eligible" if entry["complete"] and entry["cost_valid"] else "infrastructure_or_completion_ineligible"
        low = entry["loss_difference"]["low"]
        entry["harm_signal"] = bool(entry["cost_valid"] and low is not None and low > PROTOCOL["loss_noninferiority_margin"][entry["task"]])
    passed = [c for c in comparisons if c["gate_pass"]]
    broad = (not missing and not extras and len(passed) >= 4 and len({c["model"] for c in passed}) == 2
             and len({c["task"] for c in passed}) >= 2 and not any(c["harm_signal"] for c in comparisons))
    totals = {"runs": len(rows), "candidate_attempts": sum(r["generated"] for r in rows),
              "candidate_count_definition": "completed candidate evaluation slots, including invalid code; unfinished planner/coder iterations are separately counted",
              "valid_candidates": sum(r["valid_generated"] for r in rows),
              "model_calls": sum(r["model_calls"] for r in rows), "tokens": sum(r["total_tokens"] for r in rows),
              "cost_invalid_runs": sum(not r["cost_valid"] for r in rows),
              "min_budget_utilization": min((r["budget_utilization"] for r in rows), default=0),
              "max_budget_utilization": max((r["budget_utilization"] for r in rows), default=0)}
    totals["started_iterations"] = sum(len({u["iteration"] for u in r["usage"]} | {e["iteration"] for e in r["errors"]}) for r in results)
    totals["partial_budget_iterations_with_charge"] = sum(bool(r.get("partial_iteration") and r["partial_iteration"]["charged_tokens"] > 0) for r in results)
    totals["unknown_usage_calls"] = sum(len([e for e in r["errors"] if e["type"] == "ModelError"]) for r in results)
    totals["attempted_model_calls"] = totals["model_calls"] + totals["unknown_usage_calls"]
    totals["infrastructure_prior_batch_recorded_tokens"] = sum(r["summary"]["total_tokens"] for r in excluded_infrastructure)
    totals["infrastructure_prior_batch_recorded_calls"] = sum(r["summary"]["model_calls"] for r in excluded_infrastructure)
    descriptor = [{"model": r["config"]["model"], "task": r["config"]["task"],
                   "method": r["config"]["method"], "partition": r["config"]["partition"],
                   **descriptor_consistency(r)} for r in results]
    result = {"protocol": manifest["protocol"], "source_fingerprint": manifest["source_fingerprint"],
              "totals": totals, "missing": missing, "unexpected": extras,
              "aggregates": aggregates, "eligible_pair_aggregates": eligible_pair_aggregates,
              "comparisons": comparisons, "broad_stability_gate": bool(broad),
              "passed_cells": len(passed), "descriptor_diagnostics": descriptor,
              "infrastructure_excluded": excluded_infrastructure,
              "interpretation": "Pass/fail is conditional on this frozen small-task protocol. Failure to reject superiority is not proof of equivalence; no broad generalization or novelty is inferred from significance alone."}
    (output / "analysis.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    with (output / "runs.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else [])
        writer.writeheader(); writer.writerows(rows)
    render(result, output)
    plots(result, output)
    print(json.dumps({"totals": totals, "passed_cells": len(passed), "broad_stability_gate": bool(broad), "missing": len(missing)}))
    return result


def render(result, output):
    totals = result["totals"]
    verdict = "通过" if result["broad_stability_gate"] else "未通过"
    lines = ["# 完整原型的稳定性追加验证", "",
             f"**冻结协议的广泛稳定性门槛：{verdict}；6 个模型/任务单元中 {result['passed_cells']} 个通过。**", "",
             f"保存 {totals['runs']} 次运行的结果、{totals['candidate_attempts']} 个已评价候选槽（含无效代码），其中 {totals['valid_candidates']} 个有效；"
             f"记录 {totals['model_calls']} 次模型调用、{totals['tokens']:,} token。严格成本无效运行：{totals['cost_invalid_runs']}。", "",
             f"基础设施修订：MiniMax 首批发生大量限流，该提供方批次共 {len(result['infrastructure_excluded'])} 个已保存运行（含有效运行）均保留为附加记录，主分析采用降低并发后独立补跑的全批次；未按效果挑选运行。", "",
             f"共开始 {totals['started_iterations']} 个迭代；其中 {totals['partial_budget_iterations_with_charge']} 个末次迭代只完成已计费规划就因预算停止，另有 {totals['unknown_usage_calls']} 次服务错误缺少 usage。实际请求次数为 {totals['attempted_model_calls']}，有 usage 的调用数为 {totals['model_calls']}；已记录 token 不包括无法得知的服务端费用。首批被替代基础设施运行另消耗已记录 {totals['infrastructure_prior_batch_recorded_tokens']:,} token。", "",
             "每次总 token 上限 60,000；原控制器与提示保持冻结，使用 10 个新划分和 2 种模型。下表只展示两方法均完成且费用可核验的配对均值；少于 10 对时仅为描述性结果，原单元确认检验不可用。所有中断与费用仍在原始表中。", "",
             "| 模型 | 任务 | 方法 | 有效配对数 | 测试损失 ↓ | 共同门槛测试模式 ↑ | 重复率 ↓ | 候选数 | token |", "|---|---|---|---:|---:|---:|---:|---:|---:|"]
    for g in result["eligible_pair_aggregates"]:
        if not g["n"]:
            continue
        m = {k: v["mean"] for k, v in g["metrics"].items()}
        lines.append(f"| {g['model']} | {g['task']} | {g['method']} | {g['n']} | {m['validation_selected_test_loss']:.4f} | {m['anchored_test_modes']:.2f} | {m['terminal_collision_rate']:.1%} | {m['generated']:.1f} | {m['total_tokens']:.0f} |")
    lines += ["", "## 配对效应与预先规定的判定", "",
              "差值为 relational − niche。损失差为负有利，模式差为正有利。区间为配对划分差值的 95% t 区间；24 个预定检验统一 Holm 校正。", "",
              "| 模型 / 任务 | 损失差 [95% CI] | 模式差 [95% CI] | 质量优效校正 p | 模式优效校正 p | 单元通过 |", "|---|---:|---:|---:|---:|---|"]
    for c in result["comparisons"]:
        if not c["cost_valid"]:
            lines.append(f"| {c['model']} / {c['task']} | 基础设施中断，推断不可用 | 完整配对描述见 JSON | — | — | 不计通过 |")
            continue
        a, b = c["loss_difference"], c["test_mode_difference"]
        if a["low"] is None:
            continue
        lines.append(f"| {c['model']} / {c['task']} | {a['mean']:.4f} [{a['low']:.4f}, {a['high']:.4f}] | {b['mean']:.2f} [{b['low']:.2f}, {b['high']:.2f}] | {c['tests']['loss_superiority']['p_holm']:.4f} | {c['tests']['mode_superiority']['p_holm']:.4f} | {'是' if c['gate_pass'] else '否'} |")
    lines += ["", "完整判定还要求另一指标非劣，非劣检验与差值向量见 analysis.json；未显著不等于等效。若单元存在未知使用量的 API 中断，原始统计保留但该单元的推断标为不可用、p 置 1，不把基础设施故障认定为算法失败。", "",
              "## 解释范围", "",
              "原方法使用自身最好值加容差维护内部档案；本轮外部主指标统一使用共享初始规则的质量门槛，以免劣质方法因绝对门槛较宽而获得更多模式。内部搜索没有据此改变。", "",
              "预算是每次真实调用前的保守硬上限，可能留下不同余额；利用率及部分迭代费用全部报告。没有通过截取有利前缀构造结果。", "",
              "分类划分在固定数据集上重复，样本存在跨划分重叠。统计只说明这些数据和有限规则空间中的搜索行为，不能代替跨数据集、跨任务泛化。符号翻转检验依赖配对差值可交换的假设。", "",
              "模式是有限 probe/test 上的操作性行为分组。测试模式保留与输出质量均是必要诊断，不能解释为真实全局模式召回率。", "",
              "本轮主比较是原完整方法与简单小生境。它没有复现 MLEvolve、SeaEvo、AdaEvolve 的完整系统，不能产生对这些框架的排名。"]
    (output / "measured_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def plots(result, output):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    cs = [c for c in result["comparisons"] if c["n"] > 1]
    labels = [c["model"] + " / " + c["task"] + (" *" if not c["cost_valid"] else "") for c in cs]
    if not cs:
        return
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.4), layout="constrained")
    for ax, key, title in zip(axes, ["loss_difference", "test_mode_difference"],
                              ["Held-out loss difference (negative favors memory)", "Test mode difference (positive favors memory)"]):
        effects = [c[key] if c["cost_valid"] else c["complete_pair_diagnostic"][key] for c in cs]
        means = np.array([e["mean"] for e in effects])
        err = np.array([[e["mean"] - e["low"] for e in effects], [e["high"] - e["mean"] for e in effects]])
        ax.errorbar(means, np.arange(len(cs)), xerr=err, fmt="o", color="#176a62", capsize=4)
        ax.axvline(0, color="#9f5c44", linestyle="--", linewidth=1)
        ax.set_yticks(np.arange(len(cs)), labels if key == "loss_difference" else [])
        ax.invert_yaxis(); ax.set_title(title, fontsize=10); ax.grid(axis="x", alpha=.2)
    fig.suptitle("Frozen relational minus niche; * incomplete cell, eligible-pair description only")
    fig.savefig(output / "effects.png", dpi=180)
    fig.savefig(output / "effects.pdf")
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", default="chapter6_validation/runs/confirm_v1")
    parser.add_argument("--output", default="chapter6_validation/results")
    parser.add_argument("--recovery-root")
    args = parser.parse_args()
    analyze(args.root, args.output, args.recovery_root)
