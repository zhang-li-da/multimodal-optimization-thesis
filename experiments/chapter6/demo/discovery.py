"""Live planner/coder search with quality, niching and relational controllers."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import statistics
import time

from .benchmarks import (VERSION, INDEPENDENT_PROFILE, V12_TSP_PROFILE, TAGS, DESCRIPTIONS, SEEDS, FEATURES, instances,
    split_fingerprint, evaluate, behavior_distance, descriptor_hash)
from .providers import ModelClient, ModelError, parse_json
from .programs import ProgramError

SYSTEM = "You are an algorithm researcher writing small executable heuristics. Obey the provided bounded Python language. Return one valid JSON object only; no markdown. Assess feedback empirically and do not invent evaluation results."
GRAMMAR = "Allowed Python: exactly def priority(f), scalar local assignments, return, if/else or conditional expression, + - * / % ** (constant exponent <=4), comparisons, boolean operations, calls abs/min/max/sqrt/log/log1p/exp/tanh. NO imports, annotations, loops, lists, arrays, attributes, f.get(), helpers, mutation, I/O, random, or other calls. Access only listed features with f['feature']. Guard denominators with 1e-9 and log domains. <=25 source lines and <=320 AST nodes. Higher returned value is chosen."
METHODS = ("quality", "niche", "terminal", "relational", "relational_no_w",
           "relational_qp", "relational_rr", "relational_qp_rr",
           "niche_fixed_dev", "relational_branch")
CONTROLLER_FACTORS = {
    "relational": {"quality_protection": False, "restart_correction": False},
    "relational_qp": {"quality_protection": True, "restart_correction": False},
    "relational_rr": {"quality_protection": False, "restart_correction": True},
    "relational_qp_rr": {"quality_protection": True, "restart_correction": True},
}
QUALITY_TOLERANCE = {"tsp":0.035, "binpack":0.035, "classification":0.025}
BEHAVIOR_RADIUS = 0.08
MAX_ARCHIVE = 10
MAX_WORKING = 16
MAX_TERMINAL = 128
LOCAL_GAIN_EPSILON = 1e-4
MAX_LOCAL_CREDITS = 2


def _brief(node):
    e=node["evaluation"]
    return {"id":node["id"],"intent":node["intent"],"tags":node["tags"],
            "code":node["code"],"loss":e["loss"],"valid":e["valid"],
            "family_loss":e.get("family_loss",{}),"failure_type":e.get("failure_type"),
            "error":e.get("error"),"trajectory_values":e.get("trajectory_values",[])[:2]}


def report_archive(nodes, task, capacity=MAX_ARCHIVE, quality_reference=None):
    """Same final readout for EVERY method, isolated from its search policy."""
    valid=[n for n in nodes if n["evaluation"]["valid"]]
    if not valid:
        return []
    best=min(n["evaluation"]["loss"] for n in valid) if quality_reference is None else quality_reference
    eligible=sorted((n for n in valid if n["evaluation"]["loss"]<=best+QUALITY_TOLERANCE[task]),
                    key=lambda n:(n["evaluation"]["loss"],n["evaluation"].get("ast_nodes",0),n["id"]))
    kept=[]
    for node in eligible:
        if not kept or min(behavior_distance(node["evaluation"]["behavior"],k["evaluation"]["behavior"]) for k in kept) > BEHAVIOR_RADIUS:
            kept.append(node)
        if len(kept)==capacity:
            break
    return kept


def _restart_decision(valid_count, saturation_collision_count, attempts, no_growth,
                      restart_correction, recent_local_development):
    saturated=(valid_count>=2 and saturation_collision_count/max(1,valid_count)>=0.6)
    stalled=no_growth>=2 and not (restart_correction and recent_local_development)
    return {"saturated":saturated,"stalled":stalled,
            "restart":stalled or saturated or attempts==0}


class SearchState:
    def __init__(self, task, method, seed, quality_protection=None, restart_correction=None):
        self.task,self.method=task,method
        defaults=CONTROLLER_FACTORS.get(method, {"quality_protection":False,"restart_correction":False})
        self.quality_protection=defaults["quality_protection"] if quality_protection is None else quality_protection
        self.restart_correction=defaults["restart_correction"] if restart_correction is None else restart_correction
        self.rng=random.Random(seed)
        self.nodes=[]
        self.A=[]
        self.W=[]
        self.M=[]
        self.events=[]
        self.curve=[]
        self.best=None
        self.no_growth=0
        self.local_credits={}

    def observe(self,node):
        ev=node["evaluation"]
        previous=[n for n in self.nodes if n["evaluation"]["valid"]]
        nearest=None
        distance=None
        if ev["valid"] and previous:
            nearest=min(previous,key=lambda n:(behavior_distance(ev["behavior"],n["evaluation"]["behavior"]),
                                                n["evaluation"]["loss"],n["id"]))
            distance=behavior_distance(ev["behavior"],nearest["evaluation"]["behavior"])
        collision=ev["valid"] and distance is not None and distance<=BEHAVIOR_RADIUS
        improved=ev["valid"] and (self.best is None or ev["loss"] < self.best-1e-9)
        competitive=ev["valid"] and (self.best is None or ev["loss"] <= self.best+QUALITY_TOLERANCE[self.task])
        parent=next((n for n in self.nodes if n["id"]==node.get("parent_id") and n["evaluation"]["valid"]),None)
        parent_margin=(parent["evaluation"]["loss"]-ev["loss"]) if ev["valid"] and parent else None
        neighbor_margin=(nearest["evaluation"]["loss"]-ev["loss"]) if collision else None
        parent_improved=parent_margin is not None and parent_margin>LOCAL_GAIN_EPSILON
        neighbor_improved=neighbor_margin is not None and neighbor_margin>LOCAL_GAIN_EPSILON
        # Local gains are credited only while the candidate remains within the
        # common quality envelope. This protects useful development without
        # allowing a weak branch to claim unlimited progress.
        local_development=bool(competitive and (parent_improved or neighbor_improved))
        allocated=node.get("allocated_tag") or node["tags"][0]
        if improved or (competitive and not collision):
            self.local_credits[allocated]=0
        local_credit=bool(local_development and self.local_credits.get(allocated,0)<MAX_LOCAL_CREDITS)
        if local_credit and not improved:
            self.local_credits[allocated]=self.local_credits.get(allocated,0)+1
        productive_collision=bool(collision and (improved or local_development))
        unproductive_collision=bool(collision and not productive_collision)
        gain=improved or (competitive and not collision) or (self.quality_protection and local_credit)
        global_margin=(self.best-ev["loss"]) if ev["valid"] and self.best is not None else None
        self.best=min(self.best,ev["loss"]) if ev["valid"] and self.best is not None else (ev["loss"] if ev["valid"] else self.best)
        self.no_growth=0 if gain else self.no_growth+1
        event={"node_id":node["id"],"tags":node["tags"],"allocated_tag":node.get("allocated_tag"),"parent_id":node["parent_id"],
               "reference_id":node["reference_id"],"action":node["action"],"valid":ev["valid"],
               "terminal_collision":bool(collision),"distance_to_previous":distance,
               "nearest_node":nearest["id"] if nearest else None,
               "different_intent_collision":bool(collision and set(node["tags"])!=set(nearest["tags"])),
               "improved":bool(improved),"parent_improvement_margin":parent_margin,
               "global_improvement_margin":global_margin,"local_credit_eligible":local_credit,
               "neighborhood_improvement_margin":neighbor_margin,
               "parent_improved":bool(parent_improved),"neighborhood_improved":bool(neighbor_improved),
               "competitive_local_development":bool(local_development),
               "productive_collision":productive_collision,"unproductive_collision":unproductive_collision,
               "useful_gain":bool(gain),"quality_protection":self.quality_protection,
               "restart_correction":self.restart_correction,
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
        elif self.method in ("terminal", "relational", "relational_no_w") or self.method in CONTROLLER_FACTORS:
            scores={}
            history={}
            for tag in tags:
                records=[r for r in self.M if r.get("allocated_tag")==tag or (r.get("allocated_tag") is None and tag in r["tags"])]
                # Invalid code is diagnostic evidence, not proof that a
                # behavior family is exhausted. Uncertainty permits revisits.
                valid_records=[r for r in records if r["valid"]]
                n=len(valid_records)
                gains=sum(r["useful_gain"] for r in valid_records)
                raw_collisions=sum(r["terminal_collision"] for r in valid_records)
                penalized_collisions=sum(r["terminal_collision"] and not (r["improved"] or r["local_credit_eligible"])
                                        for r in valid_records) if self.quality_protection else raw_collisions
                posterior=(1+gains)/(2+n)
                uncertainty=math.sqrt(math.log(2+len(self.M))/(1+len(records)))
                score=posterior+0.45*uncertainty-0.5*penalized_collisions/max(1,n)
                saturation_collisions=sum(r["unproductive_collision"] for r in valid_records) if self.restart_correction else raw_collisions
                recent_local_development=any(r.get("local_credit_eligible",False) for r in valid_records[-2:]
                    if r.get("node_id",-1)>=len(self.nodes)-2)
                history[tag]={"attempts":len(records),"valid":n,"gains":gains,
                              "collisions":raw_collisions,"penalized_collisions":penalized_collisions,
                              "saturation_collisions":saturation_collisions,
                              "recent_local_development":recent_local_development,
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
            restart_state=_restart_decision(target_state["valid"],target_state["saturation_collisions"],
                target_state["attempts"],self.no_growth,self.restart_correction,
                target_state["recent_local_development"])
            saturated=restart_state["saturated"]
            stalled=restart_state["stalled"]
            action="restart" if restart_state["restart"] else "refine"
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
                no_growth=self.no_growth,stalled=stalled,reactivated=reactivate,
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
    root=Path(__file__).resolve().parents[3]
    digest=hashlib.sha256()
    names=(
        "chapter6_demo/__init__.py",
        "experiments/chapter6/demo/programs.py",
        "experiments/chapter6/demo/benchmarks.py",
        "experiments/chapter6/demo/discovery.py",
        "experiments/chapter6/demo/providers.py",
        "experiments/chapter6/demo/inspect_environment.py",
        "experiments/chapter6/demo/classification.py",
        "experiments/chapter6/v11/preregistration.md",
        "experiments/chapter6/v11/selector.py",
        "experiments/chapter6/v11/run_factorial.py",
        "experiments/chapter6/v11/analyze_factorial.py",
        "experiments/chapter6/v11/test_v11.py",
        "experiments/chapter6/v11/README.md",
    )
    for name in names:
        digest.update(name.encode())
        digest.update((root/name).read_bytes().replace(b"\r\n",b"\n"))
    return digest.hexdigest()


def _token_reservation(prompt, max_output):
    """Conservative admission bound: UTF-8 input bytes plus fixed margin and output cap."""
    return len(SYSTEM.encode("utf-8"))+len(prompt.encode("utf-8"))+512+max_output


def _behavior_mode_count(nodes, evaluations, radius=BEHAVIOR_RADIUS):
    ordered=sorted(nodes,key=lambda n:(evaluations[str(n["id"])]["loss"],n["id"]))
    kept=[]
    for node in ordered:
        behavior=evaluations[str(node["id"])]["behavior"]
        if not kept or min(behavior_distance(behavior,evaluations[str(k["id"])]["behavior"]) for k in kept)>radius:
            kept.append(node)
    return len(kept)


def run_search(task,method,seed,steps,provider,model,output,token_budget=None):
    if method not in METHODS:
        raise ValueError("Unknown method.")
    directory=Path(output)
    directory.mkdir(parents=True,exist_ok=True)
    final_file=directory/"result.json"
    checkpoint_file=directory/"checkpoint.json"
    pending_file=directory/"pending_call.json"
    profile=os.getenv("CHAPTER6_BENCHMARK_PROFILE", "")
    if profile and profile not in (INDEPENDENT_PROFILE,V12_TSP_PROFILE):
        raise ValueError("Unknown experiment benchmark profile.")
    if profile==V12_TSP_PROFILE and task!="tsp":
        raise ValueError("The v1.2 profile is defined for TSP only.")
    data_block=int(os.getenv("CHAPTER6_DATA_BLOCK", "0"))
    if data_block<0:
        raise ValueError("Data block must be nonnegative.")
    if token_budget is not None and token_budget<=0:
        raise ValueError("Token budget must be positive.")
    benchmark_version=f"{VERSION}|{profile or 'default'}"
    factors=CONTROLLER_FACTORS.get(method,{"quality_protection":False,"restart_correction":False})
    if profile==V12_TSP_PROFILE:
        from .v12_controller import V12_METHODS, V12SearchState, v12_source_fingerprint
        if method not in V12_METHODS:
            raise ValueError("The v1.2 profile requires a preregistered v1.2 controller.")
        run_fingerprint=v12_source_fingerprint()
    else:
        run_fingerprint=source_fingerprint()
    config={"task":task,"method":method,"seed":seed,"steps":steps,"provider":provider,"model":model,
            "benchmark_version":benchmark_version,"data_block":data_block,
            "quality_tolerance":QUALITY_TOLERANCE[task],
            "behavior_radius":BEHAVIOR_RADIUS,"archive_capacity":MAX_ARCHIVE,
            "controller_factors":factors,"token_budget":token_budget,
            "source_fingerprint":run_fingerprint,
            "splits":{s:split_fingerprint(task,s) for s in ("probe","validation","test")}}
    if final_file.exists():
        old=json.loads(final_file.read_text(encoding="utf-8"))
        if old["config"]!=config:
            raise ValueError("Existing run has different config; choose a new output directory.")
        print(json.dumps({"cached_run":str(directory),"summary":old["summary"]}),flush=True)
        return old
    # An interrupted request may have been billed without a response. Do not
    # silently replay it. The runner preserves this run as an infrastructure
    # failure until explicit provenance-aware recovery is possible.
    if pending_file.exists():
        pending=json.loads(pending_file.read_text(encoding="utf-8"))
        checkpoint=json.loads(checkpoint_file.read_text(encoding="utf-8")) if checkpoint_file.exists() else {}
        finished=len(checkpoint.get("nodes",[]))-len(SEEDS[task])
        if pending.get("iteration",finished)>=finished and not checkpoint.get("search_finished",False):
            raise RuntimeError("Interrupted model attempt remains in pending_call.json; refusing an unaccounted retry.")
    client=ModelClient.from_opencode(provider,model)
    if profile==V12_TSP_PROFILE:
        state=V12SearchState(task,method,seed,**factors)
    else:
        state=SearchState(task,method,seed,**factors)
    usage=[]
    llm_errors=[]
    budget_stops=[]
    reservation_violations=[]
    usage_missing=[]
    start=time.perf_counter()
    prior_elapsed=0.0
    search_finished=False
    if checkpoint_file.exists():
        saved=json.loads(checkpoint_file.read_text(encoding="utf-8"))
        if saved["config"]!=config:
            raise ValueError("Checkpoint config mismatch.")
        for node in saved["nodes"]:
            state.observe(node)
        usage=saved["usage"]
        llm_errors=saved["llm_errors"]
        budget_stops=saved.get("budget_stops",[])
        reservation_violations=saved.get("reservation_violations",[])
        usage_missing=saved.get("usage_missing",[])
        prior_elapsed=saved.get("elapsed_seconds",0)
        search_finished=saved.get("search_finished",False)
        if saved.get("rng_state"):
            def tuples(obj): return tuple(tuples(x) for x in obj) if isinstance(obj,list) else obj
            state.rng.setstate(tuples(saved["rng_state"]))
    else:
        for i,(name,tags,code) in enumerate(SEEDS[task]):
            ev=evaluate(code,task)
            state.observe({"id":i,"name":name,"intent":"shared hand-written seed: "+name,
                "tags":tags,"code":code,"parent_id":None,"reference_id":None,"action":"seed",
                "source":"handwritten_seed","evaluation":ev})

    def record_usage(stage, iteration, response):
        item={"stage":stage,"iteration":iteration,"recorded_utc":datetime.now(timezone.utc).isoformat(),**response.usage()}
        usage.append(item)
        if item["input_tokens"]<=0 or item["output_tokens"]<=0:
            usage_missing.append({"stage":stage,"iteration":iteration,
                                  "reason":"provider returned zero or missing token usage"})

    completed=len(state.nodes)-len(SEEDS[task])
    for step in range(steps if search_finished else completed,steps):
        stop_after_candidate=False
        selection=state.choose(step)
        plan_prompt=planner_prompt(task,selection,step)
        planner_reserve=_token_reservation(plan_prompt,1800)
        spent=sum(u["input_tokens"]+u["output_tokens"] for u in usage)
        if token_budget is not None and spent+planner_reserve>token_budget:
            budget_stops.append({"iteration":step,"stage":"planner_admission","tokens_used":spent,
                                 "tokens_reserved":planner_reserve,"budget":token_budget})
            break
        plan={"name":f"candidate_{step}","intent":"unavailable","tags":[selection["target"]]}
        code=""
        try:
            _save(pending_file,{"iteration":step,"stage":"planner_requested","config":config})
            response=client.complete(SYSTEM,plan_prompt,max_tokens=1800)
            record_usage("planner",step,response)
            observed=response.input_tokens+response.output_tokens
            if observed>planner_reserve:
                reservation_violations.append({"iteration":step,"stage":"planner",
                    "observed_tokens":observed,"reserved_tokens":planner_reserve})
            _save(directory/f"{step:03d}_plan.json",{"prompt":plan_prompt,"response":response.text,"usage":response.usage()})
            if token_budget is not None and observed>planner_reserve:
                budget_stops.append({"iteration":step,"stage":"planner_reservation_violation","budget":token_budget})
                break
            if (token_budget is not None and usage_missing
                    and usage_missing[-1]["iteration"]==step and usage_missing[-1]["stage"]=="planner"):
                budget_stops.append({"iteration":step,"stage":"planner_usage_unavailable",
                                     "budget":token_budget,"planner_usage_recorded":False})
                break
            generated_plan=parse_json(response.text)
            if not isinstance(generated_plan,dict):
                raise ValueError("Planner must return an object.")
            plan.update(generated_plan)
            code_prompt=coder_prompt(task,plan,selection)
            coder_reserve=_token_reservation(code_prompt,2000)
            spent=sum(u["input_tokens"]+u["output_tokens"] for u in usage)
            if token_budget is not None and spent+coder_reserve>token_budget:
                budget_stops.append({"iteration":step,"stage":"coder_admission_after_planner",
                                     "tokens_used":spent,"tokens_reserved":coder_reserve,
                                     "budget":token_budget,"planner_usage_recorded":True})
                break
            _save(pending_file,{"iteration":step,"stage":"coder_requested","config":config})
            response=client.complete(SYSTEM,code_prompt,max_tokens=2000)
            record_usage("coder",step,response)
            if usage_missing and usage_missing[-1]["iteration"]==step and usage_missing[-1]["stage"]=="coder":
                stop_after_candidate=token_budget is not None
            observed=response.input_tokens+response.output_tokens
            if observed>coder_reserve:
                reservation_violations.append({"iteration":step,"stage":"coder",
                    "observed_tokens":observed,"reserved_tokens":coder_reserve})
                stop_after_candidate=token_budget is not None
            _save(directory/f"{step:03d}_code.json",{"prompt":code_prompt,"response":response.text,"usage":response.usage()})
            data=parse_json(response.text)
            code=data.get("code","") if isinstance(data,dict) else ""
            if not isinstance(code,str):
                code=""
            evaluation=evaluate(code,task)
        except (ModelError,ValueError,TypeError,KeyError) as exc:
            llm_errors.append({"iteration":step,"type":type(exc).__name__,"error":str(exc)[:160]})
            if isinstance(exc,ModelError):
                usage_missing.append({"stage":"request_error","iteration":step,
                                      "reason":"request failed without provider token usage"})
                stop_after_candidate=token_budget is not None
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
              "action":selection["action"],"allocation":selection.get("audit",selection["evidence"]),"evaluation":evaluation}
        state.observe(node)
        checkpoint={"config":config,"nodes":state.nodes,"usage":usage,"llm_errors":llm_errors,
                    "budget_stops":budget_stops,"reservation_violations":reservation_violations,
                    "usage_missing":usage_missing,"rng_state":state.rng.getstate(),
                    "elapsed_seconds":prior_elapsed+time.perf_counter()-start}
        _save(checkpoint_file,checkpoint)
        pending_file.unlink(missing_ok=True)
        print(json.dumps({"task":task,"method":method,"seed":seed,"step":step+1,"steps":steps,
            "valid":evaluation["valid"],"loss":evaluation["loss"],"modes":len(state.A),
            "action":selection["action"],"collision":state.events[-1]["terminal_collision"]}),flush=True)
        if stop_after_candidate:
            budget_stops.append({"iteration":step,"stage":"stop_after_unknown_usage",
                                 "budget":token_budget,"reason":"cannot safely admit another model request"})
            break
    _save(checkpoint_file,{"config":config,"nodes":state.nodes,"usage":usage,"llm_errors":llm_errors,
         "budget_stops":budget_stops,"reservation_violations":reservation_violations,
         "usage_missing":usage_missing,"rng_state":state.rng.getstate(),
         "search_finished":True,"elapsed_seconds":prior_elapsed+time.perf_counter()-start})
    pending_file.unlink(missing_ok=True)
    # Final reporting archive and best program are fixed from validation BEFORE
    # any hidden-test feedback exists. Test never enters state or prompts.
    shared_seed_nodes=[n for n in state.nodes if n["source"]=="handwritten_seed"]
    shared_seed_validation=min(n["evaluation"]["loss"] for n in shared_seed_nodes)
    archive=report_archive(state.nodes,task,quality_reference=shared_seed_validation)
    test={}
    for node in archive:
        test[str(node["id"])]=evaluate(node["code"],task,split="test",with_probes=False)
    generated=[n for n in state.nodes if n["source"]=="live_llm"]
    events=state.events[len(SEEDS[task]):]
    best=state._simple_parent()
    if best and str(best["id"]) not in test:
        test[str(best["id"])]=evaluate(best["code"],task,split="test",with_probes=False)
    for node in shared_seed_nodes:
        if str(node["id"]) not in test:
            test[str(node["id"])]=evaluate(node["code"],task,split="test",with_probes=False)
    shared_seed_test=min(test[str(n["id"])]["loss"] for n in shared_seed_nodes)
    shared_test_threshold=shared_seed_test+QUALITY_TOLERANCE[task]
    common_test_archive=[n for n in archive if test[str(n["id"])]["valid"] and test[str(n["id"])]["loss"]<=shared_test_threshold]
    common_test_archive.sort(key=lambda n:(test[str(n["id"])]["loss"],n["id"]))
    archive_test_rows=[test[str(n["id"])]["per_instance_loss"] for n in common_test_archive]
    if archive_test_rows:
        per_instance_oracle=[min(row[j] for row in archive_test_rows) for j in range(len(archive_test_rows[0]))]
        oracle_test=statistics.fmean(per_instance_oracle)
        best_single_test=min(test[str(n["id"])]["loss"] for n in common_test_archive)
        oracle_gain=best_single_test-oracle_test
    else:
        oracle_test=best_single_test=oracle_gain=None
    valid_generated=[n for n in generated if n["evaluation"]["valid"]]
    summary={"generated":len(generated),"valid_generated":len(valid_generated),
        "valid_fraction":len(valid_generated)/max(1,len(generated)),
        "best_validation_loss":state.best,"validation_selected_best_id":best["id"] if best else None,
        "validation_selected_test_loss":(test[str(best["id"])]["loss"] if test[str(best["id"])]["valid"] else 1.0) if best else None,
        "validation_selected_test_valid":test[str(best["id"])]["valid"] if best else False,
        "test_failure_penalty_loss":1.0,
        "shared_seed_best_validation_loss":shared_seed_validation,
        "shared_seed_best_test_loss":shared_seed_test,
        "quality_constrained_modes":len(archive),
        "common_gate_archive_size":len(archive),
        "common_gate_test_eligible_size":len(common_test_archive),
        "common_gate_test_behavior_modes":_behavior_mode_count(common_test_archive,test),
        "best_single_test_loss_within_common_archive":best_single_test,
        "test_instance_oracle_archive_loss_upper_bound_only":oracle_test,
        "test_instance_oracle_gain_upper_bound_only":oracle_gain,
        "terminal_collision_rate":sum(e["terminal_collision"] for e in events)/max(1,len(events)),
        "different_intent_collisions":sum(e["different_intent_collision"] for e in events),
        "useful_generated":sum(e["useful_gain"] for e in events),
        "model_calls":len(usage),"input_tokens":sum(u["input_tokens"] for u in usage),
        "output_tokens":sum(u["output_tokens"] for u in usage),
        "usage_missing_calls":len(usage_missing),"usage_complete":not usage_missing,
        "request_errors":len(llm_errors),
        "partial_attempts":sum(s["stage"] in ("coder_admission_after_planner","planner_usage_unavailable","planner_reservation_violation")
                                for s in budget_stops),
        "model_seconds":sum(u["seconds"] for u in usage),
        "evaluator_cpu_seconds":sum(n["evaluation"]["cpu_seconds"] for n in state.nodes),
        "feature_calls":sum(n["evaluation"]["features_called"] for n in state.nodes),
        "local_checks":sum(n["evaluation"]["local_checks"] for n in state.nodes),
        "elapsed_seconds":prior_elapsed+time.perf_counter()-start,
        "token_budget":token_budget,
        "local_gain_epsilon":LOCAL_GAIN_EPSILON,"maximum_local_credits_per_family":MAX_LOCAL_CREDITS,
        "tokens_used":sum(u["input_tokens"]+u["output_tokens"] for u in usage),
        "tokens_remaining":None if token_budget is None else token_budget-sum(u["input_tokens"]+u["output_tokens"] for u in usage),
        "token_budget_valid":None if token_budget is None else
            (sum(u["input_tokens"]+u["output_tokens"] for u in usage)<=token_budget
             and not reservation_violations and not usage_missing),
        "budget_stop_stage":budget_stops[-1]["stage"] if budget_stops else None,
        "actions":dict(Counter(n["action"] for n in generated)),
        "parent_improvements":sum(e["parent_improved"] for e in events),
        "neighborhood_improvements":sum(e["neighborhood_improved"] for e in events),
        "competitive_local_developments":sum(e["competitive_local_development"] for e in events),
        "productive_collisions":sum(e["productive_collision"] for e in events),
        "unproductive_collisions":sum(e["unproductive_collision"] for e in events),
        "restarts":sum(n["action"]=="restart" for n in generated),
        "api_price":"coding plan; marginal cash cost not inferred"}
    from .v11.selector import fit_and_evaluate_selector
    selector=fit_and_evaluate_selector(
        {"nodes":state.nodes,"archive_ids":[n["id"] for n in archive],"test":test},
        task,instances(task,"validation"),instances(task,"test"))
    summary["algorithm_set_selector_status"]=selector.get("status")
    if hasattr(state,"summary"):
        summary.update(state.summary())
    result={"config":config,"summary":summary,"nodes":state.nodes,"events":state.events,
            "archive_ids":[n["id"] for n in archive],"working_ids":[n["id"] for n in state.W],
            "branch_pool":getattr(state,"branch_pool",[]),
            "terminal_memory":state.M,"curve":state.curve,"usage":usage,"llm_errors":llm_errors,
            "budget_stops":budget_stops,"reservation_violations":reservation_violations,
            "usage_missing":usage_missing,"test":test,"selector":selector,
            "claims":{"level":"live bounded-program synthesis screening study",
                "mode_definition":"quality-constrained clusters of executed behavior on fixed probes; not enumerated algorithmic optima",
                "not_reproduced":["MLEvolve","SeaEvo","AdaEvolve"],
                "protocol":"fixed candidate slots or total input+output token ceiling; actual token and CPU costs reported; same final archive readout"}}
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
    p.add_argument("--token-budget",type=int,default=None,
                   help="Optional hard input+output token ceiling, with UTF-8 byte admission reserve.")
    p.add_argument("--provider",default="alibaba-token-plan-cn")
    p.add_argument("--model",default="qwen3.7-plus")
    p.add_argument("--output",required=True)
    args=p.parse_args()
    run_search(args.task,args.method,args.seed,args.steps,args.provider,args.model,args.output,args.token_budget)


if __name__=="__main__":
    main()
