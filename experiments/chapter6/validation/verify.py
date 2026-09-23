"""Audit budgets and replay selected programs; reads no API credentials."""
import argparse
import json
from pathlib import Path
import hashlib

from chapter6_demo import discovery as old
from chapter6_demo.benchmarks import behavior_distance
from .benchmarks import Benchmark
from .budget import input_upper_bound
from .protocol import PROTOCOL, fixed_archive
from .run import fingerprint


def selected_paths(root, recovery):
    paths = []
    for path in Path(root).glob("*/result.json"):
        config = json.loads(path.read_text(encoding="utf-8"))["config"]
        if config["model"] != "MiniMax-M3" or not recovery:
            paths.append(path)
    if recovery:
        paths.extend(Path(recovery).glob("*/result.json"))
    return sorted(paths)


def verify(root, recovery, output, models=None, watch=False):
    failures, seen, replayed, infrastructure = [], set(), {}, []
    bench_cache = {}
    paths = selected_paths(root, recovery)
    models = models or list(PROTOCOL["models"])
    paths = [p for p in paths if json.loads(p.read_text(encoding="utf-8"))["config"]["model"] in models]
    ledger_path = Path(output).with_suffix(".ledger.json")
    ledger = {}
    if watch and ledger_path.exists():
        saved_ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        if saved_ledger.get("source_fingerprint") == fingerprint():
            ledger = saved_ledger.get("verified", {})
    total_nodes, total_calls, total_tokens = 0, 0, 0
    for path in paths:
        r = json.loads(path.read_text(encoding="utf-8"))
        config, summary = r["config"], r["summary"]
        prior_replays = len(replayed)
        result_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        try:
            key = (config["model"], config["task"], config["method"], config["partition"])
            assert key not in seen, "duplicate block"
            seen.add(key)
            assert config["source_fingerprint"] == fingerprint(), "changed source fingerprint"
            assert config["frozen_controller"] == old.source_fingerprint(), "changed controller"
            assert config["token_cap"] == PROTOCOL["token_cap"], "changed budget"
            bkey = (config["task"], config["partition"])
            if bkey not in bench_cache:
                bench_cache[bkey] = Benchmark(*bkey)
            benchmark = bench_cache[bkey]
            for split in ("probe", "validation", "test"):
                assert benchmark.fingerprint(split) == config["splits"][split], "split mismatch"
            generated = [n for n in r["nodes"] if n["source"] == "live_llm"]
            assert len(generated) == summary["generated"], "candidate count mismatch"
            assert sum(n["evaluation"]["valid"] for n in generated) == summary["valid_generated"]
            assert len(generated) <= config["max_steps"]
            assert len(r["usage"]) == summary["model_calls"]
            cumulative = 0
            for usage in r["usage"]:
                journal = path.parent / f"{usage['iteration']:03d}_{usage['stage']}.json"
                saved = json.loads(journal.read_text(encoding="utf-8"))
                assert saved["usage"] == usage, "usage journal mismatch"
                bound = input_upper_bound(saved["system"], saved["prompt"])
                assert bound == usage["input_admission_bound"]
                assert cumulative + bound + usage["requested_output_cap"] <= config["token_cap"], "admission over budget"
                assert 0 < usage["input_tokens"] <= bound
                assert 0 <= usage["output_tokens"] <= usage["requested_output_cap"]
                assert usage["usage_contract_valid"]
                cumulative += usage["input_tokens"] + usage["output_tokens"]
            assert cumulative == summary["total_tokens"] <= config["token_cap"]
            if not summary["cost_valid"]:
                infrastructure.append({"run": str(path.parent), "errors": r["errors"],
                                       "recorded_tokens": cumulative,
                                       "interpretation": "logged protocol exclusion; recorded calls still audited, unknown remote usage cannot be certified"})
            seed_nodes = [n for n in r["nodes"] if n["source"] == "handwritten_seed"]
            anchor = min(n["evaluation"]["loss"] for n in seed_nodes)
            assert anchor == summary["validation_anchor"]
            archive = fixed_archive(r["nodes"], anchor + config["quality_tolerance"])
            assert [n["id"] for n in archive] == r["archive_ids"], "common quality readout mismatch"
            best = min((n for n in r["nodes"] if n["evaluation"]["valid"]),
                       key=lambda n: (n["evaluation"]["loss"], n["id"]))
            assert best["id"] == summary["validation_selected_best_id"]
            assert r["test"][str(best["id"])]["loss"] == summary["validation_selected_test_loss"]
            nodes = {n["id"]: n for n in r["nodes"]}
            skip_replay = watch and ledger.get(str(path), {}).get("result_sha256") == result_digest
            for node_id, measured in ([] if skip_replay else r["test"].items()):
                node = nodes[int(node_id)]
                for split, probes, original in [("validation", True, node["evaluation"]), ("test", False, measured)]:
                    cache_key = bkey + (node["code"], split)
                    if cache_key not in replayed:
                        replayed[cache_key] = benchmark.evaluate(node["code"], split=split, with_probes=probes)
                    reproduced = replayed[cache_key]
                    for field in ("valid", "loss", "per_instance_loss", "behavior", "trajectory_behavior", "solutions"):
                        assert reproduced.get(field) == original.get(field), "program replay mismatch: " + field
            test_anchor = min(r["test"][str(n["id"])]["loss"] for n in seed_nodes)
            assert test_anchor == summary["test_anchor"]
            kept = []
            for node in sorted(archive, key=lambda n: (r["test"][str(n["id"])]["loss"] if r["test"][str(n["id"])]["valid"] else float("inf"), n["id"])):
                ev = r["test"][str(node["id"])]
                if ev["valid"] and ev["loss"] <= test_anchor + config["quality_tolerance"]:
                    if not kept or min(behavior_distance(ev["behavior"], r["test"][str(i)]["behavior"]) for i in kept) > config["behavior_radius"]:
                        kept.append(node["id"])
            assert kept == r["test_mode_ids"] and len(kept) == summary["anchored_test_modes"]
            total_nodes += len(generated); total_calls += len(r["usage"]); total_tokens += cumulative
            if watch and not skip_replay:
                ledger[str(path)] = {"result_sha256": result_digest,
                                     "unique_program_split_replays_added": len(replayed)-prior_replays}
                ledger_path.parent.mkdir(parents=True, exist_ok=True)
                ledger_path.write_text(json.dumps({"source_fingerprint": fingerprint(), "verified": ledger}, indent=2), encoding="utf-8")
        except (AssertionError, KeyError, ValueError) as exc:
            failures.append({"run": str(path.parent), "error": str(exc)})
        if len(seen) % 10 == 0:
            print(json.dumps({"checked": len(seen), "replayed": len(replayed), "failures": len(failures)}), flush=True)
    expected = {(model, task, method, part) for model in models for task in PROTOCOL["tasks"]
                for method in PROTOCOL["methods"] for part in PROTOCOL["partitions"]}
    if seen != expected and not watch:
        failures.append({"missing_runs": len(expected - seen), "unexpected_runs": len(seen - expected)})
    complete = seen == expected
    replay_count = sum(x["unique_program_split_replays_added"] for x in ledger.values()) if watch else len(replayed)
    report = {"pass": not failures and complete, "available_runs_pass": not failures,
              "complete": complete, "missing_runs": len(expected-seen),
              "models": models, "runs": len(paths), "candidate_attempts": total_nodes,
              "model_calls": total_calls, "tokens": total_tokens, "unique_program_split_replays": replay_count,
              "failures": failures, "infrastructure_ineligible": infrastructure,
              "all_runs_cost_eligible": not infrastructure,
              "scope": "source/split/recorded-budget journal audit and deterministic replay of every final selected program; infrastructure exclusions are explicit; no API calls"}
    target = Path(output); target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))
    if failures:
        raise SystemExit(1)
    return complete


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="chapter6_validation/runs/confirm_v1")
    parser.add_argument("--recovery", default="chapter6_validation/runs/confirm_minimax_recovery")
    parser.add_argument("--output", default="chapter6_validation/results/verification.json")
    parser.add_argument("--models", nargs="+", choices=list(PROTOCOL["models"]))
    parser.add_argument("--watch", action="store_true", help="Audit completed files once, record content hashes, and follow new results.")
    args = parser.parse_args()
    if args.watch:
        import time
        while not verify(args.root, args.recovery, args.output, args.models, True):
            time.sleep(20)
    else:
        verify(args.root, args.recovery, args.output, args.models)
