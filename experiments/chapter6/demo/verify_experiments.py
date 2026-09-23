"""Audit saved results and replay every unique final program without an API."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics

from .benchmarks import evaluate, split_fingerprint


def verify(roots,output):
    failures=[];versions={};unique={};run_count=0;candidates=calls=tokens=0
    def check(condition,message):
        if not condition:
            failures.append(message)
    for root_name in roots:
        root=Path(root_name)
        manifest=json.loads((root/"manifest.json").read_text(encoding="utf-8"))
        expected=len(manifest["order"])
        files=sorted(root.glob("*/result.json"))
        check(len(files)==expected,f"{root.name}: expected {expected} completed runs, found {len(files)}")
        for file in files:
            r=json.loads(file.read_text(encoding="utf-8"));name=file.parent.name
            c=r["config"];s=r["summary"]
            run_count+=1;candidates+=s["generated"];calls+=s["model_calls"]
            tokens+=s["input_tokens"]+s["output_tokens"]
            check(c["source_fingerprint"]==manifest["source_fingerprint"],name+": source mismatch")
            versions.setdefault(root.name,set()).add(c["source_fingerprint"])
            check(s["generated"]==c["steps"],name+": candidate budget mismatch")
            check(len(r["nodes"])==c["steps"]+3,name+": initial/candidate count mismatch")
            check(s["model_calls"]==len(r["usage"]),name+": API call count mismatch")
            check(s["input_tokens"]==sum(u["input_tokens"] for u in r["usage"]),name+": input token mismatch")
            check(s["output_tokens"]==sum(u["output_tokens"] for u in r["usage"]),name+": output token mismatch")
            for split in ("probe","validation","test"):
                check(c["splits"][split]==split_fingerprint(c["task"],split),name+": split mismatch "+split)
            by_id={n["id"]:n for n in r["nodes"]}
            for node_id in r["archive_ids"]:
                node=by_id[node_id];ev=node["evaluation"]
                check(ev["valid"],name+": invalid archive candidate")
                check(ev["loss"]<=s["best_validation_loss"]+c["quality_tolerance"]+1e-12,name+": archive quality gate violated")
                key=(c["task"],node["code"],"validation")
                if key not in unique:
                    unique[key]=evaluate(node["code"],c["task"])
                replay=unique[key]
                check(replay["valid"] and abs(replay["loss"]-ev["loss"])<1e-12 and replay["behavior"]==ev["behavior"],name+f": validation replay #{node_id} mismatch")
                stored=r["test"].get(str(node_id))
                check(stored is not None,name+f": missing final test #{node_id}")
                if stored is not None:
                    key=(c["task"],node["code"],"test")
                    if key not in unique:
                        unique[key]=evaluate(node["code"],c["task"],split="test",with_probes=False)
                    replay=unique[key]
                    check(replay["valid"]==stored["valid"] and replay["loss"]==stored["loss"] and replay["behavior"]==stored["behavior"],name+f": test replay #{node_id} mismatch")
            # Test numbers must never occur as fields in prompt objects.
            for prompt_path in file.parent.glob("*_plan.json"):
                item=json.loads(prompt_path.read_text(encoding="utf-8"))
                check('"test_loss"' not in item["prompt"] and '"validation_selected_test_loss"' not in item["prompt"],name+": test feedback in prompt")
    result={"pass":not failures,"runs":run_count,"live_generated_candidates":candidates,
        "recorded_successful_model_calls":calls,"total_recorded_tokens":tokens,
        "unique_program_split_replays":len(unique),"source_versions":{k:sorted(v) for k,v in versions.items()},
        "failures":failures,
        "scope":"Saved-run arithmetic and budgets, fixed splits, quality gates, every unique final archive program replayed on validation+probe and held-out test. No LLM calls."}
    Path(output).parent.mkdir(parents=True,exist_ok=True)
    Path(output).write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result))
    if failures:
        raise SystemExit(1)


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("roots",nargs="+")
    p.add_argument("--output",default="chapter6_demo/results/verification.json")
    args=p.parse_args()
    verify(args.roots,args.output)
