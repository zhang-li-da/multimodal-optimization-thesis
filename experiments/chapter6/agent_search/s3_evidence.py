"""Offline, post-freeze audit and supplemental readout for S3 r3.

Does not import or invoke any model transport. May run after terminal jobs;
--partial audits terminal jobs only and does not open test data.
"""
from __future__ import annotations

import argparse
import base64
import collections
from concurrent.futures import ProcessPoolExecutor
import json
import math
from pathlib import Path

from chapter6_demo.v12_2.common import digest, file_sha, read_json, save_json, utcnow
from chapter6_demo.v12_2.calls import DurableCalls
from .s3_tsp_r3.controller import restore_state, plain
from .s3_tsp_r3.evaluator import evaluate_search
from .s3_tsp_r3.study import verify, TERMINAL


def close(left, right, *, tolerance=1e-12):
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right
    if isinstance(left, (float, int)) and isinstance(right, (float, int)):
        return math.isclose(left, right, rel_tol=tolerance, abs_tol=tolerance)
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(close(a, b, tolerance=tolerance) for a, b in zip(left, right))
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(close(left[k], right[k], tolerance=tolerance) for k in left)
    return left == right


def audit_job(args):
    study, job, reexecute = args
    study = Path(study); run = study / "runs" / job["job_id"]
    checkpoint = read_json(run / "checkpoint.json")
    config = checkpoint["config"]
    state = restore_state(checkpoint)
    assert state.snapshot() == checkpoint["state"], "Final checkpoint differs from decision replay"
    snapshot = read_json(study / "data" / f"search-b{job['data_block']}.json")
    assert digest(snapshot) == config["data_search_sha256"]
    events = state.events
    replayed = len(events)
    keys = ("valid", "loss", "per_instance_loss", "per_instance_value", "behavior",
            "trajectory_behavior", "trajectory_values", "family_loss", "solutions",
            "failure_type", "program_identity", "features_called", "instance_evaluations")
    evaluated = 0
    for node in state.nodes:
        saved = node["evaluation"]
        assert saved["data_snapshot_sha256"] == digest(snapshot)
        assert saved["evaluated_instance_ids"] == [x["id"] for x in snapshot["validation"]]
        assert saved["probe_instance_ids"] == [x["id"] for x in snapshot["probe"]]
        if reexecute:
            fresh = evaluate_search(node["code"], snapshot)
            for key in keys:
                assert close(saved.get(key), fresh.get(key)), f"Evaluation mismatch: {job['job_id']} node {node['id']} field {key}"
            evaluated += 1
    for ledger in state.ledgers.values():
        assert 0 <= ledger["proposal_slots_scheduled"] <= ledger["grant_awarded"] <= job["maximum_direction_attempts"]
    assert len(state.pool) <= job["capacity"]
    for unit in state.adaptive_units:
        records = [r for r in checkpoint["records"] if r["decision"]["adaptive_unit_id"] == unit["unit_id"]]
        assert len(records) == unit["proposal_slots"] == job["adaptive_unit_steps"]
        assert not any(r["decision"]["allocation"]["protected"] for r in records)
        tokens = [r["costs"]["tokens_added"] for r in records]
        assert unit["tokens"] == (sum(tokens) if all(type(t) is int for t in tokens) else None)
        reward = sum(max(0, r["event"]["global_best_loss_before"] - r["event"]["global_best_loss_after"]) for r in records)
        assert close(unit["global_best_improvement"], reward)
    calls = DurableCalls(run, config, None).usage()
    assert calls["call_attempts"] <= job["request_limit"]
    assert calls["known_tokens"] <= job["token_budget"]
    models = collections.Counter()
    finish = collections.Counter()
    fingerprint_checks = 0
    for request_path in (run / "calls").glob("*/request.json"):
        folder = request_path.parent; request = read_json(request_path)
        assert request["run_config_sha256"] == digest(config)
        raw_path = folder / "raw_response.json"
        if raw_path.exists():
            raw = read_json(raw_path)
            assert raw["request_sha256"] == digest(request)
            assert raw["envelope_sha256"] == digest(raw["envelope"])
            body = json.loads(base64.b64decode(raw["envelope"]["body_base64"], validate=True))
            models[str(body.get("model"))] += 1
            choices = body.get("choices", [])
            finish[str(choices[0].get("finish_reason")) if choices else "missing"] += 1
            fingerprint_checks += 1
        if request["stage"] == "planner":
            evidence = json.loads(request["prompt"])["allocation_evidence"]
            assert set(evidence) == {"parent_available", "reference_available"}
    readout = read_json(run / "selection_frozen.json")
    valid = [n for n in state.nodes if n["evaluation"]["valid"]]
    assert readout["best_id"] == min(valid, key=lambda n: (n["evaluation"]["loss"], n["id"]))["id"]
    best_ancestry = set()
    current = readout["best_id"]
    while current is not None:
        assert current not in best_ancestry
        best_ancestry.add(current)
        current = state.by_id[current].get("parent_id")
    protected = [e for e in events if e["protected_development"]]
    lagging = [e for e in protected if e["protected_parent_was_behind_global"]]
    admission = [e for e in events if e["branch_entry_created"]]
    lagging_admission = [e for e in admission if e["loss"] > e["global_best_loss_before"] + state.gain_epsilon]
    attempts = collections.Counter(e["action"] for e in events)
    row = {
        "job_id": job["job_id"], "arm": job["arm_id"], "block": job["data_block"],
        "status": read_json(run / "status.json")["status"],
        "checkpoint_replay": "passed", "replayed_proposals": replayed,
        "program_evaluations_reexecuted": evaluated,
        "fingerprint_checks": fingerprint_checks, "returned_models": dict(models),
        "finish_reasons": dict(finish), "request_attempts": calls["call_attempts"],
        "known_tokens": calls["known_tokens"], "usage_complete": calls["usage_complete"],
        "valid_generated": sum(e["valid"] for e in events),
        "action_counts": dict(attempts), "branch_admissions": len(admission),
        "lagging_branch_admissions": len(lagging_admission),
        "parentless_explore_admissions": sum(e["action"] == "explore" for e in admission),
        "branch_slots": sum(e["branch_development"] for e in events),
        "ordinary_branch_slots": sum(e["branch_development"] and not e["protected_development"] for e in events),
        "protected_slots": len(protected), "protected_valid": sum(e["valid"] for e in protected),
        "lagging_protected_slots": len(lagging), "lagging_protected_valid": sum(e["valid"] for e in lagging),
        "lagging_protected_local_improvements": sum(e["local_improvement"] for e in lagging),
        "lagging_protected_global_improvements": sum(e["global_improvement"] for e in lagging),
        "best_has_lagging_protected_ancestor": any(e["node_id"] in best_ancestry for e in lagging),
        "unspent_protected_slots": sum(e["protection_remaining"] for e in state.pool.values()),
        "unspent_B_eligibility": sum(e["remaining"] for e in state.pool.values()),
        "adaptive_units": len(state.adaptive_units),
        "adaptive_probability_range": [min(d["evidence"]["p_develop"] for d in state.decisions),
                                       max(d["evidence"]["p_develop"] for d in state.decisions)] if state.decisions else None,
        "known_reproductions": sum(e["known_reproduction"] for e in events),
        "evaluation_wall_seconds": sum(n["evaluation"].get("wall_seconds", 0) for n in state.nodes),
        "evaluation_cpu_seconds": sum(n["evaluation"].get("cpu_seconds", 0) for n in state.nodes),
        "instance_evaluations": sum(n["evaluation"].get("instance_evaluations", 0) for n in state.nodes),
    }
    return row


def safe_audit_job(args):
    try:
        return {"audit_status": "passed", **audit_job(args)}
    except Exception as exc:
        return {"job_id": args[1]["job_id"], "arm": args[1]["arm_id"],
                "block": args[1]["data_block"], "audit_status": "failed",
                "error_type": type(exc).__name__, "error": str(exc)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--partial", action="store_true")
    parser.add_argument("--reexecute", action="store_true")
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    manifest = verify(args.study, frozen=True, roles=("search",))
    jobs, excluded = [], []
    for job in manifest["jobs"]:
        run = args.study / "runs" / job["job_id"]
        status = read_json(run / "status.json") if (run / "status.json").exists() else {}
        if (run / "search_result.json").exists() and status.get("status") in TERMINAL:
            jobs.append(job)
        else:
            excluded.append({"job_id": job["job_id"], "status": status.get("status", "active_or_not_started"),
                             "audit_status": "not_auditable_no_terminal_readout"})
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        rows = list(pool.map(safe_audit_job, [(str(args.study.resolve()), j, args.reexecute) for j in jobs]))
    passed = [r for r in rows if r["audit_status"] == "passed"]
    report = {"utc": utcnow(), "study_id": manifest["study_id"],
              "manifest_sha256": manifest["manifest_sha256"], "partial": args.partial,
              "planned_jobs": len(manifest["jobs"]), "audited_jobs": len(rows),
              "passed_jobs": len(passed), "failed_jobs": len(rows)-len(passed), "excluded": excluded,
              "test_data_opened_by_this_audit": False, "new_model_calls": 0,
              "numeric_comparison": "abs_tol=rel_tol=1e-12; exact routes, behaviors, identities and IDs",
              "replayed_proposals": sum(r["replayed_proposals"] for r in passed),
              "reexecuted_evaluations": sum(r["program_evaluations_reexecuted"] for r in passed),
              "rows": rows}
    save_json(args.output, report)
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}))
    if report["failed_jobs"] or (excluded and not args.partial):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
