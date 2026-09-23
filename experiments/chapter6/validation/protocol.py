"""Predeclared evaluation rules for the prospective replication."""
from chapter6_demo.discovery import QUALITY_TOLERANCE, BEHAVIOR_RADIUS, MAX_ARCHIVE
from chapter6_demo.benchmarks import behavior_distance

PROTOCOL = {
    "id": "independent-split-hard-budget-20260923-v1",
    "tasks": ["tsp", "binpack", "classification"],
    "models": {"qwen3.7-plus": "alibaba-token-plan-cn", "MiniMax-M3": "minimax-cn-coding-plan"},
    "methods": ["niche", "relational"],
    "partitions": list(range(11, 21)),
    "seed_rule": "search seed = 88000 + partition; one independent API search per method/model/block",
    "token_cap": 60000,
    "candidate_safety_cap": 60,
    "planner_output_cap": 1800,
    "coder_output_cap": 2000,
    "primary_quality": "validation-selected best rule evaluated once on the final held-out split",
    "primary_coverage": "up to 10 probe-separated rules under a common seed-anchored validation gate; retain only rules meeting the corresponding test anchor gate, deduplicate on test behavior",
    "quality_tolerance": QUALITY_TOLERANCE,
    "loss_noninferiority_margin": {"tsp": .005, "binpack": .01, "classification": .005},
    "mode_noninferiority_margin": .5,
    "multiplicity": "Holm family of 24 one-sided paired sign-flip tests: 6 task/model cells times loss superiority, mode superiority, loss noninferiority, mode noninferiority. Exact enumeration is conditional on sign-exchangeability of block differences.",
    "interval": "95% paired-block t intervals; report raw effects, wins/ties/losses and usage alongside p values",
    "cell_gate": "Holm-adjusted p < .05 for both loss superiority and mode noninferiority, OR for both mode superiority and loss noninferiority",
    "broad_stability_gate": "at least 4/6 cells pass, covering both models and at least 2 tasks, with no cell showing a loss CI entirely above its noninferiority margin",
    "scope": "Small tasks and the fixed language/feature interface only. Repeated classification splits overlap in samples; inference is conditional on these datasets, not new-dataset generalization.",
    "stopping": "Finish every predeclared run regardless of effect direction. Budget guard per call. Retain failed and incomplete iterations in usage. API errors without usage invalidate strict cost comparisons; do not silently rerun.",
    "external_comparator_gate": "Only after the frozen method passes the broad gate, invest in full closest-framework experiments; failure against niche already blocks a broad superiority claim.",
}


def fixed_archive(nodes, cutoff, capacity=MAX_ARCHIVE, radius=BEHAVIOR_RADIUS):
    eligible = sorted((n for n in nodes if n["evaluation"]["valid"] and n["evaluation"]["loss"] <= cutoff),
                      key=lambda n: (n["evaluation"]["loss"], n["evaluation"].get("ast_nodes", 0), n["id"]))
    kept = []
    for node in eligible:
        if not kept or min(behavior_distance(node["evaluation"]["behavior"], p["evaluation"]["behavior"]) for p in kept) > radius:
            kept.append(node)
        if len(kept) == capacity:
            break
    return kept
