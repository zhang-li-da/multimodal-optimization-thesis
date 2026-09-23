"""Build a standalone offline dashboard from actual saved experiment results."""
import argparse
import json
from pathlib import Path

from .benchmarks import instances
from .analyze import metrics


def build(roots,output):
    runs=[]
    for root in roots:
        for path in sorted(Path(root).glob("*/result.json")):
            raw=json.loads(path.read_text(encoding="utf-8"))
            nodes=[]
            for n in raw["nodes"]:
                ev=n["evaluation"]
                nodes.append({**{k:v for k,v in n.items() if k not in ("evaluation",)},
                    "evaluation":{k:v for k,v in ev.items() if k not in ("behavior","trajectory_behavior")}})
            runs.append({"name":path.parent.name,"config":raw["config"],"summary":metrics(raw),
                         "nodes":nodes,"events":raw["events"],"curve":raw["curve"],
                         "archive_ids":raw["archive_ids"],"working_ids":raw["working_ids"],
                         "test":{k:{a:b for a,b in v.items() if a not in ("behavior","trajectory_behavior")} for k,v in raw["test"].items()}})
    data={"runs":runs,"instances":{t:list(instances(t,"validation")) for t in ("tsp","binpack","classification")}}
    payload=json.dumps(data,ensure_ascii=False).replace("<","\\u003c").replace("&","\\u0026")
    template=Path(__file__).with_name("dashboard_template.html").read_text(encoding="utf-8")
    target=Path(output)
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(template.replace("__EXPERIMENT_DATA__",payload),encoding="utf-8")
    print(json.dumps({"dashboard":str(target),"runs":len(runs)}))


if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("roots",nargs="+")
    p.add_argument("--output",default="chapter6_demo/dashboard.html")
    args=p.parse_args()
    build(args.roots,args.output)
