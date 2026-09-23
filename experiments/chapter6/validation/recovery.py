"""Registered provider-level recovery, replacing the whole affected model batch."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import subprocess
import sys

from chapter6_demo.discovery import _save
from .protocol import PROTOCOL
from .run import fingerprint


def main():
    original = Path("chapter6_validation/runs/confirm_v1")
    recovered = Path("chapter6_validation/runs/confirm_minimax_recovery")
    recovered.mkdir(parents=True, exist_ok=True)
    amendment_path = Path("chapter6_validation/artifacts/infrastructure_amendment.json")
    if not amendment_path.exists():
        observed = []
        for p in original.glob("*/result.json"):
            r = json.loads(p.read_text(encoding="utf-8"))
            observed.append({"run": str(p.parent), "model": r["config"]["model"],
                             "cost_valid": r["summary"]["cost_valid"], "calls": r["summary"]["model_calls"],
                             "errors": r["errors"]})
        _save(amendment_path, {
            "registered_utc": datetime.now(timezone.utc).isoformat(),
            "reason": "MiniMax returned widespread HTTP 429 under shared 8-worker scheduling. No treatment-effect analysis inspected before this provider-level decision.",
            "rule": "Retain every first-batch MiniMax run as infrastructure evidence; replace ALL 60 MiniMax runs in a new folder, including any valid ones. Retain and finish all Qwen runs. No outcome-based run selection.",
            "provider_concurrency": {"qwen3.7-plus": 6, "MiniMax-M3": 2},
            "unchanged": "controller, prompts, data partitions, token cap, output caps, primary metrics, statistical family and pass thresholds",
            "source_fingerprint": fingerprint(), "initial_infrastructure_observations": observed,
            "limitations": "The amendment follows an infrastructure failure; this is not an untouched preregistration. Failed-batch usage is reported as overhead and is not merged into confirmatory runs.",
        })
    _save(recovered / "manifest.json", {"created_utc": datetime.now(timezone.utc).isoformat(),
               "protocol": PROTOCOL, "source_fingerprint": fingerprint(),
               "amendment": str(amendment_path), "whole_provider_recovery": "MiniMax-M3"})
    jobs = [(model, provider, task, method, partition)
            for model, provider in PROTOCOL["models"].items() for task in PROTOCOL["tasks"]
            for method in PROTOCOL["methods"] for partition in PROTOCOL["partitions"]]
    random.Random(9332206).shuffle(jobs)

    def job(model, provider, task, method, part):
        root = recovered if model == "MiniMax-M3" else original
        out = root / f"{model}_{task}_{method}_p{part}"
        out.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, "-m", "chapter6_validation.run", "--task", task,
                   "--method", method, "--partition", str(part), "--provider", provider,
                   "--model", model, "--token-cap", str(PROTOCOL["token_cap"]),
                   "--max-steps", str(PROTOCOL["candidate_safety_cap"]), "--output", str(out)]
        with (out / "console.log").open("a", encoding="utf-8") as log:
            code = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT).returncode
        return {"model": model, "task": task, "method": method, "partition": part,
                "exit_code": code, "output": str(out)}

    results = []
    with ThreadPoolExecutor(max_workers=6) as qwen, ThreadPoolExecutor(max_workers=2) as minimax:
        futures = [(minimax if item[0] == "MiniMax-M3" else qwen).submit(job, *item) for item in jobs]
        for future in as_completed(futures):
            r = future.result(); results.append(r)
            _save(recovered / "combined_status.json", results)
            print(json.dumps({"completed": len(results), "total": len(jobs), **r}), flush=True)
    if any(r["exit_code"] for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
