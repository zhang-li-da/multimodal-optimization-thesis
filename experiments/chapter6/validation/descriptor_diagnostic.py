"""Summarize probe/test relation changes with a stated selection limitation."""
import argparse
import json
from pathlib import Path

from .analyze import descriptor_consistency
from .verify import selected_paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="chapter6_validation/runs/confirm_v1")
    parser.add_argument("--recovery", default="chapter6_validation/runs/confirm_minimax_recovery")
    parser.add_argument("--output", default="chapter6_validation/results/descriptor_diagnostic.json")
    args = parser.parse_args()
    groups = {}
    for path in selected_paths(args.root, args.recovery):
        r = json.loads(path.read_text(encoding="utf-8"))
        if not r["summary"]["cost_valid"]:
            continue
        key = (r["config"]["model"], r["config"]["task"])
        counts = descriptor_consistency(r)
        target = groups.setdefault(key, {k: 0 for k in counts})
        for name, value in counts.items():
            target[name] += value
    result = {"scope": "Post-hoc pooled pairs among validation-selected test-evaluated programs only. Pairs are dependent and selection-biased; no population-wide error-rate estimate or significance test.",
              "groups": [{"model": k[0], "task": k[1], **v} for k, v in sorted(groups.items())]}
    output = Path(args.output); output.parent.mkdir(exist_ok=True, parents=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    lines = ["# Probe 与测试行为关系的一致性", "",
             "该诊断只检查最终被选中、因此拥有 test 执行结果的程序对。没有对全部未选候选获取 test 反馈；不能把这里的比例当成开放程序空间的误判率。程序对相关，也不做逐对显著性检验。", "",
             "| 模型 | 任务 | 已检查程序对 | probe 相近而 test 分离 | probe 分离而 test 相近 |", "|---|---|---:|---:|---:|"]
    for row in result["groups"]:
        lines.append(f"| {row['model']} | {row['task']} | {row['pairs']} | {row['probe_same_test_different']} / {row['probe_same']} | {row['probe_different_test_same']} / {row['probe_different']} |")
    lines += ["", "TSP 的 probe 使用 10 城市而验证/test 为 12 城市，装箱的 probe 长度也更短；关系变化可同时包含有限采样与分布/规模变化，不能全部归因于纯抽样噪声。", "",
              "该诊断回答固定描述符是否在新实例保持，而不提供真等价标签。具有完整枚举真值的机制检查另见 witness_mechanism.md。正式方法应分别测试同分布辨识与分布移位稳健性。"]
    output.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"groups": len(groups)}))


if __name__ == "__main__":
    main()
