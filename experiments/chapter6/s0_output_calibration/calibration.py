"""S0 r2: task-shaped, single-attempt calibration before real search."""
from __future__ import annotations

import argparse
import base64
from collections import Counter
import copy
import csv
import hashlib
import json
import math
from pathlib import Path
import random
import statistics

from chapter6_demo.benchmarks import SEEDS, TAGS
from chapter6_demo.discovery import SYSTEM, planner_prompt, coder_prompt
from chapter6_demo.programs import Program, ProgramError, FEATURES
from chapter6_demo.providers import parse_json
from chapter6_demo.v12_1.audit_r2 import offline_only
from chapter6_demo.v12_2.calls import DurableCalls, HTTPTransport, IndeterminateCall, ProviderFailure
from chapter6_demo.v12_2.common import (ROOT, digest, environment, file_sha, git, read_json,
    run_lock, save_json, source_record, utcnow)

HERE = Path(__file__).resolve().parent
PROTOCOL = HERE / "protocol.final.json"
TEMPLATES = ("from_scratch", "parent_revision", "reference_revision")


def source():
    files = source_record()["files"].copy()
    for name in ("__init__.py", "calibration.py", "test_calibration.py", "protocol.final.json"):
        path = HERE / name
        files[path.relative_to(ROOT).as_posix()] = hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    return {"format": "s0-r2-named-lf-sha256", "files": files, "sha256": digest(files)}


def fixtures(cohort, template, repeat):
    """Synthetic execution feedback only, no TSP instance or hidden result."""
    offset = {"calibration": 0, "acceptance": 10, "end_to_end": 30}[cohort]
    j = offset + repeat
    def node(index):
        name, tags, code = SEEDS["tsp"][index]
        return {"id": index, "name": name, "intent": "synthetic interface fixture",
                "tags": tags, "code": code,
                "evaluation": {"loss": .081 + .0002*j - .003*index, "valid": True,
                    "family_loss": {"uniform": .10, "clustered": .04, "grid": .08},
                    "trajectory_values": [], "failure_type": None, "error": None}}
    parent = None if template == "from_scratch" else node(0 if cohort == "calibration" else 1)
    reference = node(2) if template == "reference_revision" else None
    target = TAGS["tsp"][(j + TEMPLATES.index(template)) % len(TAGS["tsp"])]
    selection = {"target": target, "action": "restart" if parent is None else ("recombine" if reference else "refine"),
        "parent": parent, "reference": reference, "evidence": {"decision_step": j}, "recent": []}
    a = round(.12 + .003*j, 4)
    c = round(.08 + .002*j, 4)
    plan = {"name": f"bounded_fixture_{cohort}_{template}_{repeat}",
            "intent": "Retain local distance and introduce a bounded progress-dependent return and regret correction.",
            "tags": ["return_aware", "regret"],
            "formula": f"-distance + {a} * progress * return_distance + {c} * regret / (1 + regret)"}
    return {"selection": selection, "plan": plan, "step": j,
            "synthetic_feedback_not_measured_quality": True}


def calibration_jobs(protocol):
    jobs = []
    for role in ("planner", "coder"):
        for ci, config in enumerate(protocol["configs"]):
            for ti, template in enumerate(TEMPLATES):
                for repeat in range(3):
                    jobs.append({"job_id": f"cal-{role[0]}-c{ci}-t{ti}-r{repeat}",
                        "cohort": "calibration", "role": role, "config_id": config["id"],
                        "template": template, "repeat": repeat, "fixture": fixtures("calibration", template, repeat)})
    random.Random(protocol["order_seed"]).shuffle(jobs)
    return jobs


def acceptance_jobs():
    return [{"job_id": f"acc-{role[0]}-t{ti}-r{repeat}", "cohort": "acceptance",
             "role": role, "config_id": "$selected_from_calibration", "template": template,
             "repeat": repeat, "fixture": fixtures("acceptance", template, repeat)}
            for role in ("planner", "coder") for ti, template in enumerate(TEMPLATES) for repeat in range(6)]


def e2e_jobs():
    return [{"job_id": f"e2e-t{ti}-r{repeat}-{role[0]}", "cohort": "end_to_end",
             "role": role, "config_id": "$selected_from_calibration", "case_id": f"e2e-t{ti}-r{repeat}",
             "template": template, "repeat": repeat, "fixture": fixtures("end_to_end", template, repeat)}
            for ti, template in enumerate(TEMPLATES) for repeat in range(2) for role in ("planner", "coder")]


def freeze(output):
    output = Path(output)
    if output.exists() or git("status", "--porcelain", "--untracked-files=no"):
        raise ValueError("Freeze requires a new directory and committed source.")
    protocol = read_json(PROTOCOL)
    jobs = calibration_jobs(protocol) + acceptance_jobs() + e2e_jobs()
    assert len(jobs) == 84 and len({j["job_id"] for j in jobs}) == 84
    manifest = {"schema": "chapter6-s0-r2-manifest", "status": "FROZEN_PENDING_EXECUTION",
        "created_utc": utcnow(), "source_commit": git("rev-parse", "HEAD"), "source": source(),
        "environment": environment(), "protocol": protocol, "jobs": jobs, "new_model_calls": 0}
    manifest["manifest_sha256"] = digest(manifest)
    save_json(output / "manifest.json", manifest, immutable=True)
    return manifest


def verify(study, runtime=False):
    m = read_json(Path(study) / "manifest.json")
    if digest({k: v for k, v in m.items() if k != "manifest_sha256"}) != m["manifest_sha256"]:
        raise ValueError("Manifest fingerprint mismatch.")
    if runtime and (m["source"] != source() or m["environment"] != environment() or m["protocol"] != read_json(PROTOCOL)):
        raise ValueError("Frozen source, environment or protocol differs.")
    return m


def decode_final(text):
    # Never parse a JSON draft embedded in a truncated, unclosed thought.
    if not isinstance(text, str) or text.count("<think>") != text.count("</think>"):
        raise ValueError("Unclosed thought or missing content")
    return parse_json(text)


def planner_valid(value):
    if not isinstance(value, dict):
        return False
    return (all(isinstance(value.get(k), str) and value[k].strip() for k in ("name", "intent", "formula"))
        and isinstance(value.get("tags"), list) and 1 <= len(value["tags"]) <= 2
        and all(t in TAGS["tsp"] for t in value["tags"]))


def safe_code_valid(code):
    try:
        program = Program(code, "tsp")
        # Deliberately include zeros and late-tour conditions; no Python exec.
        for k in range(4):
            features = {name: (.1 + .07*i)*(k+1) for i, name in enumerate(FEATURES["tsp"])}
            features["progress"] = (.1, .5, .9, 1.)[k]
            if k == 3:
                features.update(nearest_remaining=0., mean_remaining=0., regret=0., cluster_density=0., spread=0.)
            if not math.isfinite(program(features)):
                return False, "nonfinite_return"
        return True, "executable_on_4_feature_fixtures"
    except (ProgramError, TypeError, ValueError, RecursionError) as exc:
        return False, type(exc).__name__


def validate_response(role, text, finish_reason="stop", failure=None):
    result = {"complete": False, "json_valid": False, "schema_valid": False,
              "executable": False if role == "coder" else None, "valid": False}
    if failure:
        return {**result, "validation_reason": failure["type"]}
    result["complete"] = finish_reason == "stop" and text.count("<think>") == text.count("</think>")
    try:
        parsed = decode_final(text)
        result["json_valid"] = True
    except (ValueError, TypeError):
        return {**result, "validation_reason": "final_json_missing_or_malformed"}
    if role == "planner":
        result["schema_valid"] = bool(planner_valid(parsed))
        reason = "valid" if result["schema_valid"] else "planner_schema"
    elif role == "coder":
        result["schema_valid"] = isinstance(parsed, dict) and isinstance(parsed.get("code"), str) and bool(parsed["code"].strip())
        result["executable"], reason = safe_code_valid(parsed["code"]) if result["schema_valid"] else (False, "coder_schema")
    else:
        raise ValueError("Unknown response role")
    result["valid"] = bool(result["complete"] and result["schema_valid"] and (role == "planner" or result["executable"]))
    return {**result, "validation_reason": reason if result["complete"] else "incomplete_output"}


def metadata(folder):
    response = read_json(folder / "response.json") if (folder / "response.json").exists() else {}
    raw = read_json(folder / "raw_response.json") if (folder / "raw_response.json").exists() else {}
    try:
        body = json.loads(base64.b64decode(raw["envelope"]["body_base64"]))
    except (KeyError, ValueError):
        body = {}
    choice = (body.get("choices") or [{}])[0]
    usage = response.get("usage_raw") or {}
    return {**{k: response.get(k) for k in ("input_tokens", "output_tokens", "usage_complete",
             "seconds", "returned_model", "request_id")}, "finish_reason": choice.get("finish_reason"),
             "reasoning_tokens": (usage.get("completion_tokens_details") or {}).get("reasoning_tokens"),
             "cached_tokens": (usage.get("prompt_tokens_details") or {}).get("cached_tokens")}


class LazyTransport:
    def __init__(self, protocol):
        self.protocol = protocol
        self.transport = None
    def send(self, request, persist):
        if self.transport is None:
            self.transport = HTTPTransport(self.protocol["provider"], self.protocol["model"], self.protocol["timeout_seconds"])
            if self.transport.client.base_url != self.protocol["provider_endpoint"]:
                raise ProviderFailure("Configured endpoint differs from protocol.")
        self.transport.send(request, persist)


def run_one(study, manifest, job, config_id, transport, actual_plan=None):
    protocol = manifest["protocol"]
    cap = next(c for c in protocol["configs"] if c["id"] == config_id)[job["role"]+"_max_tokens"]
    fixture = job["fixture"]
    prompt = (planner_prompt("tsp", fixture["selection"], fixture["step"]) if job["role"] == "planner" else
              coder_prompt("tsp", actual_plan if actual_plan is not None else fixture["plan"], fixture["selection"]))
    folder = Path(study) / "runs" / job["job_id"]
    config = {"schema": "s0-r2-call", "manifest_sha256": manifest["manifest_sha256"],
              "job_id": job["job_id"], "provider": protocol["provider"], "model": protocol["model"],
              "parameters": {"temperature": protocol["temperature"]}, "config_id": config_id}
    save_json(folder / "config.json", config, immutable=True)
    calls = DurableCalls(folder, config, transport)
    failure, response = None, {}
    if (folder / "outcome.json").exists():
        previous = read_json(folder / "outcome.json")
        if previous["failure"]:
            return previous, None
    try:
        response = calls.complete(0, job["role"], SYSTEM, prompt, cap)
    except (IndeterminateCall, ProviderFailure) as exc:
        failure = {"type": type(exc).__name__}
    meta = metadata(folder / "calls" / f"000-{job['role']}")
    outcome = {k: v for k, v in job.items() if k != "fixture"}
    outcome.update(config_id=config_id, max_tokens=cap, input_characters=len(SYSTEM)+len(prompt),
        dispatched=True, failure=failure, **meta,
        **validate_response(job["role"], response.get("text", ""), meta["finish_reason"], failure))
    save_json(folder / "outcome.json", outcome, immutable=True)
    print(json.dumps({"job": job["job_id"], "valid": outcome["valid"],
        "finish_reason": meta["finish_reason"], "tokens": [meta["input_tokens"], meta["output_tokens"]]}), flush=True)
    return outcome, decode_final(response["text"]) if outcome["valid"] else None


def select_config(outcomes, protocol):
    """Only 36 calibration slots select the cap; acceptance is never used."""
    stats = []
    for config in protocol["configs"]:
        rows = [x for x in outcomes if x["cohort"] == "calibration" and x["config_id"] == config["id"]]
        if len(rows) != 18:
            raise ValueError("Complete both calibration cohorts before selecting.")
        p, c = (sum(x["valid"] for x in rows if x["role"] == r) for r in ("planner", "coder"))
        tokens = [x["input_tokens"] + x["output_tokens"] for x in rows if x.get("usage_complete")]
        stats.append({"id": config["id"], "planner_valid": p, "coder_valid": c,
            "median_tokens": statistics.median(tokens) if len(tokens) == 18 else None})
    chosen = max(stats, key=lambda s: (min(s["planner_valid"], s["coder_valid"]),
        s["planner_valid"]+s["coder_valid"], -(s["median_tokens"] if s["median_tokens"] is not None else math.inf),
        s["id"] == "expanded_caps"))["id"]
    return {"selected_config": chosen, "calibration_statistics": stats, "selection_uses_acceptance": False}


def gates(outcomes):
    counts = {}
    for role in ("planner", "coder"):
        rows = [r for r in outcomes if r["cohort"] == "acceptance" and r["role"] == role]
        counts[role] = {"planned": 18, "observed": len(rows), "valid": sum(r["valid"] for r in rows)}
    e2e = [r for r in outcomes if r["cohort"] == "end_to_end"]
    successes = sum(all(any(r.get("case_id") == f"e2e-t{ti}-r{rep}" and r["role"] == role and r["valid"]
        for r in e2e) for role in ("planner", "coder")) for ti in range(3) for rep in range(2))
    ready = all(v["observed"] == 18 and v["valid"] >= 17 for v in counts.values()) and successes >= 5
    return {"acceptance": counts, "e2e_successes": successes, "e2e_planned": 6, "ready_for_s1": ready}


def run(study, *, transport=None, runtime=True):
    study = Path(study)
    manifest = verify(study, runtime=runtime)
    protocol = manifest["protocol"]
    transport = transport or LazyTransport(protocol)
    outcomes, selected, plans = [], None, {}
    with run_lock(study):
        if (study / "run_result.json").exists():
            return read_json(study / "run_result.json")
        for job in manifest["jobs"]:
            if job["cohort"] != "calibration" and selected is None:
                selected = select_config(outcomes, protocol)
                save_json(study / "selection.json", selected, immutable=True)
            config_id = job["config_id"] if job["cohort"] == "calibration" else selected["selected_config"]
            case = job.get("case_id")
            if case and job["role"] == "coder" and case not in plans:
                outcome = {k: v for k, v in job.items() if k != "fixture"}
                outcome.update(config_id=config_id, dispatched=False, valid=False, complete=False,
                    json_valid=False, schema_valid=False, executable=False, failure=None,
                    validation_reason="skipped_after_invalid_planner")
                save_json(study / "runs" / job["job_id"] / "outcome.json", outcome, immutable=True)
            else:
                outcome, parsed = run_one(study, manifest, job, config_id, transport, plans.get(case))
                if case and job["role"] == "planner" and outcome["valid"]:
                    plans[case] = parsed
            outcomes.append(outcome)
            save_json(study / "progress.json", {"outcomes": outcomes, "completed_slots": len(outcomes),
                "planned_slots": len(manifest["jobs"])})
            if outcome.get("failure"):
                result = {"status": "infrastructure_halted", "after_job": job["job_id"],
                    "completed_slots": len(outcomes), "dispatched_requests": sum(r["dispatched"] for r in outcomes),
                    "no_automatic_retry": True, **gates(outcomes)}
                save_json(study / "run_result.json", result, immutable=True)
                return result
        result = {"status": "complete", "completed_slots": len(outcomes),
            "dispatched_requests": sum(r["dispatched"] for r in outcomes), **selected, **gates(outcomes)}
        save_json(study / "run_result.json", result, immutable=True)
        return result


def wilson(k, n, z=1.959963984540054):
    if not n:
        return None
    d = 1+z*z/n
    c = (k/n + z*z/(2*n))/d
    r = z*math.sqrt((k/n)*(1-k/n)/n+z*z/(4*n*n))/d
    return [c-r, c+r]


def analyze(study, output):
    study, output = Path(study), Path(output)
    manifest = verify(study)
    rows = [read_json(study / "runs" / j["job_id"] / "outcome.json")
            for j in manifest["jobs"] if (study / "runs" / j["job_id"] / "outcome.json").exists()]
    cohorts = []
    for cohort, config, role in sorted({(r["cohort"], r["config_id"], r["role"]) for r in rows}):
        values = [r for r in rows if (r["cohort"], r["config_id"], r["role"]) == (cohort, config, role)]
        dispatched = [r for r in values if r["dispatched"]]
        tokens = [r["input_tokens"]+r["output_tokens"] for r in dispatched if r.get("usage_complete")]
        seconds = [r["seconds"] for r in dispatched if r.get("seconds") is not None]
        k = sum(r["valid"] for r in values)
        cohorts.append({"cohort": cohort, "config_id": config, "role": role,
            "planned": 9 if cohort == "calibration" else (18 if cohort == "acceptance" else 6),
            "observed_slots": len(values), "dispatched": len(dispatched), "valid": k,
            "complete": sum(r["complete"] for r in values), "schema_valid": sum(r["schema_valid"] for r in values),
            "executable": sum(bool(r["executable"]) for r in values) if role == "coder" else None,
            "wilson_95": wilson(k, len(values)), "known_tokens": sum(tokens),
            "usage_complete": len(tokens) == len(dispatched), "median_tokens": statistics.median(tokens) if tokens else None,
            "total_seconds": sum(seconds), "finish_reasons": dict(Counter(r.get("finish_reason") for r in values))})
    total = sum(c["known_tokens"] for c in cohorts)
    summary = {"study_id": manifest["protocol"]["study_id"], "manifest_sha256": manifest["manifest_sha256"],
        "source_commit": manifest["source_commit"], "new_model_calls_by_analysis": 0,
        "result": read_json(study / "run_result.json"), "cohorts": cohorts,
        "known_tokens": total, "total_requests": sum(r["dispatched"] for r in rows),
        "returned_models": sorted({r["returned_model"] for r in rows if r.get("returned_model")}),
        "claim": "Engineering calibration, not search superiority. Synthetic feedback; four feature inputs do not prove universal executability."}
    output.mkdir(parents=True, exist_ok=True)
    save_json(output / "summary.json", summary, immutable=True)
    for name, values in (("cohorts", cohorts), ("outcomes", rows)):
        with (output / f"{name}.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=sorted({k for r in values for k in r}))
            writer.writeheader(); writer.writerows(values)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("freeze"); p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("run"); p.add_argument("--study", type=Path, required=True); p.add_argument("--live", action="store_true", required=True)
    p = sub.add_parser("analyze"); p.add_argument("--study", type=Path, required=True); p.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "run":
        result = run(args.study)
    else:
        with offline_only():
            result = freeze(args.output) if args.command == "freeze" else analyze(args.study, args.output)
    print(json.dumps({k: v for k, v in result.items() if k not in ("source", "jobs", "cohorts")}, ensure_ascii=True))


if __name__ == "__main__":
    main()
