"""Live planner/coder search with quality, niching and relational controllers."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
import time

from .benchmarks import (VERSION, TAGS, DESCRIPTIONS, SEEDS, FEATURES, instances,
    split_fingerprint, evaluate, behavior_distance, descriptor_hash)
from .providers import ModelClient, ModelError, parse_json
from .programs import ProgramError

SYSTEM = "You are an algorithm researcher writing small executable heuristics. Obey the provided bounded Python language. Return one valid JSON object only; no markdown. Assess feedback empirically and do not invent evaluation results."
GRAMMAR = "Allowed Python: exactly def priority(f), scalar local assignments, return, if/else or conditional expression, + - * / % ** (constant exponent <=4), comparisons, boolean operations, calls abs/min/max/sqrt/log/log1p/exp/tanh. NO imports, annotations, loops, lists, arrays, attributes, f.get(), helpers, mutation, I/O, random, or other calls. Access only listed features with f['feature']. Guard denominators with 1e-9 and log domains. <=25 source lines and <=320 AST nodes. Higher returned value is chosen."
METHODS = ("quality", "niche", "terminal", "relational", "relational_no_w")
QUALITY_TOLERANCE = {"tsp":0.035, "binpack":0.035, "classification":0.025}
BEHAVIOR_RADIUS = 0.08
MAX_ARCHIVE = 10
MAX_WORKING = 16
MAX_TERMINAL = 128


def _brief(node):
    e=node["evaluation"]
    return {"id":node["id"],"intent":node["intent"],"tags":node["tags"],
            "code":node["code"],"loss":e["loss"],"valid":e["valid"],
            "family_loss":e.get("family_loss",{}),"failure_type":e.get("failure_type"),
            "error":e.get("error"),"trajectory_values":e.get("trajectory_values",[])[:2]}


def report_archive(nodes, task, capacity=MAX_ARCHIVE):
    """Same final readout for EVERY method, isolated from its search policy."""
    valid=[n for n in nodes if n["evaluation"]["valid"]]
    if not valid:
        return []
    best=min(n["evaluation"]["loss"] for n in valid)
    eligible=sorted((n for n in valid if n["evaluation"]["loss"]<=best+QUALITY_TOLERANCE[task]),
                    key=lambda n:(n["evaluation"]["loss"],n["evaluation"].get("ast_nodes",0),n["id"]))
    kept=[]
    for node in eligible:
        if not kept or min(behavior_distance(node["evaluation"]["behavior"],k["evaluation"]["behavior"]) for k in kept) > BEHAVIOR_RADIUS:
            kept.append(node)
        if len(kept)==capacity:
            break
    return kept


class SearchState:
    def __init__(self, task, method, seed):
        self.task,self.method=task,method
        self.rng=random.Random(seed)
        self.nodes=[]
        self.A=[]
        self.W=[]
        self.M=[]
        self.events=[]
        self.curve=[]
        self.best=None
        self.no_growth=0

    def observe(self,node):
        ev=node["evaluation"]
        previous=[n for n in self.nodes if n["evaluation"]["valid"]]
        nearest=None
        distance=None
        if ev["valid"] and previous:
            nearest=min(previous,key=lambda n:behavior_distance(ev["behavior"],n["evaluation"]["behavior"]))
            distance=behavior_distance(ev["behavior"],nearest["evaluation"]["behavior"])
        collision=ev["valid"] and distance is not None and distance<=BEHAVIOR_RADIUS
        improved=ev["valid"] and (self.best is None or ev["loss"] < self.best-1e-9)
        competitive=ev["valid"] and (self.best is None or ev["loss"] <= self.best+QUALITY_TOLERANCE[self.task])
        gain=improved or (competitive and not collision)
        self.best=min(self.best,ev["loss"]) if ev["valid"] and self.best is not None else (ev["loss"] if ev["valid"] else self.best)
        self.no_growth=0 if gain else self.no_growth+1
        event={"node_id":node["id"],"tags":node["tags"],"allocated_tag":node.get("allocated_tag"),"parent_id":node["parent_id"],
               "reference_id":node["reference_id"],"action":node["action"],"valid":ev["valid"],
               "terminal_collision":bool(collision),"distance_to_previous":distance,
               "nearest_node":nearest["id"] if nearest else None,
               "different_intent_collision":bool(collision and set(node["tags"])!=set(nearest["tags"])),
               "improved":bool(improved),"useful_gain":bool(gain),
               "loss":ev["loss"],"failure_type":ev.get("failure_type"),
               "fidelity":"full-validation-plus-fixed-probes",
               "feature_calls":ev["features_called"],"local_checks":ev["local_checks"],
               "cpu_seconds":ev["cpu_seconds"],"wall_seconds":ev["wall_seconds"],
               "behavior_hash":descriptor_hash(ev["behavior"]) if ev["valid"] else None,
               "trajectory_hash":descriptor_hash(ev.get("trajectory_behavior",[])) if ev["valid"] else None}
        self.nodes.append(node)
        self.events.append(event)
        self.M.append(event)
        self.M=self.M[-MAX_TERMINAL:]
        self.A=report_archive(self.nodes,self.task)
        # W stores useful intermediate behavior or diverse subcompetitive
        # attempts. It does not mix invalid programs into output archive A.
        if ev["valid"]:
            wnovel=not self.W or min(behavior_distance(ev["trajectory_behavior"],w["evaluation"]["trajectory_behavior"]) for w in self.W)>BEHAVIOR_RADIUS
            if wnovel:
                self.W.append(node)
                self.W=self.W[-MAX_WORKING:]
        self.curve.append({"candidates":len(self.nodes),"best_validation_loss":self.best,
            "quality_constrained_modes":len(self.A),
            "terminal_collisions":sum(e["terminal_collision"] for e in self.events),
            "cumulative_feature_calls":sum(e["feature_calls"] for e in self.events)})

    def _simple_parent(self):
        valid=[n for n in self.nodes if n["evaluation"]["valid"]]
        return min(valid,key=lambda n:(n["evaluation"]["loss"],n["id"])) if valid else None

    def choose(self,step):
        tags=TAGS[self.task]
        parent=self._simple_parent()
        reference=None
        target=tags[step%len(tags)]
        action="restart" if step%5==4 else "refine"
        evidence={"controller":self.method,"step":step}
        if self.method=="niche":
            parent=self.rng.choice(self.A) if self.A else parent
            others=[n for n in self.A if parent and n["id"]!=parent["id"]]
            if others and step%3==2:
                reference=self.rng.choice(others)
                action="recombine"
        elif self.method in ("terminal", "relational", "relational_no_w"):
            scores={}
            history={}
            for tag in tags:
                records=[r for r in self.M if r.get("allocated_tag")==tag or (r.get("allocated_tag") is None and tag in r["tags"])]
                # Invalid code is diagnostic evidence, not proof that a
                # behavior family is exhausted. Uncertainty permits revisits.
                valid_records=[r for r in records if r["valid"]]
                n=len(valid_records)
                gains=sum(r["useful_gain"] for r in valid_records)
                collisions=sum(r["terminal_collision"] for r in valid_records)
                posterior=(1+gains)/(2+n)
                uncertainty=math.sqrt(math.log(2+len(self.M))/(1+len(records)))
                score=posterior+0.45*uncertainty-0.5*collisions/max(1,n)
                history[tag]={"attempts":len(records),"valid":n,"gains":gains,"collisions":collisions,
                              "priority":score}
                scores[tag]=score
            target=max(tags,key=lambda t:(scores[t],-tags.index(t)))
            # Exploration reserve + periodic reactivation is fixed in advance.
            reactivate=step>0 and step%7==6
            if reactivate:
                target=self.rng.choice(tags)
            parent_pool=self.A if self.method!="terminal" else ([parent] if parent else [])
            parents=[n for n in parent_pool if target in n["tags"]]
            if parents:
                parent=min(parents,key=lambda n:n["evaluation"]["loss"])
            elif parent_pool:
                # Start from a competitive but less-consumed actual behavior.
                def pressure(n):
                    h=descriptor_hash(n["evaluation"]["behavior"])
                    return sum(r["behavior_hash"]==h for r in self.M)
                parent=min(parent_pool,key=lambda n:(pressure(n),n["evaluation"]["loss"]))
            target_state=history[target]
            saturated=(target_state["valid"]>=2 and target_state["collisions"]/target_state["valid"]>=0.6)
            action="restart" if self.no_growth>=2 or saturated or target_state["attempts"]==0 else "refine"
            if reactivate:
                action="reactivate"
            if parent:
                working=[] if self.method=="relational_no_w" else self.W
                candidates=[n for n in parent_pool+working if n["id"]!=parent["id"]]
                if candidates:
                    losses=parent["evaluation"]["per_instance_loss"]
                    def complement(n):
                        ev=n["evaluation"]
                        paired=statistics.fmean(max(0,a-b) for a,b in zip(losses,ev["per_instance_loss"]))
                        trajectory_novelty=behavior_distance(parent["evaluation"]["trajectory_behavior"],ev["trajectory_behavior"])
                        return paired+0.02*trajectory_novelty
                    reference=max(candidates,key=complement)
                    if complement(reference)>0.005 and action=="refine":
                        action="recombine"
            evidence.update(tag_statistics=history,selected_tag=target,saturated=saturated,
                no_growth=self.no_growth,reactivated=reactivate,
                repeated_outcomes=[r for r in self.M if r["terminal_collision"]][-3:])
        if action=="restart":
            parent=None
        return {"target":target,"action":action,"parent":parent,"reference":reference,
                "evidence":evidence,"recent":[_brief(n) for n in self.nodes[-2:]]}


def planner_prompt(task,selection,step):
    context={"task":task,"task_contract":DESCRIPTIONS[task],"iteration":step,
        "target_strategy_family":selection["target"],"action":selection["action"],
        "parent":_brief(selection["parent"]) if selection["parent"] else None,
        "complementary_reference":_brief(selection["reference"]) if selection["reference"] else None,
        "allocation_evidence":selection["evidence"],
        "allowed_tags":TAGS[task],
        "common_seed_rules":[{"name":name,"code":code} for name,tags,code in SEEDS[task]],
        "recent_execution_feedback":selection.get("recent",[]),
        "quality_requirement":"Competitive quality is mandatory. Use the seed rules as anchors even for restart. Monotone transforms or feature terms constant across candidate actions produce identical behavior; seek justified ranking changes on a useful subset. Large distance-ignoring terms in TSP or unnecessary loose packing often hurt. Keep nonlinear corrections bounded and explain the hypothesis.",
        "request":"Propose a concrete mathematical scoring heuristic. Explain the intended behavior change in <=100 words. Output JSON with name, intent, tags (1-2 allowed_tags), formula. Do not claim improvement until evaluated; do not output code at planning stage."}
    return json.dumps(context,ensure_ascii=False)


def coder_prompt(task,plan,selection):
    return json.dumps({"task_contract":DESCRIPTIONS[task],"features":FEATURES[task],
        "language":GRAMMAR,"plan":plan,
        "parent_code":selection["parent"]["code"] if selection["parent"] else None,
        "reference_code":selection["reference"]["code"] if selection["reference"] else None,
        "request":"Implement the proposed priority rule as actual Python code. Output JSON with exactly one code field. Use newline escapes valid inside JSON. No annotations or imports."},ensure_ascii=False)


def _save(path,obj):
    temp=path.with_suffix(path.suffix+".tmp")
    temp.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
    temp.replace(path)


def source_fingerprint():
    root=Path(__file__).parent
    digest=hashlib.sha256()
    for name in ("programs.py","benchmarks.py","discovery.py","providers.py","classification.py"):
        digest.update(name.encode())
        digest.update((root/name).read_bytes())
    return digest.hexdigest()


def run_search(task,method,seed,steps,provider,model,output):
    if method not in METHODS:
        raise ValueError("Unknown method.")
    directory=Path(output)
    directory.mkdir(parents=True,exist_ok=True)
    final_file=directory/"result.json"
    checkpoint_file=directory/"checkpoint.json"
    config={"task":task,"method":method,"seed":seed,"steps":steps,"provider":provider,"model":model,
            "benchmark_version":VERSION,"quality_tolerance":QUALITY_TOLERANCE[task],
            "behavior_radius":BEHAVIOR_RADIUS,"archive_capacity":MAX_ARCHIVE,
            "source_fingerprint":source_fingerprint(),
            "splits":{s:split_fingerprint(task,s) for s in ("probe","validation","test")}}
    if final_file.exists():
        old=json.loads(final_file.read_text(encoding="utf-8"))
        if old["config"]!=config:
            raise ValueError("Existing run has different config; choose a new output directory.")
        print(json.dumps({"cached_run":str(directory),"summary":old["summary"]}),flush=True)
        return old
    client=ModelClient.from_opencode(provider,model)
    state=SearchState(task,method,seed)
    usage=[]
    llm_errors=[]
    start=time.perf_counter()
    prior_elapsed=0.0
    if checkpoint_file.exists():
        saved=json.loads(checkpoint_file.read_text(encoding="utf-8"))
        if saved["config"]!=config:
            raise ValueError("Checkpoint config mismatch.")
        for node in saved["nodes"]:
            state.observe(node)
        usage=saved["usage"]
        llm_errors=saved["llm_errors"]
        prior_elapsed=saved.get("elapsed_seconds",0)
        if saved.get("rng_state"):
            def tuples(obj): return tuple(tuples(x) for x in obj) if isinstance(obj,list) else obj
            state.rng.setstate(tuples(saved["rng_state"]))
    else:
        for i,(name,tags,code) in enumerate(SEEDS[task]):
            ev=evaluate(code,task)
            state.observe({"id":i,"name":name,"intent":"shared hand-written seed: "+name,
                "tags":tags,"code":code,"parent_id":None,"reference_id":None,"action":"seed",
                "source":"handwritten_seed","evaluation":ev})
    completed=len(state.nodes)-len(SEEDS[task])
    for step in range(completed,steps):
        selection=state.choose(step)
        plan_prompt=planner_prompt(task,selection,step)
        plan={"name":f"candidate_{step}","intent":"unavailable","tags":[selection["target"]]}
        code=""
        try:
            response=client.complete(SYSTEM,plan_prompt,max_tokens=1800)
            usage.append({"stage":"planner","iteration":step,**response.usage()})
            _save(directory/f"{step:03d}_plan.json",{"prompt":plan_prompt,"response":response.text,"usage":response.usage()})
            generated_plan=parse_json(response.text)
            if not isinstance(generated_plan,dict):
                raise ValueError("Planner must return an object.")
            plan.update(generated_plan)
            code_prompt=coder_prompt(task,plan,selection)
            response=client.complete(SYSTEM,code_prompt,max_tokens=2000)
            usage.append({"stage":"coder","iteration":step,**response.usage()})
            _save(directory/f"{step:03d}_code.json",{"prompt":code_prompt,"response":response.text,"usage":response.usage()})
            data=parse_json(response.text)
            code=data.get("code","") if isinstance(data,dict) else ""
            if not isinstance(code,str):
                code=""
            evaluation=evaluate(code,task)
        except (ModelError,ValueError,TypeError,KeyError) as exc:
            llm_errors.append({"iteration":step,"type":type(exc).__name__,"error":str(exc)[:160]})
            evaluation=evaluate("",task)
            evaluation.update(failure_type=type(exc).__name__,error=str(exc)[:160])
        # Both stages retain actual usage; there is no invisible retry or
        # fallback code. Invalid candidates consume their proposal slot.
        plan_tags=plan.get("tags",[])
        plan_tags=[t for t in plan_tags if t in TAGS[task]] if isinstance(plan_tags,list) else []
        node={"id":len(state.nodes),"name":str(plan.get("name","candidate"))[:80],
              "intent":str(plan.get("intent",""))[:800],"tags":plan_tags[:2] or [selection["target"]],
              "allocated_tag":selection["target"],"code":code,"source":"live_llm",
              "parent_id":selection["parent"]["id"] if selection["parent"] else None,
              "reference_id":selection["reference"]["id"] if selection["reference"] else None,
              "action":selection["action"],"allocation":selection["evidence"],"evaluation":evaluation}
        state.observe(node)
        checkpoint={"config":config,"nodes":state.nodes,"usage":usage,"llm_errors":llm_errors,
                    "rng_state":state.rng.getstate(),"elapsed_seconds":prior_elapsed+time.perf_counter()-start}
        _save(checkpoint_file,checkpoint)
        print(json.dumps({"task":task,"method":method,"seed":seed,"step":step+1,"steps":steps,
            "valid":evaluation["valid"],"loss":evaluation["loss"],"modes":len(state.A),
            "action":selection["action"],"collision":state.events[-1]["terminal_collision"]}),flush=True)
    # Final reporting archive and best program are fixed from validation BEFORE
    # any hidden-test feedback exists. Test never enters state or prompts.
    archive=report_archive(state.nodes,task)
    test={}
    for node in archive:
        test[str(node["id"])]=evaluate(node["code"],task,split="test",with_probes=False)
    generated=[n for n in state.nodes if n["source"]=="live_llm"]
    events=state.events[len(SEEDS[task]):]
    best=state._simple_parent()
    if best and str(best["id"]) not in test:
        test[str(best["id"])]=evaluate(best["code"],task,split="test",with_probes=False)
    valid_generated=[n for n in generated if n["evaluation"]["valid"]]
    summary={"generated":len(generated),"valid_generated":len(valid_generated),
        "valid_fraction":len(valid_generated)/max(1,len(generated)),
        "best_validation_loss":state.best,"validation_selected_best_id":best["id"] if best else None,
        "validation_selected_test_loss":test[str(best["id"])]["loss"] if best else None,
        "quality_constrained_modes":len(archive),
        "terminal_collision_rate":sum(e["terminal_collision"] for e in events)/max(1,len(events)),
        "different_intent_collisions":sum(e["different_intent_collision"] for e in events),
        "useful_generated":sum(e["useful_gain"] for e in events),
        "model_calls":len(usage),"input_tokens":sum(u["input_tokens"] for u in usage),
        "output_tokens":sum(u["output_tokens"] for u in usage),
        "model_seconds":sum(u["seconds"] for u in usage),
        "evaluator_cpu_seconds":sum(n["evaluation"]["cpu_seconds"] for n in state.nodes),
        "feature_calls":sum(n["evaluation"]["features_called"] for n in state.nodes),
        "local_checks":sum(n["evaluation"]["local_checks"] for n in state.nodes),
        "elapsed_seconds":prior_elapsed+time.perf_counter()-start,
        "actions":dict(Counter(n["action"] for n in generated)),
        "api_price":"coding plan; marginal cash cost not inferred"}
    result={"config":config,"summary":summary,"nodes":state.nodes,"events":state.events,
            "archive_ids":[n["id"] for n in archive],"working_ids":[n["id"] for n in state.W],
            "terminal_memory":state.M,"curve":state.curve,"usage":usage,"llm_errors":llm_errors,"test":test,
            "claims":{"level":"live bounded-program synthesis pilot",
                "mode_definition":"quality-constrained clusters of executed behavior on fixed probes; not enumerated algorithmic optima",
                "not_reproduced":["MLEvolve","SeaEvo","AdaEvolve"],
                "protocol":"equal proposal slots and maximum tokens per call; actual token and CPU costs reported; same final archive readout"}}
    _save(final_file,result)
    (directory/"programs").mkdir(exist_ok=True)
    for node in archive:
        (directory/"programs"/f"candidate_{node['id']:03d}.py").write_text(node["code"],encoding="utf-8")
    print(json.dumps({"finished":str(directory),"summary":summary}),flush=True)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--task",choices=TAGS,default="tsp")
    p.add_argument("--method",choices=METHODS,default="relational")
    p.add_argument("--seed",type=int,default=0)
    p.add_argument("--steps",type=int,default=12)
    p.add_argument("--provider",default="alibaba-token-plan-cn")
    p.add_argument("--model",default="qwen3.7-plus")
    p.add_argument("--output",required=True)
    args=p.parse_args()
    run_search(args.task,args.method,args.seed,args.steps,args.provider,args.model,args.output)


if __name__=="__main__":
    main()
