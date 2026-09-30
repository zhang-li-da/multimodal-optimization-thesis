"""Two mock-provider integration fixtures on old saved data, with no model calls."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from chapter6_demo.v12_1.audit_r2 import offline_only
from .cli import parameters
from .common import CONTROLLER, file_sha, read_json, save_json, source_record
from .data import DRAFT, HISTORICAL
from .fixtures import FakeTransport
from .runner import restore, run_search, state_snapshot
from .test_stage import evaluate_frozen


def smoke(output):
    output = Path(output)
    if output.exists():
        raise ValueError("Use a fresh fixture directory.")
    block = read_json(HISTORICAL)["blocks"]["0"]
    search = {"profile": "chapter6-v12-tsp14-v1", "block": 0,
              **{split: block[split]["instances"] for split in ("probe", "validation")}}
    test = {"profile": "chapter6-v12-tsp14-v1", "block": 0, "test": block["test"]["instances"]}
    rows = []
    with offline_only():
        for method in ("niche_fixed_dev", "relational_branch"):
            client = FakeTransport()
            job = {"job_id": "fixture-" + method, "provider": "fixture-no-api", "model": "fixture-no-api",
                   "controller": method, "controller_class": CONTROLLER,
                   "data_block": 0, "search_seed": 0, "task": "tsp", "steps": 8, "token_budget": None}
            folder = output / method
            result = run_search(job, search, folder, {"old_snapshot_sha256": file_sha(HISTORICAL)},
                                parameters(read_json(DRAFT)), client)
            cp = read_json(folder / "checkpoint.json")
            restored = restore(cp)
            assert state_snapshot(restored) == cp["controller_state"]
            evaluated = evaluate_frozen(folder, test, output / "tests" / f"{method}.json")
            rows.append({"controller": method, "controller_class": result["config"]["controller_class"],
                         "fixture_proposals": result["summary"]["completed_proposals"],
                         "fixture_provider_calls": len(client.requests),
                         "valid_generated": result["summary"]["valid_generated"],
                         "restored_state_equal": True, "test_after_readout": True,
                         "test_evaluations": len(evaluated["evaluations"]),
                         "readout_sha256": result["selection_frozen_sha256"], "model_calls": 0})
    report = {"purpose": "Engineering integration fixtures; not performance experiments",
              "new_model_calls": 0, "new_online_search_runs": 0, "fixture_runs": len(rows),
              "source": source_record(), "fixture_rows": rows,
              "data": "old r2 block 0 coordinates; new blocks are not used for fixture evaluation"}
    save_json(output / "summary.json", report, immutable=True)
    return {key: value for key, value in report.items() if key != "source"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(smoke(args.output)))


if __name__ == "__main__":
    main()
