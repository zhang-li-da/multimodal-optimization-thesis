"""Descriptive failure analysis, explicitly separate from prospective efficacy tests."""
import argparse
from collections import Counter
import json
from pathlib import Path

from .verify import selected_paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="chapter6_validation/runs/confirm_v1")
    parser.add_argument("--recovery", default="chapter6_validation/runs/confirm_minimax_recovery")
    parser.add_argument("--output", default="chapter6_validation/results/mechanism_diagnostics.json")
    args = parser.parse_args()
    groups = {}
    for path in selected_paths(args.root, args.recovery):
        r = json.loads(path.read_text(encoding="utf-8"))
        c = r["config"]
        if not r["summary"]["cost_valid"]:
            continue
        key = (c["model"], c["task"], c["method"])
        group = groups.setdefault(key, {"runs": 0, "candidates": 0, "improvements": 0,
                                       "improvements_marked_repeated": 0, "repeated": 0,
                                       "different_intent_repeated": 0, "actions": Counter(),
                                       "tokens": 0, "planner_input_tokens": 0, "coder_input_tokens": 0,
                                       "output_tokens": 0})
        group["runs"] += 1
        group["tokens"] += r["summary"]["total_tokens"]
        for usage in r["usage"]:
            group[usage["stage"] + "_input_tokens"] += usage["input_tokens"]
            group["output_tokens"] += usage["output_tokens"]
        ids = {n["id"] for n in r["nodes"] if n["source"] == "live_llm"}
        events = [e for e in r["events"] if e["node_id"] in ids]
        for e in events:
            group["candidates"] += 1
            group["improvements"] += e["improved"]
            group["improvements_marked_repeated"] += bool(e["improved"] and e["terminal_collision"])
            group["repeated"] += e["terminal_collision"]
            group["different_intent_repeated"] += e["different_intent_collision"]
            group["actions"][e["action"]] += 1
    result = {"scope": "Post-hoc descriptive mechanism analysis of all cost-eligible runs; associations do not establish causal effects of restart or W.",
              "groups": [{"model": key[0], "task": key[1], "method": key[2], **value} for key, value in sorted(groups.items())]}
    output = Path(args.output); output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    lines = ["# 执行重复与开发收益的事后诊断", "",
             "以下只汇总成本可核验运行。它是描述性关联分析，不改变预定主指标，也不证明重启是质量变化的因果原因。", "",
             "| 模型 | 任务 | 方法 | 运行 | 严格质量改进 | 其中被判为重复 | 重启 / 候选 | 平均每候选 token |", "|---|---|---|---:|---:|---:|---:|---:|"]
    for group in result["groups"]:
        count = group["candidates"]
        lines.append(f"| {group['model']} | {group['task']} | {group['method']} | {group['runs']} | {group['improvements']} | {group['improvements_marked_repeated']} | {group['actions'].get('restart',0)} / {count} | {group['tokens']/max(1,count):.0f} |")
    lines += ["", "原控制器虽把质量进展计作 useful_gain，但调度另行对所有 collision 施加惩罚。相同候选可以同时触发进展与碰撞，说明这两个信号不应直接视为相反。实际改进幅度、同模式互补性与 probe 误并需要进一步分开判断。", "",
              "下一版必须通过质量保护消融、重启率对照和随机关系对照来检验机制解释。不能仅凭这些计数就宣布新版会改善性能。"]
    output.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"groups": len(result["groups"]), "runs": sum(g["runs"] for g in result["groups"])}))


if __name__ == "__main__":
    main()
