"""Finite-domain executable counterexample to interpreting probe agreement as equivalence.

This is a synthetic identification experiment, not an LLM-search success test.
Programs are fixed before IID witness sampling. Every count/cost is reported.
"""
from itertools import product
import json
import math
from pathlib import Path

import numpy as np
from scipy.stats import beta

from chapter6_demo.programs import Program


def interval(successes, n, alpha):
    if n == 0:
        return 0.0, 1.0
    low = 0.0 if successes == 0 else float(beta.ppf(alpha / 2, successes, n - successes + 1))
    high = 1.0 if successes == n else float(beta.ppf(1 - alpha / 2, successes + 1, n - successes))
    return low, high


def program_pairs():
    values = np.arange(1, 257, dtype=float) / 257
    # The candidate chooses the largest score over a two-action instance.
    cases = [({"gap": float(x), "position": 0.0}, {"gap": .8, "position": 1.0}) for x in values]
    base = 'def priority(f):\n    return -f["gap"]\n'
    equivalent = 'def priority(f):\n    renamed = f["gap"]\n    return 2 - 3 * renamed\n'
    rows = [("equivalent", equivalent)]
    for k in (3, 13, 51):
        threshold = (k + .5) / 257
        code = f'def priority(f):\n    if f["gap"] < {threshold!r}:\n        return -2\n    return -f["gap"]\n'
        rows.append((f"rare_difference_{k}_of_256", code))

    def decisions(code):
        program = Program(code, "binpack")
        return np.array([max(range(2), key=lambda i: (program(case[i]), -i)) for case in cases])
    original = decisions(base)
    return [{"name": name, "code": code, "disagreement": (decisions(code) != original).astype(int),
             "reference_code": base} for name, code in rows]


def main():
    rng = np.random.default_rng(260923)
    pairs = program_pairs()
    sizes = [6, 24, 96, 384]
    looks = [24, 48, 96, 192, 384, 768, 1536]
    radius = .03
    reps = 400
    records = []
    for pair in pairs:
        true_distance = float(pair["disagreement"].mean())
        true_same = true_distance <= radius
        fixed = []
        for n in sizes:
            samples = rng.choice(pair["disagreement"], size=(reps, n), replace=True)
            estimate = samples.mean(axis=1)
            mistakes = (estimate <= radius) != true_same
            fixed.append({"probe_n": n, "wrong_decisions": int(mistakes.sum()), "replicates": reps,
                          "error_rate": float(mistakes.mean()), "program_calls_per_pair": n * 4})
        wrong = decided = 0
        observations = []
        bounds = []
        for _ in range(reps):
            samples = rng.choice(pair["disagreement"], size=max(looks), replace=True)
            decision = None
            used = max(looks)
            for index, n in enumerate(looks):
                # Bonferroni over this fixed finite schedule: all intervals for
                # this pair have joint coverage at least 95% under IID sampling.
                low, high = interval(int(samples[:n].sum()), n, .05 / len(looks))
                if high < radius:
                    decision, used = True, n
                    break
                if low > radius:
                    decision, used = False, n
                    break
            observations.append(used)
            if decision is not None:
                decided += 1
                wrong += int(decision != true_same)
        records.append({"pair": pair["name"], "true_distance_on_finite_domain": true_distance,
                        "true_same_at_radius": true_same, "fixed_probe": fixed,
                        "independent_witness": {"decided": decided, "undecided": reps - decided,
                          "wrong_decisions": wrong, "replicates": reps,
                          "mean_observations": float(np.mean(observations)),
                          "mean_program_calls": float(4 * np.mean(observations))}})
    report = {"scope": "Synthetic finite-domain identification only; no LLM calls, no algorithm-discovery efficacy claim. Programs are executed once on all 256 states and Monte Carlo sampling replays these exact outcomes. Reported per-pair program calls are equivalent evaluation work, not measured repeated interpreter calls.",
              "radius": radius, "domain_size": 256, "replicates": reps,
              "witness_looks": looks, "interval": "Clopper-Pearson with alpha=.05/7 per scheduled look, independent IID uniform sampling after pair is fixed",
              "programs": [{k: v for k, v in p.items() if k != "disagreement"} for p in pairs],
              "results": records}
    out = Path("chapter6_validation/results"); out.mkdir(parents=True, exist_ok=True)
    (out / "witness_mechanism.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    lines = ["# 有限探针的辨识机制检查", "",
             "这是有限域上可枚举真值的独立辨识实验，不是新 agent 有效性的证据。真实评分函数通过原 AST 解释器运行，程序对在采样前固定。", "",
             "256 个等概率状态、两个动作，行为距离为动作不一致比例，近似相似阈值 0.03。包括严格单调变换等价程序与仅在 3、13、51 个状态改变行为的程序。", "",
             "| 程序对 | 全域距离 | 固定 6 探针错误率 | 固定 24 探针错误率 | witness 错判 / 未决 | witness 平均实例数 |", "|---|---:|---:|---:|---:|---:|"]
    for r in records:
        w = r["independent_witness"]
        lines.append(f"| {r['pair']} | {r['true_distance_on_finite_domain']:.4f} | {r['fixed_probe'][0]['error_rate']:.1%} | {r['fixed_probe'][1]['error_rate']:.1%} | {w['wrong_decisions']} / {w['undecided']}（共 {reps}） | {w['mean_observations']:.1f} |")
    lines += ["", "固定探针方法按点估计与阈值比较；witness 方法使用七个预定样本量、Clopper–Pearson 区间并控制整个观察日程的误差，区间无法确认时保留未决。", "",
              "本脚本先用解释器枚举全部状态，再对确切结果做 Monte Carlo 采样。JSON 中程序调用数表示逐样本实际重执行时所需的等价工作量，不是本次重复解释器调用的实测计数。", "",
              "witness 需要明显更多样本，因此错误减少不意味着同成本更优。完整 JSON 还报告固定 96/384 探针；正式实验必须与相同成本的固定大探针比较。", "",
              "这个小域只有 256 个状态，直接枚举可以更便宜地得到精确关系；有放回采样实验仅用于检查有限观察与区间覆盖机制，不主张它优于该有限域的穷举。真实任务中 witness 是否划算要重新测量。", "",
              "该实验展示了有限 probe 误拆/误并的可能性与辨识成本，并未证明所提动态控制器有效，也未证明开放程序空间的真实模式覆盖。"]
    (out / "witness_mechanism.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"pairs": len(records), "replicates": reps, "results": records}))


if __name__ == "__main__":
    main()
