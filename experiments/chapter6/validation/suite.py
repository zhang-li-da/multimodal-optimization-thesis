"""Freeze the protocol before launch, randomize jobs, retain every outcome."""
import argparse
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--output", default="chapter6_validation/runs/confirm_v1")
    args = parser.parse_args()
    root = Path(args.output)
    root.mkdir(parents=True, exist_ok=True)
    jobs = [(model, provider, task, method, partition)
            for model, provider in PROTOCOL["models"].items() for task in PROTOCOL["tasks"]
            for method in PROTOCOL["methods"] for partition in PROTOCOL["partitions"]]
    random.Random(9332206).shuffle(jobs)
    manifest = {"created_utc": datetime.now(timezone.utc).isoformat(), "protocol": PROTOCOL,
                "source_fingerprint": fingerprint(), "order": jobs, "workers": args.workers}
    path = root / "manifest.json"
    if path.exists():
        old = json.loads(path.read_text(encoding="utf-8"))
        if old["protocol"] != manifest["protocol"] or old["source_fingerprint"] != manifest["source_fingerprint"]:
            raise SystemExit("Frozen manifest mismatch; cannot change a running experiment.")
    else:
        _save(path, manifest)

    def job(model, provider, task, method, partition):
        out = root / f"{model}_{task}_{method}_p{partition}"
        out.mkdir(exist_ok=True)
        command = [sys.executable, "-m", "chapter6_validation.run", "--task", task,
                   "--method", method, "--partition", str(partition), "--provider", provider,
                   "--model", model, "--token-cap", str(PROTOCOL["token_cap"]),
                   "--max-steps", str(PROTOCOL["candidate_safety_cap"]), "--output", str(out)]
        with (out / "console.log").open("a", encoding="utf-8") as log:
            code = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT).returncode
        return {"model": model, "task": task, "method": method, "partition": partition,
                "exit_code": code, "output": str(out)}

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(job, *item) for item in jobs]
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            _save(root / "status.json", results)
            print(json.dumps({"completed": len(results), "total": len(jobs), **result}), flush=True)
    if any(r["exit_code"] != 0 for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
