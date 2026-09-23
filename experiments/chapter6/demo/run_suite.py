"""Run a frozen factorial experiment; model calls remain inside each run."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import subprocess
import sys

from .discovery import METHODS, source_fingerprint


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tasks",nargs="+",choices=["tsp","binpack","classification"],default=["tsp","binpack"])
    p.add_argument("--methods",nargs="+",choices=METHODS,default=["quality","niche","terminal","relational"])
    p.add_argument("--seeds",nargs="+",type=int,default=[0,1,2,3,4])
    p.add_argument("--steps",type=int,default=12)
    p.add_argument("--workers",type=int,default=4)
    p.add_argument("--provider",default="alibaba-token-plan-cn")
    p.add_argument("--model",default="qwen3.7-plus")
    p.add_argument("--output",default="chapter6_demo/runs/pilot_qwen")
    args=p.parse_args()
    root=Path(args.output)
    root.mkdir(parents=True,exist_ok=True)
    jobs=[(t,m,s) for t in args.tasks for m in args.methods for s in args.seeds]
    random.Random(602023).shuffle(jobs)
    manifest={"created_utc":datetime.now(timezone.utc).isoformat(),"args":vars(args),
              "order":jobs,"source_fingerprint":source_fingerprint(),
              "budget":"Equal live candidate slots; two bounded calls per slot, invalid candidates count; actual input/output tokens and CPU costs retained.",
              "primary_question":"Does outcome memory reduce functionally repeated discovery under a quality gate compared with the same planner/coder and niching alone?",
              "factorial":{"quality":[0,0],"niche":[1,0],"terminal":[0,1],"relational":[1,1]},
              "factors":["niching parent selection","typed execution-memory allocation"],
              "analysis":"Per-task independent run replicates; no claim that nodes or instances are independent algorithm replicates; same final readout across methods."}
    manifest_path=root/"manifest.json"
    if manifest_path.exists():
        old=json.loads(manifest_path.read_text(encoding="utf-8"))
        if old["args"]!=manifest["args"] or old["source_fingerprint"]!=manifest["source_fingerprint"]:
            raise SystemExit("Manifest mismatch; use a new experiment directory.")
    else:
        manifest_path.write_text(json.dumps(manifest,indent=2),encoding="utf-8")

    def job(task,method,seed):
        out=root/f"{task}_{method}_s{seed}"
        out.mkdir(exist_ok=True)
        cmd=[sys.executable,"-m","chapter6_demo.discovery","--task",task,"--method",method,
             "--seed",str(seed),"--steps",str(args.steps),"--provider",args.provider,
             "--model",args.model,"--output",str(out)]
        with (out/"console.log").open("a",encoding="utf-8") as log:
            result=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT)
        return {"task":task,"method":method,"seed":seed,"exit_code":result.returncode,"output":str(out)}

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures=[pool.submit(job,*item) for item in jobs]
        results=[]
        for future in as_completed(futures):
            result=future.result()
            results.append(result)
            print(json.dumps({"completed":len(results),"total":len(jobs),**result}),flush=True)
    (root/"status.json").write_text(json.dumps(results,indent=2),encoding="utf-8")
    if any(r["exit_code"]!=0 for r in results):
        raise SystemExit(1)


if __name__=="__main__":
    main()
