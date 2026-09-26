"""Freeze, run and analyze the MiniMax task-shaped output calibration."""
from __future__ import annotations

import argparse
import ast
import base64
from collections import Counter
import copy
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

from chapter6_demo.discovery import SYSTEM
from chapter6_demo.providers import parse_json
from chapter6_demo.v12_1.audit_r2 import offline_only
from chapter6_demo.v12_2.calls import DurableCalls, HTTPTransport, IndeterminateCall, ProviderFailure
from chapter6_demo.v12_2.common import ROOT, digest, environment, file_sha, git, read_json, run_lock, save_json, source_record, utcnow

HERE = Path(__file__).resolve().parent
PROTOCOL = HERE / "protocol.final.json"
TERMINAL_FAILURES = (IndeterminateCall, ProviderFailure, OSError, TimeoutError)

TAGS = ["local_distance", "return_aware", "regret", "cluster", "lookahead", "progress", "nonlinear", "hybrid"]

CALIBRATION_TEMPLATES = {
    "from_scratch": {
        "parent": "No parent program exists. Propose a first priority rule for the stated constrained interface.",
        "task": "Start from scratch and combine distance and return-aware evidence without using hidden labels."
    },
    "parent_revision": {
        "parent": "Parent program:\ndef priority(f):\n    return -f[\"distance\"]\nParent validation gap: 0.083. Improve this parent while keeping the public interface.",
        "task": "Modify the parent to reduce dead-end choices using one or two available features."
    },
    "reference_revision": {
        "parent": "Parent program:\ndef priority(f):\n    return -f[\"distance\"]\nParent validation gap: 0.083.",
        "reference": "Reference program:\ndef priority(f):\n    return -f[\"distance\"] + 0.25 * f[\"return_distance\"]\nReference validation gap: 0.079.",
        "task": "Use the parent and reference as evidence, then propose an explicitly different but valid direction."
    }
}

ACCEPTANCE_TEMPLATES = {
    "from_scratch": {
        "parent": "No parent program exists. The feature contract is fixed and no hidden evaluation is available.",
        "task": "Create a concise first rule emphasizing regret and cluster density."
    },
    "parent_revision": {
        "parent": "Parent program:\ndef priority(f):\n    return -f[\"distance\"] + 0.20 * f[\"return_distance\"]\nObserved probe loss: 0.071.",
        "task": "Make a bounded revision that uses progress or nearest_remaining without changing the function interface."
    },
    "reference_revision": {
        "parent": "Parent program:\ndef priority(f):\n    return -f[\"distance\"] + 0.20 * f[\"return_distance\"]\nObserved probe loss: 0.071.",
        "reference": "Reference program:\ndef priority(f):\n    return -f[\"distance\"] + 0.30 * f[\"regret\"]\nObserved probe loss: 0.069.",
        "task": "Propose a legal hybrid rule and describe which evidence motivates the change."
    }
}

CODER_PLANS = {
    "from_scratch": {"name": "calibration_distance", "intent": "Use local distance with a small progress tie adjustment.", "tags": ["local_distance", "progress"], "modifications": ["add a bounded progress coefficient"]},
    "parent_revision": {"name": "calibration_return", "intent": "Add return awareness while retaining local distance.", "tags": ["local_distance", "return_aware"], "modifications": ["add a return_distance term"]},
    "reference_revision": {"name": "calibration_regret", "intent": "Blend distance, return distance and regret.", "tags": ["local_distance", "return_aware", "regret"], "modifications": ["add a regret term with a fixed small coefficient"]}
}

E2E_CASES = [
    ("from_scratch", "A fresh route constructor for a clustered instance family."),
    ("from_scratch", "A fresh route constructor for a grid-like instance family."),
    ("parent_revision", "A parent rule that needs a conservative local repair."),
    ("parent_revision", "A parent rule with a return-aware reference."),
    ("reference_revision", "Two valid rules with complementary feature evidence."),
    ("reference_revision", "A final bounded hybrid proposal with explicit intent.")
]


def tooling_files():
    paths = [HERE / "__init__.py", HERE / "protocol.final.json", HERE / "calibration.py"]
    files = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest() for p in paths}
    return {"format": "named-s0-calibration-files-lf-sha256-v1", "files": files, "sha256": digest(files)}


def freeze(output):
    output = Path(output)
    if output.exists():
        raise ValueError("Use a new immutable study directory.")
    protocol = read_json(PROTOCOL)
    manifest = {
        "schema": "chapter6-s0-study-manifest-v1",
        "status": "FROZEN_PENDING_EXECUTION",
        "created_utc": utcnow(),
        "source_commit": git("rev-parse", "HEAD"),
        "source": source_record(),
        "tooling_source": tooling_files(),
        "environment": environment(),
        "protocol": protocol,
        "new_model_calls": 0,
        "study_data": {"calibration_templates_sha256": digest(CALIBRATION_TEMPLATES), "acceptance_templates_sha256": digest(ACCEPTANCE_TEMPLATES), "coder_plans_sha256": digest(CODER_PLANS), "e2e_cases_sha256": digest(E2E_CASES)}
    }
    manifest["manifest_sha256"] = digest(manifest)
    save_json(output / "manifest.json", manifest, immutable=True)
    return manifest


def verify(study):
    manifest = read_json(Path(study) / "manifest.json")
    if digest({k: v for k, v in manifest.items() if k != "manifest_sha256"}) != manifest["manifest_sha256"]:
        raise ValueError("S0 manifest digest mismatch.")
    if manifest["protocol"] != read_json(PROTOCOL):
        raise ValueError("S0 protocol changed after freeze.")
    if manifest["tooling_source"] != tooling_files():
        raise ValueError("S0 source changed after freeze.")
    return manifest


def planner_system():
    return SYSTEM + "\nFor this calibration, output exactly one JSON object and no prose outside it. Required keys: name (string), intent (string), tags (array using only the supplied vocabulary), modifications (array of strings)."


def planner_prompt(template, text, repeat):
    item = CALIBRATION_TEMPLATES[template] if text == "calibration" else ACCEPTANCE_TEMPLATES[template]
    lines = [f"Calibration template: {template}", f"Repeat identifier: {repeat}", item["parent"]]
    if item.get("reference"):
        lines.append(item["reference"])
    lines += ["Task: " + item["task"], "Feature vocabulary: " + ", ".join(TAGS), "Return the required JSON object."]
    return "\n\n".join(lines)


def coder_system():
    return SYSTEM + "\nReturn exactly one JSON object with a code string. The code must define priority(f), use no imports, and be safe to execute on a feature dictionary."


def coder_prompt(template, acceptance=False, repeat=0):
    plans = CODER_PLANS[template]
    label = "acceptance" if acceptance else "calibration"
    return "\n".join(["Coder calibration cohort: " + label, "Repeat identifier: " + str(repeat), "Frozen legal plan:", json.dumps(plans, ensure_ascii=False, indent=2), "Return {\"code\":\"...\"} only."])


def planner_valid(text):
    try:
        value = parse_json(text)
        if not isinstance(value, dict):
            return False, "not_object"
        if any(not isinstance(value.get(k), str if k in ("name", "intent") else list) for k in ("name", "intent", "tags", "modifications")):
            return False, "schema_type"
        if not value["name"].strip() or not value["intent"].strip() or not value["modifications"]:
            return False, "schema_empty"
        if not value["tags"] or any(t not in TAGS for t in value["tags"]):
            return False, "unknown_tag"
        if any(not isinstance(x, str) or not x.strip() for x in value["modifications"]):
            return False, "invalid_modification"
        return True, "valid"
    except Exception as exc:
        return False, type(exc).__name__


def safe_code_valid(code):
    if not isinstance(code, str) or not code.strip():
        return False, "empty_code"
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return False, "syntax_error"
    forbidden = (ast.Import, ast.ImportFrom, ast.Attribute, ast.While, ast.For, ast.AsyncFor, ast.With, ast.AsyncWith, ast.Try, ast.ClassDef, ast.Lambda, ast.Delete, ast.Global, ast.Nonlocal, ast.Call)
    if any(isinstance(node, forbidden) for node in ast.walk(tree)):
        return False, "unsafe_ast"
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "priority"]
    if len(functions) != 1 or any(isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name != "priority" for node in tree.body):
        return False, "wrong_interface"
    try:
        namespace = {"__builtins__": {}}
        exec(compile(tree, "<calibration>", "exec"), namespace, namespace)
        value = namespace["priority"]({"distance": 0.4, "return_distance": 0.2, "regret": 0.1, "progress": 0.5, "nearest_remaining": 0.3, "cluster_density": 0.7})
        if not isinstance(value, (int, float)):
            return False, "non_numeric_return"
    except Exception as exc:
        return False, type(exc).__name__
    return True, "executable"


def validate_response(role, text, failure=None):
    """Validate one persisted response without parsing it more than once.

    Provider failures and malformed model output are both recorded as an
    outcome.  A malformed response must not abort the remaining cohort: every
    dispatched slot is part of the fixed calibration denominator.
    """
    if failure:
        return False, failure.get("type", "provider_failure")
    try:
        value = parse_json(text)
    except Exception as exc:
        return False, type(exc).__name__
    if role == "planner":
        return planner_valid(text)
    if role == "coder":
        if not isinstance(value, dict) or "code" not in value:
            return False, "missing_code"
        return safe_code_valid(value["code"])
    return False, "unknown_role"


def call_metadata(directory):
    directory = Path(directory)
    response = read_json(directory / "response.json") if (directory / "response.json").exists() else {}
    raw = read_json(directory / "raw_response.json") if (directory / "raw_response.json").exists() else {}
    body = json.loads(base64.b64decode(raw["envelope"]["body_base64"])) if raw else {}
    choice = (body.get("choices") or [{}])[0]
    text = response.get("text", "")
    closed = "</think>" in text
    suffix = text.rsplit("</think>", 1)[-1] if closed else text
    return {"finish_reason": choice.get("finish_reason"), "input_tokens": response.get("input_tokens"), "output_tokens": response.get("output_tokens"), "usage_complete": response.get("usage_complete"), "seconds": response.get("seconds"), "returned_model": response.get("returned_model"), "has_think": "<think>" in text, "think_closed": closed, "final_suffix_characters": len(suffix.strip()) if closed else None, "request_id": response.get("request_id")}


def one_call(study, job, step, stage, system, prompt, max_tokens):
    directory = Path(study) / "runs" / job
    config = read_json(directory / "config.json")
    calls = DurableCalls(directory, config, HTTPTransport(config["provider"], config["model"], config["timeout_seconds"]))
    try:
        response = calls.complete(step, stage, system, prompt, max_tokens)
        return response, call_metadata(directory / "calls" / f"{step:03d}-{stage}"), None
    except Exception as exc:
        metadata = call_metadata(directory / "calls" / f"{step:03d}-{stage}") if (directory / "calls" / f"{step:03d}-{stage}" / "response.json").exists() else {"finish_reason": None, "input_tokens": None, "output_tokens": None, "usage_complete": False, "seconds": None, "returned_model": None}
        return None, metadata, {"type": type(exc).__name__, "error": str(exc)[:180]}


def ensure_job(study, job, config_id, cohort):
    directory = Path(study) / "runs" / job
    directory.mkdir(parents=True, exist_ok=True)
    config = {
        "schema": "chapter6-s0-run-config-v1", "job_id": job, "cohort": cohort, "config_id": config_id,
        "provider": "minimax-cn-coding-plan", "model": "MiniMax-M3", "parameters": {"temperature": 0.7},
        "timeout_seconds": 120, "source": verify(study)["tooling_source"], "execution_mode": "live"
    }
    save_json(directory / "config.json", config, immutable=True)
    return directory


def planned_calls(manifest):
    configs = [x["id"] for x in manifest["protocol"]["configs"]]
    result = []
    for cohort, role, templates in [("calibration", "planner", CALIBRATION_TEMPLATES), ("calibration", "coder", CODER_PLANS), ("acceptance", "planner", ACCEPTANCE_TEMPLATES), ("acceptance", "coder", CODER_PLANS)]:
        for config in configs:
            for template in templates:
                for repeat in range(3):
                    result.append({"cohort": cohort, "role": role, "config_id": config, "template": template, "repeat": repeat, "job": f"{cohort}-{role}-{config}-{template}-{repeat}"})
    for index, (template, task) in enumerate(E2E_CASES):
        result.append({"cohort": "end_to_end", "role": "planner", "config_id": "selected_after_acceptance", "template": template, "repeat": index, "job": f"end_to_end-{index}", "task": task})
        result.append({"cohort": "end_to_end", "role": "coder", "config_id": "selected_after_acceptance", "template": template, "repeat": index, "job": f"end_to_end-{index}", "task": task})
    return result


def run(study):
    study = Path(study)
    manifest = verify(study)
    if manifest["status"] != "FROZEN_PENDING_EXECUTION":
        raise ValueError("S0 study is not frozen.")
    root = study / "runs"
    root.mkdir(parents=True, exist_ok=True)
    planned = planned_calls(manifest)
    report = []
    halted = None
    # Calibration and acceptance are fully completed before selecting an
    # end-to-end diagnostic configuration. No model result is used as quality.
    for item in planned:
        if item["cohort"] == "end_to_end":
            continue
        if halted:
            break
        config = next(x for x in manifest["protocol"]["configs"] if x["id"] == item["config_id"])
        directory = ensure_job(study, item["job"], item["config_id"], item["cohort"])
        existing = directory / "outcome.json"
        if existing.exists():
            outcome = read_json(existing)
            report.append(outcome)
            continue
        prompt = coder_prompt(item["template"], item["cohort"] == "acceptance", item["repeat"]) if item["role"] == "coder" else planner_prompt(item["template"], item["cohort"], item["repeat"])
        system = coder_system() if item["role"] == "coder" else planner_system()
        max_tokens = config["coder_max_tokens"] if item["role"] == "coder" else config["planner_max_tokens"]
        response, metadata, failure = one_call(study, item["job"], 0, item["role"], system, prompt, max_tokens)
        text_value = response.get("text", "") if response else ""
        valid, reason = validate_response(item["role"], text_value, failure)
        outcome = {**item, "max_tokens": max_tokens, "valid": valid, "validation_reason": reason, "failure": failure, **metadata}
        save_json(existing, outcome, immutable=True)
        report.append(outcome)
        if failure and failure["type"] == "IndeterminateCall":
            halted = {"reason": "indeterminate_request", "job": item["job"]}
    # Selection and end-to-end are deterministic from saved outcomes.
    save_json(study / "calibration_progress.json", {"status": "complete" if not halted else "halted", "halt": halted, "outcomes": report}, immutable=True)
    selected = select_config(report, manifest["protocol"])
    if halted:
        return {"status": "halted", "selected_config": selected, "outcomes": len(report)}
    for index, (template, task) in enumerate(E2E_CASES):
        config = next(x for x in manifest["protocol"]["configs"] if x["id"] == selected)
        job = f"end_to_end-{index}"
        directory = ensure_job(study, job, selected, "end_to_end")
        prompt = planner_prompt(template, "acceptance", index) + "\nSpecific diagnostic case: " + task
        planner_outcome_path = directory / "planner_outcome.json"
        if planner_outcome_path.exists():
            planner_outcome = read_json(planner_outcome_path)
        else:
            response, metadata, failure = one_call(study, job, 0, "planner", planner_system(), prompt, config["planner_max_tokens"])
            planner_ok, planner_reason = validate_response("planner", response.get("text", "") if response else "", failure)
            planner_outcome = {"job": job, "config_id": selected, "template": template, "valid": planner_ok, "validation_reason": planner_reason, "failure": failure, **metadata}
            save_json(planner_outcome_path, planner_outcome, immutable=True)
        coder_outcome_path = directory / "coder_outcome.json"
        if coder_outcome_path.exists():
            coder_outcome = read_json(coder_outcome_path)
            coder_failure = coder_outcome.get("failure")
        else:
            coder_response, coder_metadata, coder_failure = one_call(study, job, 1, "coder", coder_system(), coder_prompt(template, True, index), config["coder_max_tokens"])
            coder_ok, coder_reason = validate_response("coder", coder_response.get("text", "") if coder_response else "", coder_failure)
            coder_outcome = {"job": job, "config_id": selected, "template": template, "valid": coder_ok, "validation_reason": coder_reason, "failure": coder_failure, **coder_metadata}
            save_json(coder_outcome_path, coder_outcome, immutable=True)
        if coder_failure and coder_failure["type"] == "IndeterminateCall":
            halted = {"reason": "indeterminate_request", "job": job}
            break
    save_json(study / "selection.json", {"selected_config": selected, "selection_rule": manifest["protocol"]["selection_rule"], "calibration_not_ready": not gates_pass(report), "e2e_started": True, "halt": halted}, immutable=True)
    return {"status": "complete" if not halted else "halted", "selected_config": selected, "outcomes": len(report), "e2e_cases": len(E2E_CASES)}


def select_config(outcomes, protocol):
    summaries = {}
    for config in protocol["configs"]:
        p = [x for x in outcomes if x["cohort"] == "acceptance" and x["role"] == "planner" and x["config_id"] == config["id"]]
        c = [x for x in outcomes if x["cohort"] == "acceptance" and x["role"] == "coder" and x["config_id"] == config["id"]]
        summaries[config["id"]] = {"planner_valid": sum(x["valid"] for x in p), "coder_valid": sum(x["valid"] for x in c), "coder_executable": sum(x["valid"] for x in c), "tokens": [x["input_tokens"] + x["output_tokens"] for x in p+c if x["input_tokens"] is not None and x["output_tokens"] is not None]}
    passers = [config["id"] for config in protocol["configs"] if summaries[config["id"]]["planner_valid"] >= 17 and summaries[config["id"]]["coder_valid"] >= 17]
    if len(passers) == 1:
        return passers[0]
    if len(passers) > 1:
        return min(passers, key=lambda x: statistics.median(summaries[x]["tokens"]) if summaries[x]["tokens"] else float("inf"))
    return "expanded_caps"


def gates_pass(outcomes):
    for config in ("legacy_caps", "expanded_caps"):
        p = [x for x in outcomes if x["cohort"] == "acceptance" and x["role"] == "planner" and x["config_id"] == config]
        c = [x for x in outcomes if x["cohort"] == "acceptance" and x["role"] == "coder" and x["config_id"] == config]
        if len(p) == 18 and len(c) == 18 and sum(x["valid"] for x in p) >= 17 and sum(x["valid"] for x in c) >= 17:
            return True
    return False


def wilson(successes, total, z=1.959963984540054):
    if not total:
        return None
    phat = successes / total
    denom = 1 + z*z/total
    centre = (phat + z*z/(2*total)) / denom
    radius = z * ((phat*(1-phat)/total + z*z/(4*total*total)) ** 0.5) / denom
    return [centre-radius, centre+radius]


def analyze(study, output):
    study, output = Path(study), Path(output)
    manifest = verify(study)
    progress = read_json(study / "calibration_progress.json")
    outcomes = progress["outcomes"]
    e2e = []
    for directory in sorted((study / "runs").glob("end_to_end-*")):
        if (directory / "planner_outcome.json").exists():
            e2e.append(read_json(directory / "planner_outcome.json"))
        if (directory / "coder_outcome.json").exists():
            e2e.append(read_json(directory / "coder_outcome.json"))
    rows = []
    for config in manifest["protocol"]["configs"]:
        for cohort in ("calibration", "acceptance"):
            for role in ("planner", "coder"):
                values = [x for x in outcomes if x["config_id"] == config["id"] and x["cohort"] == cohort and x["role"] == role]
                success = sum(x["valid"] for x in values)
                rows.append({"config_id": config["id"], "cohort": cohort, "role": role, "planned": len(values), "valid": success, "fraction": success/len(values) if values else None, "wilson_95": wilson(success, len(values)), "known_tokens": sum((x["input_tokens"] or 0)+(x["output_tokens"] or 0) for x in values), "finish_reasons": dict(Counter(x.get("finish_reason") for x in values)), "returned_models": sorted({x.get("returned_model") for x in values if x.get("returned_model")})})
    selected = read_json(study / "selection.json") if (study / "selection.json").exists() else None
    summary = {"study_id": manifest["protocol"]["study_id"], "manifest_sha256": manifest["manifest_sha256"], "analysis_utc": utcnow(), "new_model_calls_by_analysis": 0, "outcomes": len(outcomes), "e2e_outcomes": len(e2e), "selection": selected, "calibration_not_ready": not gates_pass(outcomes), "claim": "Output-readiness engineering calibration only; no search-quality claim.", "rows": rows}
    output.mkdir(parents=True)
    save_json(output / "summary.json", summary, immutable=True)
    save_json(output / "manifest_binding.json", {"manifest_sha256": manifest["manifest_sha256"], "source_commit": manifest["source_commit"], "new_model_calls_by_analysis": 0}, immutable=True)
    import csv
    with (output / "cohorts.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0])); writer.writeheader(); writer.writerows(rows)
    with (output / "outcomes.csv").open("w", encoding="utf-8", newline="") as stream:
        fields = sorted({k for x in outcomes+e2e for k in x})
        writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader(); writer.writerows(outcomes+e2e)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("freeze"); p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("run"); p.add_argument("--study", type=Path, required=True); p.add_argument("--live", action="store_true", required=True)
    p = sub.add_parser("analyze"); p.add_argument("--study", type=Path, required=True); p.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "freeze":
        with offline_only(): result = freeze(args.output)
        print(json.dumps({"status": result["status"], "manifest_sha256": result["manifest_sha256"]}))
    elif args.command == "run":
        result = run(args.study)
        print(json.dumps(result))
    else:
        with offline_only(): result = analyze(args.study, args.output)
        print(json.dumps({k: v for k, v in result.items() if k != "rows"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
