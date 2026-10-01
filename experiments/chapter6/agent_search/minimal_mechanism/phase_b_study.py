"""Offline-first orchestration for the Chapter 6 phase-B diagnostic."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import random
import shutil
import time
from pathlib import Path

from chapter6_demo import benchmarks
from chapter6_demo.benchmarks import SEEDS
from chapter6_demo.v12_1.audit_r2 import offline_only
from chapter6_demo.v12_2.common import (
    digest, environment, file_sha, git, read_json, save_json, source_record, utcnow,
)
from chapter6_demo.v12_2.data import content_hash
from chapter6_demo.v12_2.calls import ProviderFailure

from ..component_validation.service import (
    DiagnosticHTTPTransport, GlobalPauseGate,
)
from ..s3_tsp_r3.runner import run_search
from .freeze_sources import verify_index as verify_source_hash_index
from .inventory import verify_source_index
from .phase_b_runner import (
    TERMINAL as CONTINUATION_TERMINAL,
    run_phase_b,
)


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
PROTOCOL_PATH = HERE / "protocol.b.json"
PREFIX_TERMINAL = {
    "search_complete_test_not_run", "infrastructure_incomplete", "budget_exhausted",
    "sent_unknown", "provider_failed", "not_started",
}
TEST_ELIGIBLE = {"continuation_complete", "infrastructure_incomplete", "budget_exhausted",
                 "branch_unavailable", "preparation_incomplete", "not_started",
                 "sent_unknown", "provider_failed"}
STRATEGIES = ("I", "B", "E0", "EG")


def tooling_source() -> dict:
    paths = [
        HERE / "phase_b_study.py", HERE / "phase_b_runner.py", HERE / "controller.py",
        HERE / "protocol.b.json", HERE / "inventory.py", HERE / "DATA_INVENTORY.json",
        HERE / "test_protocol_b.py", HERE / "test_phase_b_study.py",
        HERE / "test_phase_b_runner.py", HERE / "test_controller.py",
        HERE / "test_inventory.py", HERE / "analyze_phase_b.py", HERE / "test_analyze_phase_b.py",
        HERE / "freeze_sources.py", HERE / "SOURCE_SHA256.json",
        HERE / "METHOD_ZH.md", HERE / "PROTOCOL_ZH.md", HERE / "BUDGET_ZH.md",
        HERE / "REPORT_ZH.md", HERE / "HISTORY_AUDIT_ZH.md", HERE / "HISTORY_AUDIT.json",
        HERE / "demo_real.json", HERE / "DATA_SOURCE_SHA256.tsv",
        ROOT / "experiments/chapter6/agent_search/component_validation/e1_runner.py",
        ROOT / "experiments/chapter6/agent_search/component_validation/service.py",
        ROOT / "experiments/chapter6/agent_search/s3_tsp_r3/runner.py",
        ROOT / "experiments/chapter6/agent_search/s3_tsp_r3/controller.py",
        ROOT / "experiments/chapter6/agent_search/s3_tsp_r3/evaluator.py",
        ROOT / "experiments/chapter6/demo/benchmarks.py",
        ROOT / "experiments/chapter6/demo/discovery.py",
        ROOT / "experiments/chapter6/demo/providers.py",
    ]
    source_index = verify_source_hash_index()
    files = {path.relative_to(ROOT).as_posix(): file_sha(path) for path in paths}
    return {"format": "chapter6-phase-b-source-files-sha256-v1", "files": files,
            "source_hash_index": source_index,
            "sha256": digest({"files": files, "source_hash_index": source_index})}


def _validate_protocol(protocol: dict) -> None:
    if protocol.get("schema") != "chapter6-minimal-mechanism-protocol-v1":
        raise ValueError("unexpected phase-B protocol schema")
    stage = protocol.get("stages", {}).get("B", {})
    model = protocol.get("model", {})
    data = protocol.get("data", {})
    if (data.get("adapter_counts_per_family") != {"probe": 4, "validation": 12, "test": 20}
            or data.get("adapter_split_counts_per_block") != {"probe": 12, "validation": 36, "test": 60}
            or data.get("search_evaluations_per_proposal") != 48
            or data.get("test_evaluations_per_candidate") != 60):
        raise ValueError("phase-B evaluation counts differ from the committed TSP14 adapter")
    required = {
        "blocks": list(range(60, 68)),
        "actions": list(STRATEGIES), "repetitions_per_action_checkpoint": 2,
        "checkpoints_planned": 16, "continuation_jobs_planned": 128,
        "proposals_per_job_max": 8, "max_continuation_proposals": 1024,
        "max_public_prefix_proposals": 192, "max_requests": 2432, "max_tokens": 14800000,
    }
    for key, expected in required.items():
        if stage.get(key) != expected:
            raise ValueError(f"phase-B protocol mismatch for {key}")
    prefix = stage.get("public_prefix", {})
    if (prefix.get("jobs") != 8 or prefix.get("checkpoints") != [8, 24]
            or prefix.get("policy") != "SP incumbent-only"
            or prefix.get("proposals_per_block_max") != 24):
        raise ValueError("phase-B public-prefix policy or checkpoints changed")
    branch_rule = stage.get("branch_selection", {})
    if (branch_rule.get("eligible") != "valid non-identical program with validation loss difference in (0, 0.035] and probe behavior distance > 0.08"
            or branch_rule.get("winner") != "minimum validation loss, then minimum node id"
            or branch_rule.get("behavior_distance") != "chapter6_demo.benchmarks.behavior_distance over aligned probe behavior descriptors; strict threshold > 0.08"
            or branch_rule.get("test_access") is not False):
        raise ValueError("phase-B branch selection rule changed")
    if stage.get("shared_seed_programs_per_prefix") != len(SEEDS["tsp"]):
        raise ValueError("phase-B shared seed count differs from the committed SP runner")
    b_expected_search = (stage["max_continuation_proposals"] + stage["max_public_prefix_proposals"]) * 48
    b_expected_seeds = len(stage["blocks"]) * len(SEEDS["tsp"]) * 48
    if (stage.get("search_proposal_evaluation_cap") != b_expected_search
            or stage.get("shared_seed_evaluation_cap") != b_expected_seeds
            or stage.get("search_instance_evaluation_cap") != b_expected_search + b_expected_seeds
            or stage.get("test_candidate_program_cap") != 288
            or stage.get("test_instance_evaluation_cap") != 288 * 60):
        raise ValueError("phase-B evaluation resource caps do not match the adapter workload")
    stage_c = protocol["stages"]["C"]
    c_proposals = stage_c["searches"] * stage_c["proposals_per_search_safety_max"] * 48
    c_seeds = stage_c["searches"] * len(SEEDS["tsp"]) * 48
    c_search = c_proposals + c_seeds
    if (stage_c.get("search_proposal_evaluation_cap") != c_proposals
            or stage_c.get("shared_seed_evaluation_cap") != c_seeds
            or stage_c.get("search_instance_evaluation_cap") != c_search
            or stage_c.get("test_program_cap") != stage_c["searches"]
            or stage_c.get("test_instance_evaluation_cap") != stage_c["searches"] * 60):
        raise ValueError("phase-C evaluation resource caps do not match the adapter workload")
    stage_d = protocol["stages"]["D"]
    if (stage_d.get("search_instance_evaluation_cap_per_block") != 8 * (64 + len(SEEDS["tsp"])) * 48
            or stage_d.get("test_instance_evaluation_cap_per_block") != 8 * 60):
        raise ValueError("phase-D per-block evaluation resource caps do not match the adapter workload")
    generation = protocol.get("generation", {})
    for key in ("temperature", "planner_max_tokens", "coder_max_tokens", "timeout_seconds"):
        value = generation.get(key)
        if not isinstance(value, (int, float)) or value <= 0:
            raise ValueError(f"missing frozen generation parameter: {key}")
    if generation.get("wall_deadline_policy") != (
            "Before each request, use the absolute task wall deadline; after pacing, provider timeout is "
            "min(180 seconds, remaining task time), and no request starts after the deadline."):
        raise ValueError("phase-B wall deadline policy changed")
    if not model.get("requested_model") or not model.get("provider"):
        raise ValueError("provider and requested model must be frozen")
    if model.get("max_concurrency") != 1 or model.get("retry_policy") != "none":
        raise ValueError("phase B requires serial dispatch and no automatic retries")


def _inventory_check() -> dict:
    """Verify the saved full-corpus inventory and the selected new block pool."""
    inventory_path = HERE / "DATA_INVENTORY.json"
    inventory = read_json(inventory_path)
    stored_hash = inventory.get("inventory_sha256")
    if digest({key: value for key, value in inventory.items() if key != "inventory_sha256"}) != stored_hash:
        raise ValueError("data inventory digest mismatch")
    if not inventory.get("scan", {}).get("complete"):
        raise ValueError("data inventory has unreadable JSON or ZIP sources")
    source_index = verify_source_index(expected=inventory.get("data_source_index"))
    rows = inventory.get("candidate_blocks", {}).get("exact_coordinate_collisions_vs_blocks_3_59_and_between_candidates", [])
    archive_rows = inventory.get("candidate_blocks", {}).get("exact_coordinate_collisions_vs_repository_json_and_zip_json", [])
    if rows or archive_rows:
        raise ValueError("candidate block pool has an exact coordinate collision")
    claimed_candidates = set(inventory.get("manifest_scan", {}).get("claimed_candidate_blocks", [])) & set(range(60, 68))
    if claimed_candidates:
        raise ValueError(f"candidate blocks are already claimed by a historical batch: {sorted(claimed_candidates)}")
    blocks = set(inventory.get("candidate_blocks", {}).get("groups", {})
                 .get("stage_b_diagnostic", {}).get("blocks", []))
    if blocks != set(range(60, 68)):
        raise ValueError("inventory does not reserve exactly blocks 60-67 for B")
    return {"path": inventory_path.relative_to(ROOT).as_posix(),
            "sha256": file_sha(inventory_path), "inventory_sha256": stored_hash,
            "data_source_index": source_index,
            "full_manifest_scan": True, "all_zip_json_scanned": True,
            "historical_blocks_scanned": [0, 59], "candidate_blocks": list(range(60, 68)),
            "exact_coordinate_collisions": 0, "claimed_candidate_blocks": []}


def _data_overlap_check(blocks: list[int]) -> dict:
    old_ids, old_hashes = set(), set()
    for block in range(3, 60):
        for split in ("probe", "validation", "test"):
            for item in benchmarks._instances_cached("tsp", split, benchmarks.V12_TSP_PROFILE, block):
                old_ids.add(item["id"])
                old_hashes.add(content_hash(item))
    seen_ids, seen_hashes = set(old_ids), set(old_hashes)
    checked = 0
    for block in blocks:
        for split in ("probe", "validation"):
            for item in benchmarks._instances_cached("tsp", split, benchmarks.V12_TSP_PROFILE, block):
                hashed = content_hash(item)
                if item["id"] in seen_ids or hashed in seen_hashes:
                    raise ValueError(f"phase-B data overlap at {block}/{split}/{item['id']}")
                seen_ids.add(item["id"])
                seen_hashes.add(hashed)
                checked += 1
    return {"old_instance_count": len(old_ids), "new_search_instance_count": checked,
            "candidate_test_splits_deferred_until_global_gate": True,
            "id_or_exact_coordinate_collisions": 0, "geometric_equivalence_checked": False}


def prefix_jobs(protocol: dict) -> list[dict]:
    _validate_protocol(protocol)
    stage = protocol["stages"]["B"]
    generation = protocol["generation"]
    model = protocol["model"]
    jobs = []
    for block in stage["blocks"]:
        jobs.append({
            "job_id": f"prefix-sp-b{block}", "provider": model["provider"],
            "model": model["requested_model"], "task": "tsp", "data_block": block,
            "search_seed": block * 1000, "search_seed_label": 0, "arm_id": "SP",
            "policy": "SP", "protection": False, "role": "public_prefix",
            "steps": 24, "token_budget": stage["token_limit_prefix_per_job"],
            "request_limit": 48, "wall_limit_seconds": stage["wall_limit_prefix_job_seconds"],
            "capacity": 6, "grant": 2, "maximum_direction_attempts": 8,
            "quality_tolerance": 0.035, "gain_epsilon": 0.0001,
            "behavior_radius": 0.08, "adaptive_unit_steps": 2,
            "temperature": generation["temperature"],
            "planner_max_tokens": generation["planner_max_tokens"],
            "coder_max_tokens": generation["coder_max_tokens"],
            "timeout_seconds": generation["timeout_seconds"],
        })
    random.Random(stage["dispatch_order_seed"]).shuffle(jobs)
    return jobs


def continuation_jobs(protocol: dict) -> list[dict]:
    stage = protocol["stages"]["B"]
    model = protocol["model"]
    jobs = []
    for block in stage["blocks"]:
        for prefix_step in stage["public_prefix"]["checkpoints"]:
            checkpoint_id = f"b{block}-step{prefix_step:02d}"
            for strategy in stage["actions"]:
                for repetition in range(stage["repetitions_per_action_checkpoint"]):
                    jobs.append({
                        "job_id": f"{strategy.lower()}-{checkpoint_id}-r{repetition}",
                        "strategy": strategy, "checkpoint_id": checkpoint_id,
                        "provider": model["provider"], "model": model["requested_model"],
                        "data_block": block, "public_prefix_steps": prefix_step,
                        "repetition": repetition, "steps": 8,
                    })
    random.Random(stage["dispatch_order_seed"] + 1).shuffle(jobs)
    return jobs


def prepare(output: Path) -> dict:
    """Create an offline draft: frozen candidate data and planned job IDs only."""
    output = Path(output).resolve()
    if output.exists():
        raise ValueError("Use a new phase-B draft directory")
    protocol = read_json(PROTOCOL_PATH)
    _validate_protocol(protocol)
    inventory = _inventory_check()
    overlap = _data_overlap_check(protocol["stages"]["B"]["blocks"])
    output.mkdir(parents=True)
    data_records, block_split_counts = [], {}
    for block in protocol["stages"]["B"]["blocks"]:
        search = {"profile": benchmarks.V12_TSP_PROFILE, "block": block,
                  "probe": copy.deepcopy(benchmarks._instances_cached(
                      "tsp", "probe", benchmarks.V12_TSP_PROFILE, block)),
                  "validation": copy.deepcopy(benchmarks._instances_cached(
                      "tsp", "validation", benchmarks.V12_TSP_PROFILE, block))}
        split_counts = {"probe": len(search["probe"]), "validation": len(search["validation"])}
        expected_search_counts = {key: protocol["data"]["adapter_split_counts_per_block"][key]
                                  for key in ("probe", "validation")}
        if split_counts != expected_search_counts:
            raise ValueError(f"TSP14 search split counts differ from protocol at block {block}: {split_counts}")
        block_split_counts[str(block)] = split_counts
        rel = f"data/search-b{block}.json"
        save_json(output / rel, search, immutable=True)
        data_records.append({"block": block, "role": "search", "path": rel,
                             "sha256": file_sha(output / rel),
                             "instance_count": len(search["probe"]) + len(search["validation"])})
    manifest = {
        "schema": "chapter6-phase-b-diagnostic-study-v1",
        "status": "DRAFT_NOT_EXECUTABLE", "created_utc": utcnow(),
        "study_id": "chapter6-phase-b-diagnostic-20260930",
        "basis_commit": protocol["basis_commit"], "source_commit": git("rev-parse", "HEAD"),
        "source": source_record(), "tooling_source": tooling_source(),
        "environment": environment(), "protocol": protocol,
        "protocol_path": PROTOCOL_PATH.relative_to(ROOT).as_posix(),
        "protocol_sha256": file_sha(PROTOCOL_PATH), "inventory_check": inventory,
        "overlap_check": overlap, "data": data_records,
        "evaluation_counts": {"split_counts_per_block": block_split_counts,
                              "search_instances_per_proposal": protocol["data"]["search_evaluations_per_proposal"],
                              "test_instances_per_candidate": protocol["data"]["test_evaluations_per_candidate"],
                              "shared_seed_programs": len(SEEDS["tsp"]),
                              "shared_seed_proposals": 0},
        "prefix_jobs": prefix_jobs(protocol), "jobs": continuation_jobs(protocol),
        "planned_checkpoints": 16, "planned_prefix_jobs": 8,
        "planned_continuation_jobs": 128, "new_model_calls": 0,
        "test_access": False,
        "note": "offline search data preparation only; Test values remain unopened until all continuation tasks are terminal and validation selections are frozen",
    }
    manifest["manifest_sha256"] = digest(manifest)
    save_json(output / "manifest.json", manifest, immutable=True)
    return manifest


def verify(study: Path, *, frozen: bool = False) -> dict:
    study = Path(study).resolve()
    manifest = read_json(study / "manifest.json")
    if digest({key: value for key, value in manifest.items() if key != "manifest_sha256"}) != manifest.get("manifest_sha256"):
        raise ValueError("phase-B study manifest digest mismatch")
    if file_sha(ROOT / manifest["protocol_path"]) != manifest["protocol_sha256"]:
        raise ValueError("phase-B protocol changed after preparation")
    if manifest.get("protocol") != read_json(PROTOCOL_PATH):
        raise ValueError("phase-B manifest does not embed the committed protocol")
    if manifest.get("source_commit") != git("rev-parse", "HEAD"):
        raise ValueError("phase-B source commit changed after preparation")
    if manifest.get("source") != source_record() or manifest.get("tooling_source") != tooling_source():
        raise ValueError("phase-B source files changed; create a new study version")
    if manifest.get("inventory_check") != _inventory_check():
        raise ValueError("full data inventory changed after preparation")
    if manifest.get("prefix_jobs") != prefix_jobs(manifest["protocol"]):
        raise ValueError("phase-B public-prefix matrix changed")
    if manifest.get("jobs") != continuation_jobs(manifest["protocol"]):
        raise ValueError("phase-B continuation matrix changed")
    if frozen and manifest.get("status") != "FROZEN_PENDING_EXECUTION":
        raise ValueError("phase-B study is not frozen")
    for rec in manifest["data"]:
        path = (study / rec["path"]).resolve()
        if not path.is_relative_to(study) or file_sha(path) != rec["sha256"]:
            raise ValueError(f"phase-B data snapshot changed: {rec['path']}")
    return manifest


def freeze(draft: Path, output: Path) -> dict:
    draft, output = Path(draft).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Use a new phase-B frozen directory")
    manifest = verify(draft)
    if git("status", "--porcelain"):
        raise ValueError("phase-B freeze requires committed source files")
    output.mkdir(parents=True)
    for rec in manifest["data"]:
        target = output / rec["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(draft / rec["path"], target)
    frozen = copy.deepcopy(manifest)
    frozen["status"] = "FROZEN_PENDING_EXECUTION"
    frozen["created_utc"] = utcnow()
    frozen.pop("manifest_sha256", None)
    frozen["manifest_sha256"] = digest(frozen)
    save_json(output / "manifest.json", frozen, immutable=True)
    return frozen


def _acceptance_record(path: Path, expected_model: str) -> dict:
    path = Path(path)
    summary_path = path / "summary.json" if path.is_dir() else path
    summary = read_json(summary_path)
    if summary.get("status") != "passed" or summary.get("returned_model") != expected_model:
        raise ValueError("provider workload acceptance is not passed for the frozen model")
    workload = summary.get("workload") or summary.get("acceptance_workload") or {}
    if int(workload.get("planner_completed", 0)) < 3 or int(workload.get("coder_completed", 0)) < 3:
        raise ValueError("phase B requires at least three accepted planner and coder calls")
    return {"path": str(summary_path.resolve()), "sha256": file_sha(summary_path),
            "status": summary["status"], "returned_model": summary["returned_model"],
            "planner_completed": int(workload["planner_completed"]),
            "coder_completed": int(workload["coder_completed"])}


def _authorization(study: Path, manifest: dict, authorization_path: Path,
                   stage: str) -> dict:
    """Check a separately issued authorization before any transport is created."""
    auth = read_json(authorization_path)
    phase = "public_prefix" if stage == "prefix" else "continuation"
    limits = manifest["protocol"]["stages"]["B"]["authorization_caps"][phase]
    planned_requests = int(limits["max_requests"])
    planned_tokens = int(limits["max_tokens"])
    if auth.get("status") != "approved" or auth.get("study_manifest_sha256") != manifest["manifest_sha256"]:
        raise ValueError("authorization must be approved and bound to this frozen manifest")
    if phase not in auth.get("approved_stages", []):
        raise ValueError(f"authorization does not include {phase}")
    if auth.get("requested_model") != manifest["protocol"]["model"]["requested_model"]:
        raise ValueError("authorization model differs from frozen protocol")
    if int(auth.get("max_requests", 0)) < planned_requests or int(auth.get("max_tokens", 0)) < planned_tokens:
        raise ValueError(f"authorization does not cover the frozen {phase} worst-case budget")
    if int(auth.get("max_requests", 0)) > planned_requests or int(auth.get("max_tokens", 0)) > planned_tokens:
        raise ValueError(f"authorization exceeds the protocol's {phase} budget ceiling")
    return {"path": str(Path(authorization_path).resolve()), "sha256": file_sha(authorization_path),
            "study_manifest_sha256": manifest["manifest_sha256"], "approved_stage": phase,
            "max_requests": int(auth["max_requests"]), "max_tokens": int(auth["max_tokens"])}


def _write_status(path: Path, status: str, **details) -> dict:
    value = {"status": status, "updated_utc": utcnow(), **details}
    save_json(path, value, immutable=True)
    return value


def _canonical_terminal(run_dir: Path, raw_status: str, *, prefix: bool = False) -> dict:
    """Expose sent requests with unknown cost as missing outcomes, never zero."""
    sent_unknown, provider_failed, unknown_usage = [], [], []
    call_dir = Path(run_dir) / "calls"
    if call_dir.exists():
        for state_path in sorted(call_dir.glob("*/state.json")):
            state = read_json(state_path)
            row = {"call": state_path.parent.name, "request_sha256": state.get("request_sha256")}
            if state.get("status") == "sent_unknown":
                sent_unknown.append(row)
            elif state.get("status") == "provider_failed":
                provider_failed.append(row)
            elif state.get("status") == "response_persisted":
                response_path = state_path.parent / "response.json"
                response = read_json(response_path) if response_path.exists() else {}
                if not response.get("usage_complete"):
                    unknown_usage.append(row)
    terminal = "sent_unknown" if sent_unknown else "provider_failed" if provider_failed else raw_status
    details = {"underlying_runner_status": raw_status,
               "attempted_calls": sent_unknown + provider_failed,
               "unknown_usage_calls": unknown_usage,
               "unknown_cost": bool(sent_unknown or provider_failed or unknown_usage),
               "missing_outcome": bool(sent_unknown or provider_failed or unknown_usage),
               "outcome_counted_as_zero": False, "no_automatic_retry": True}
    allowed = PREFIX_TERMINAL if prefix else TEST_ELIGIBLE
    if terminal not in allowed:
        terminal = "infrastructure_incomplete"
        details["unrecognized_runner_status"] = raw_status
    return _write_status(Path(run_dir) / "terminal_status.json", terminal, **details)


def _snapshot_by_block(study: Path, manifest: dict, block: int, role: str) -> dict:
    if role != "search":
        raise ValueError("search dispatch cannot access a Test snapshot")
    rec = next(r for r in manifest["data"] if r["block"] == block and r["role"] == role)
    snapshot = read_json(study / rec["path"])
    if "test" in snapshot:
        raise ValueError("Test data supplied to search dispatch")
    return snapshot


def _latest_diagnostics(run_dir: Path) -> dict | None:
    diagnostics_path = Path(run_dir) / "service_diagnostics.json"
    diagnostics = read_json(diagnostics_path).get("diagnostics") if diagnostics_path.exists() else None
    call_dir = Path(run_dir) / "calls"
    if call_dir.exists():
        for state_path in sorted(call_dir.glob("*/state.json")):
            diagnostics = read_json(state_path).get("diagnostics") or diagnostics
    return diagnostics


def _provider_gate(gate: GlobalPauseGate, run_dir: Path, status: str) -> dict:
    diagnostics = _latest_diagnostics(run_dir)
    decision = gate.observe(diagnostics)
    category = (diagnostics or {}).get("error_category")
    if (status in {"infrastructure_incomplete", "sent_unknown", "provider_failed"}
            and category != "rate_limit" and not decision["pause"]):
        decision["pause"] = True
        decision["reason"] = "unclassified infrastructure interruption"
    return decision


class _TrackedDiagnosticTransport(DiagnosticHTTPTransport):
    def __init__(self, provider, model, timeout, **kwargs):
        super().__init__(provider, model, timeout, **kwargs)
        self._maximum_timeout = float(timeout)
        self._wall_deadline = None
        self.last_diagnostics = None

    def set_wall_deadline(self, deadline: float) -> None:
        self._wall_deadline = float(deadline)

    def send(self, request, persist):
        if self._wall_deadline is not None:
            self._pace()
            remaining = self._wall_deadline - time.perf_counter()
            if remaining <= 0:
                raise TimeoutError("task wall deadline expired before request dispatch")
            client = self.client
            old_timeout, old_interval = client.timeout, self.min_interval_seconds
            client.timeout = min(self._maximum_timeout, remaining)
            self.min_interval_seconds = 0.0
            try:
                return super().send(request, persist)
            finally:
                client.timeout = old_timeout
                self.min_interval_seconds = old_interval
        try:
            return super().send(request, persist)
        except ProviderFailure as exc:
            self.last_diagnostics = getattr(exc, "diagnostics", None)
            raise


def _persist_transport_diagnostics(run_dir: Path, transport) -> None:
    value = getattr(transport, "last_diagnostics", None)
    if value:
        save_json(Path(run_dir) / "service_diagnostics.json",
                  {"diagnostics": value, "persisted_after_dispatch": True}, immutable=True)


def _run_prefix_with_budget(job: dict, snapshot: dict, run_dir: Path,
                            binding: dict, parameters: dict, transport):
    """Add a conservative pre-dispatch token reservation to the frozen S3 runner."""
    from ..s3_tsp_r3 import runner as s3_runtime

    if "test" in snapshot:
        raise ValueError("Test data supplied to public-prefix runner")
    base_calls = s3_runtime.DurableCalls
    checkpoint_path = Path(run_dir) / "checkpoint.json"
    prior_elapsed = (float(read_json(checkpoint_path).get("elapsed_seconds", 0.0))
                     if checkpoint_path.exists() else 0.0)
    started = time.perf_counter() - prior_elapsed

    class BudgetedCalls(base_calls):
        def complete(self, step, stage, system, prompt, max_tokens):
            usage = self.usage()
            attempts = int(usage.get("call_attempts", 0))
            if attempts and not usage.get("usage_complete"):
                raise s3_runtime.BudgetStop("token_usage_incomplete")
            if attempts >= int(parameters["request_limit"]):
                raise s3_runtime.BudgetStop("request_limit")
            prompt_byte_upper_bound = len(system.encode("utf-8")) + len(prompt.encode("utf-8"))
            reserve = prompt_byte_upper_bound + 512 + int(max_tokens)
            if int(usage.get("known_tokens", 0)) + reserve > int(parameters["token_budget"]):
                raise s3_runtime.BudgetStop("token_budget_reservation")
            if time.perf_counter() - started >= int(parameters["wall_limit_seconds"]):
                raise s3_runtime.BudgetStop("wall_limit")
            if not callable(getattr(transport, "set_wall_deadline", None)):
                raise ValueError("live transport must enforce the frozen wall deadline")
            transport.set_wall_deadline(started + float(parameters["wall_limit_seconds"]))
            response = super().complete(step, stage, system, prompt, max_tokens)
            updated = self.usage()
            if not updated.get("usage_complete"):
                raise s3_runtime.BudgetStop("token_usage_incomplete")
            if int(updated.get("known_tokens", 0)) > int(parameters["token_budget"]):
                raise s3_runtime.BudgetStop("token_budget_overrun_reported_by_provider")
            return response

    s3_runtime.DurableCalls = BudgetedCalls
    try:
        return run_search(job, snapshot, run_dir, binding, parameters, transport, mode="live")
    finally:
        s3_runtime.DurableCalls = base_calls


def dispatch_prefixes(study: Path, *, authorization_path: Path, acceptance_path: Path,
                      transport_factory=None) -> dict:
    study = Path(study).resolve()
    manifest = verify(study, frozen=True)
    authorization = _authorization(study, manifest, authorization_path, "prefix")
    acceptance = _acceptance_record(acceptance_path, manifest["protocol"]["model"]["requested_model"])
    dispatch_dir = study / "dispatch"
    dispatch_dir.mkdir(exist_ok=True)
    if (dispatch_dir / "halt.json").exists():
        raise ValueError("phase-B dispatch is halted; do not restart this batch")
    protocol = manifest["protocol"]
    gate = GlobalPauseGate(protocol["model"].get("pause_after_consecutive_rate_limits", 2))
    if transport_factory is None:
        transport_factory = lambda job: _TrackedDiagnosticTransport(
            job["provider"], job["model"], job["timeout_seconds"], min_interval_seconds=1.0)
    for job in manifest["prefix_jobs"]:
        run_dir = study / "prefix_runs" / job["job_id"]
        status_path = run_dir / "status.json"
        canonical_path = run_dir / "terminal_status.json"
        if canonical_path.exists() and read_json(canonical_path).get("status") in PREFIX_TERMINAL:
            status = read_json(canonical_path)["status"]
            pause = _provider_gate(gate, run_dir, status)
            if pause["pause"]:
                _write_status(dispatch_dir / "halt.json", "paused", after_job=job["job_id"],
                              reason=pause, no_new_jobs=True, new_model_calls=0)
                break
            continue
        if status_path.exists() and read_json(status_path).get("status") in PREFIX_TERMINAL:
            status = read_json(status_path).get("status")
            status = _canonical_terminal(run_dir, status, prefix=True)["status"]
            if status in {"infrastructure_incomplete", "sent_unknown", "provider_failed"}:
                pause = _provider_gate(gate, run_dir, status)
                if pause["pause"]:
                    _write_status(dispatch_dir / "halt.json", "paused", after_job=job["job_id"],
                                  reason=pause, no_new_jobs=True, new_model_calls=0)
                    break
            continue
        if (dispatch_dir / "halt.json").exists():
            break
        run_dir.mkdir(parents=True, exist_ok=True)
        snapshot = _snapshot_by_block(study, manifest, job["data_block"], "search")
        parameters = {key: job[key] for key in ("temperature", "planner_max_tokens", "coder_max_tokens",
                     "timeout_seconds", "token_budget", "request_limit", "wall_limit_seconds")}
        transport = None
        try:
            transport = transport_factory(job)
            result = _run_prefix_with_budget(
                job, snapshot, run_dir,
                {"manifest_sha256": manifest["manifest_sha256"],
                 "authorization_sha256": authorization["sha256"]},
                parameters, transport)
            status = result.get("status", "infrastructure_incomplete")
            if status not in PREFIX_TERMINAL:
                status = "infrastructure_incomplete"
                _write_status(status_path, status, source_status=result.get("status"))
        except Exception as exc:
            status = "infrastructure_incomplete"
            _write_status(status_path, status, error_type=type(exc).__name__,
                          no_automatic_retry=True)
        _persist_transport_diagnostics(run_dir, transport)
        status = _canonical_terminal(run_dir, status, prefix=True)["status"]
        pause = _provider_gate(gate, run_dir, status)
        save_json(dispatch_dir / f"{job['job_id']}.json",
                  {"job_id": job["job_id"], "status": status,
                   "service_pause": pause, "utc": utcnow()}, immutable=True)
        if pause["pause"]:
            _write_status(dispatch_dir / "halt.json", "paused",
                          after_job=job["job_id"], reason=pause,
                          no_new_jobs=True, new_model_calls=0)
            break
    for job in manifest["prefix_jobs"]:
        status_path = study / "prefix_runs" / job["job_id"] / "status.json"
        if not status_path.exists():
            run_dir = status_path.parent
            _write_status(status_path, "not_started", reason="global_service_pause")
            _write_status(run_dir / "terminal_status.json", "not_started",
                          reason="global_service_pause", attempted_calls=[], unknown_cost=False,
                          missing_outcome=True, outcome_counted_as_zero=False,
                          no_automatic_retry=True)
    return {"jobs": len(manifest["prefix_jobs"]), "statuses": _prefix_statuses(study, manifest),
            "provider_acceptance": acceptance, "authorization": authorization,
            "halted": (dispatch_dir / "halt.json").exists()}


def _prefix_statuses(study: Path, manifest: dict) -> dict:
    result = {}
    for job in manifest["prefix_jobs"]:
        path = study / "prefix_runs" / job["job_id"] / "status.json"
        terminal = path.with_name("terminal_status.json")
        source = terminal if terminal.exists() else path
        result[job["job_id"]] = read_json(source).get("status", "missing") if source.exists() else "missing"
    return result


def _select_prefix_checkpoint(block: int, prefix_step: int, nodes: list[dict], search_sha256: str,
                              *, quality_tolerance: float = 0.035,
                              minimum_behavior_distance: float = 0.08) -> dict:
    # The committed prefix runner has three seeds. Never inspect later proposals,
    # even if a caller accidentally supplies the complete run.
    nodes = nodes[:len(SEEDS["tsp"]) + prefix_step]
    candidates = [node for node in nodes
                  if node.get("evaluation", {}).get("valid") and
                  isinstance(node.get("evaluation", {}).get("loss"), (int, float))]
    if not candidates:
        return {"checkpoint_id": f"b{block}-step{prefix_step:02d}", "block": block,
                "prefix_step": prefix_step, "status": "preparation_incomplete",
                "reason": "no valid validation candidate in frozen prefix"}
    incumbent = min(candidates, key=lambda n: (n["evaluation"]["loss"], n["id"]))
    branch_audit, branch_candidates = [], []
    for node in candidates:
        if node["id"] == incumbent["id"] or node.get("code") == incumbent.get("code"):
            continue
        loss_delta = node["evaluation"]["loss"] - incumbent["evaluation"]["loss"]
        if not 0 < loss_delta <= quality_tolerance:
            continue
        try:
            distance = benchmarks.behavior_distance(
                incumbent["evaluation"].get("behavior", []),
                node["evaluation"].get("behavior", []))
        except (TypeError, ValueError):
            distance = None
        eligible = distance is not None and distance > minimum_behavior_distance
        branch_audit.append({"node_id": node["id"], "validation_loss_delta": loss_delta,
                             "probe_behavior_distance": distance, "eligible": eligible})
        if eligible:
            branch_candidates.append(node)
    branch = min(branch_candidates, key=lambda n: (n["evaluation"]["loss"], n["id"])) if branch_candidates else None
    return {
        "checkpoint_id": f"b{block}-step{prefix_step:02d}", "status": "ready",
        "prefix_step": prefix_step, "continuation": {"block": block,
            "snapshot_sha256": search_sha256, "public_prefix_steps": prefix_step},
        "selected_on": "validation", "nodes": copy.deepcopy(nodes),
        "incumbent": {"id": incumbent["id"], "loss": incumbent["evaluation"]["loss"]},
        "branch": ({"id": branch["id"], "loss": branch["evaluation"]["loss"]}
                   if branch else None),
        "branch_selection_rule": {
            "eligible": "valid non-identical program with validation loss difference in (0, 0.035] and probe behavior distance > 0.08",
            "winner": "minimum validation loss, then minimum node id",
            "behavior_distance": "chapter6_demo.benchmarks.behavior_distance over aligned probe behavior descriptors; strict threshold > 0.08",
            "test_access": False},
        "branch_candidate_audit": branch_audit,
        "incumbent_validation_loss": incumbent["evaluation"]["loss"],
        "branch_unavailable": branch is None,
    }


def freeze_checkpoints(study: Path) -> dict:
    """Freeze step-8/24 states after every public-prefix job has terminated."""
    study = Path(study).resolve()
    manifest = verify(study, frozen=True)
    if (study / "checkpoint_manifest.json").exists():
        return verify_checkpoints(study, manifest)
    statuses = _prefix_statuses(study, manifest)
    if any(status not in PREFIX_TERMINAL for status in statuses.values()):
        raise ValueError("all public-prefix jobs must be terminal before checkpoint selection")
    records, branch_unavailable = [], []
    for prefix_job in manifest["prefix_jobs"]:
        block = prefix_job["data_block"]
        run_dir = study / "prefix_runs" / prefix_job["job_id"]
        result_path = run_dir / "search_result.json"
        checkpoint_path = run_dir / "checkpoint.json"
        result = read_json(result_path) if result_path.exists() else {}
        saved = read_json(checkpoint_path) if checkpoint_path.exists() else {}
        proposals = saved.get("records", [])
        seeds = saved.get("seeds", [])
        for prefix_step in manifest["protocol"]["stages"]["B"]["public_prefix"]["checkpoints"]:
            expected_seeds = SEEDS["tsp"]
            seeds_match = (len(seeds) == len(expected_seeds) and all(
                seed.get("name") == expected[0] and seed.get("tags") == expected[1]
                and seed.get("code") == expected[2]
                and seed.get("evaluation", {}).get("valid") is True
                for seed, expected in zip(seeds, expected_seeds)))
            if len(proposals) < prefix_step or not seeds_match:
                checkpoint = {"checkpoint_id": f"b{block}-step{prefix_step:02d}",
                              "block": block, "prefix_step": prefix_step,
                              "status": "preparation_incomplete",
                              "prefix_status": statuses[prefix_job["job_id"]],
                              "completed_proposals": len(proposals)}
            else:
                nodes = list(seeds) + [row["node"] for row in proposals[:prefix_step]]
                search_rec = next(rec for rec in manifest["data"]
                                  if rec["block"] == block and rec["role"] == "search")
                checkpoint = _select_prefix_checkpoint(block, prefix_step, nodes,
                    search_rec["sha256"], quality_tolerance=0.035,
                    minimum_behavior_distance=0.08)
                checkpoint["public_prefix_job_id"] = prefix_job["job_id"]
                checkpoint["public_prefix_result_sha256"] = file_sha(result_path) if result_path.exists() else None
                checkpoint["public_prefix_status"] = statuses[prefix_job["job_id"]]
            rel = f"checkpoints/{checkpoint['checkpoint_id']}.json"
            checkpoint["path"] = rel
            save_json(study / rel, checkpoint, immutable=True)
            record = {"checkpoint_id": checkpoint["checkpoint_id"], "block": block,
                      "prefix_step": prefix_step, "path": rel,
                      "sha256": file_sha(study / rel), "status": checkpoint["status"],
                      "branch_unavailable": bool(checkpoint.get("branch_unavailable", False))}
            records.append(record)
            if record["branch_unavailable"]:
                branch_unavailable.append(record["checkpoint_id"])
    checkpoint_manifest = {
        "schema": "chapter6-phase-b-checkpoints-v1", "study_manifest_sha256": manifest["manifest_sha256"],
        "created_utc": utcnow(), "records": records,
        "prefix_job_statuses": statuses,
        "branch_unavailable_checkpoint_ids": branch_unavailable,
        "test_access": False,
    }
    checkpoint_manifest["checkpoint_manifest_sha256"] = digest(checkpoint_manifest)
    save_json(study / "checkpoint_manifest.json", checkpoint_manifest, immutable=True)
    return checkpoint_manifest


def verify_checkpoints(study: Path, manifest: dict) -> dict:
    checkpoint_manifest = read_json(study / "checkpoint_manifest.json")
    if digest({key: value for key, value in checkpoint_manifest.items()
               if key != "checkpoint_manifest_sha256"}) != checkpoint_manifest.get("checkpoint_manifest_sha256"):
        raise ValueError("checkpoint manifest digest mismatch")
    if checkpoint_manifest.get("study_manifest_sha256") != manifest["manifest_sha256"]:
        raise ValueError("checkpoints bind to a different study manifest")
    if len(checkpoint_manifest.get("records", [])) != 16:
        raise ValueError("phase B requires all 16 planned checkpoint states")
    for rec in checkpoint_manifest["records"]:
        path = (study / rec["path"]).resolve()
        if not path.is_relative_to(study) or file_sha(path) != rec["sha256"]:
            raise ValueError(f"checkpoint changed: {rec['checkpoint_id']}")
    return checkpoint_manifest


def _checkpoint_map(study: Path, checkpoint_manifest: dict) -> dict:
    return {row["checkpoint_id"]: read_json(study / row["path"])
            for row in checkpoint_manifest["records"]}


def _continuation_parameters(job: dict, protocol: dict) -> dict:
    generation = protocol["generation"]
    return {"temperature": generation["temperature"],
            "planner_max_tokens": generation["planner_max_tokens"],
            "coder_max_tokens": generation["coder_max_tokens"],
            "timeout_seconds": generation["timeout_seconds"],
            "token_budget": protocol["stages"]["B"]["token_limit_continuation_per_job"],
            "request_limit": 16,
            "wall_limit_seconds": protocol["stages"]["B"]["wall_limit_continuation_job_seconds"]}


def dispatch_continuations(study: Path, *, authorization_path: Path,
                           acceptance_path: Path, transport_factory=None,
                           runner=run_phase_b) -> dict:
    study = Path(study).resolve()
    manifest = verify(study, frozen=True)
    checkpoint_manifest = verify_checkpoints(study, manifest)
    authorization = _authorization(study, manifest, authorization_path, "continuation")
    acceptance = _acceptance_record(acceptance_path, manifest["protocol"]["model"]["requested_model"])
    dispatch_dir = study / "dispatch"
    dispatch_dir.mkdir(exist_ok=True)
    if (dispatch_dir / "halt.json").exists():
        raise ValueError("phase-B dispatch is halted; do not restart this batch")
    protocol = manifest["protocol"]
    gate = GlobalPauseGate(protocol["model"].get("pause_after_consecutive_rate_limits", 2))
    if transport_factory is None:
        transport_factory = lambda job: _TrackedDiagnosticTransport(
            job["provider"], job["model"], protocol["generation"]["timeout_seconds"],
            min_interval_seconds=1.0)
    checkpoints = _checkpoint_map(study, checkpoint_manifest)
    for job in manifest["jobs"]:
        run_dir = study / "runs" / job["job_id"]
        status_path = run_dir / "status.json"
        canonical_path = run_dir / "terminal_status.json"
        if canonical_path.exists() and read_json(canonical_path).get("status") in TEST_ELIGIBLE:
            continue
        if status_path.exists() and read_json(status_path).get("status") in CONTINUATION_TERMINAL:
            _canonical_terminal(run_dir, read_json(status_path).get("status"))
            continue
        if (dispatch_dir / "halt.json").exists():
            break
        checkpoint = checkpoints[job["checkpoint_id"]]
        if checkpoint.get("status") != "ready":
            run_dir.mkdir(parents=True, exist_ok=True)
            status = _write_status(status_path, "preparation_incomplete",
                                   checkpoint_status=checkpoint.get("status"), new_model_calls=0)
            _write_status(run_dir / "terminal_status.json", "preparation_incomplete",
                          checkpoint_status=checkpoint.get("status"), reason="public_prefix_not_available",
                          attempted_calls=[], unknown_cost=False, missing_outcome=True,
                          outcome_counted_as_zero=False, no_automatic_retry=True)
        elif job["strategy"] == "B" and not checkpoint.get("branch"):
            run_dir.mkdir(parents=True, exist_ok=True)
            status = _write_status(status_path, "branch_unavailable",
                                   checkpoint_id=job["checkpoint_id"], new_model_calls=0)
            _write_status(run_dir / "terminal_status.json", "branch_unavailable",
                          checkpoint_id=job["checkpoint_id"], reason="no_eligible_nonidentical_branch",
                          attempted_calls=[], unknown_cost=False, missing_outcome=True,
                          outcome_counted_as_zero=False, no_automatic_retry=True)
        else:
            run_dir.mkdir(parents=True, exist_ok=True)
            snapshot = _snapshot_by_block(study, manifest, job["data_block"], "search")
            parameters = _continuation_parameters(job, protocol)
            bound_job = {**job, "checkpoint_path": f"checkpoints/{job['checkpoint_id']}.json",
                         "checkpoint_sha256": next(rec["sha256"] for rec in checkpoint_manifest["records"]
                                                    if rec["checkpoint_id"] == job["checkpoint_id"])}
            transport = None
            try:
                transport = transport_factory(bound_job)
                result = runner(bound_job, checkpoint, snapshot, run_dir,
                                {"manifest_sha256": manifest["manifest_sha256"],
                                 "checkpoint_manifest_sha256": checkpoint_manifest["checkpoint_manifest_sha256"],
                                 "authorization_sha256": authorization["sha256"]},
                                parameters, transport, mode="live")
                status_name = result.get("status", "infrastructure_incomplete")
                if status_name not in CONTINUATION_TERMINAL:
                    status_name = "infrastructure_incomplete"
                status = read_json(status_path) if status_path.exists() else _write_status(
                    status_path, status_name, summary=result.get("summary"), usage=result.get("usage"))
            except Exception as exc:
                status_name = "infrastructure_incomplete"
                status = _write_status(status_path, status_name, error_type=type(exc).__name__,
                                       no_automatic_retry=True)
            _persist_transport_diagnostics(run_dir, transport)
            status_name = status.get("status", "infrastructure_incomplete")
            status = _canonical_terminal(run_dir, status_name)
        pause = _provider_gate(gate, run_dir, status["status"])
        save_json(dispatch_dir / f"{job['job_id']}.json",
                  {"job_id": job["job_id"], "status": status["status"],
                   "service_pause": pause, "utc": utcnow()}, immutable=True)
        if pause["pause"]:
            _write_status(dispatch_dir / "halt.json", "paused",
                          after_job=job["job_id"], reason=pause,
                          no_new_jobs=True, new_model_calls=0)
            break
    for job in manifest["jobs"]:
        status_path = study / "runs" / job["job_id"] / "status.json"
        if not status_path.exists():
            run_dir = status_path.parent
            _write_status(status_path, "not_started", reason="global_service_pause")
            _write_status(run_dir / "terminal_status.json", "not_started",
                          reason="global_service_pause", attempted_calls=[], unknown_cost=False,
                          missing_outcome=True, outcome_counted_as_zero=False,
                          no_automatic_retry=True)
    return {"jobs": len(manifest["jobs"]), "statuses": _continuation_statuses(study, manifest),
            "provider_acceptance": acceptance, "authorization": authorization,
            "halted": (dispatch_dir / "halt.json").exists()}


def _continuation_statuses(study: Path, manifest: dict) -> dict:
    statuses = {}
    for job in manifest["jobs"]:
        path = study / "runs" / job["job_id"] / "status.json"
        terminal = path.with_name("terminal_status.json")
        source = terminal if terminal.exists() else path
        statuses[job["job_id"]] = read_json(source).get("status", "missing") if source.exists() else "missing"
    return statuses


def _validate_candidate_freezes(study: Path, manifest: dict) -> dict:
    statuses = _continuation_statuses(study, manifest)
    if any(status not in TEST_ELIGIBLE for status in statuses.values()):
        raise ValueError("Test is sealed until all 128 continuation tasks are terminal")
    audited, not_applicable, missing_prefixes = [], [], []
    for job in manifest["jobs"]:
        run_dir = study / "runs" / job["job_id"]
        status = statuses[job["job_id"]]
        selection_path, result_path = run_dir / "selection_candidates.json", run_dir / "search_result.json"
        terminal_path = run_dir / "terminal_status.json"
        terminal = read_json(terminal_path) if terminal_path.exists() else {}
        if not selection_path.exists():
            if status == "continuation_complete":
                raise ValueError(f"completed task is missing frozen prefix selections: {job['job_id']}")
            if not (terminal.get("reason") or terminal.get("error_type")
                    or terminal.get("underlying_runner_status") or terminal.get("attempted_calls")):
                raise ValueError(f"terminal task is missing an auditable outcome reason: {job['job_id']}")
            for prefix in ("4", "8"):
                missing_prefixes.append({"job_id": job["job_id"], "prefix": int(prefix),
                                         "status": "missing_prefix", "terminal_status": status,
                                         "reason": terminal.get("reason") or terminal.get("underlying_runner_status") or status})
            not_applicable.append(job["job_id"])
            continue
        selections = read_json(selection_path)
        if status == "continuation_complete" and not result_path.exists():
            raise ValueError(f"completed task is missing its terminal result: {job['job_id']}")
        if result_path.exists():
            result = read_json(result_path)
            if result.get("selection_candidates_sha256") != file_sha(selection_path):
                raise ValueError(f"selection hash differs from terminal result: {job['job_id']}")
        elif status in {"branch_unavailable", "preparation_incomplete", "not_started"}:
            raise ValueError(f"skipped task unexpectedly has prefix selections: {job['job_id']}")
        for prefix in ("4", "8"):
            value = selections.get(prefix, {})
            if value.get("status") == "frozen_on_validation":
                if value.get("selected_on") != "validation" or not isinstance(value.get("code"), str):
                    raise ValueError(f"prefix {prefix} was not selected using validation only")
            elif value.get("status") != "missing_prefix":
                raise ValueError(f"prefix {prefix} has an unknown freeze status")
            else:
                missing_prefixes.append({"job_id": job["job_id"], "prefix": int(prefix),
                                         "status": "missing_prefix", "terminal_status": status,
                                         "completed_proposals": value.get("completed_proposals")})
        audited.append(job["job_id"])
    return {"audited_job_ids": audited, "not_applicable_job_ids": not_applicable,
            "missing_prefixes": missing_prefixes,
            "all_planned_jobs_terminal": True, "all_available_prefixes_frozen_on_validation": True}


def _materialize_test_snapshots(study: Path, manifest: dict) -> dict:
    """Open and snapshot Test data only after the caller has passed every global freeze check."""
    study = Path(study).resolve()
    statuses = _continuation_statuses(study, manifest)
    planned_ids = {job["job_id"] for job in manifest["jobs"]}
    if (set(statuses) != planned_ids
            or any(status not in TEST_ELIGIBLE for status in statuses.values())):
        raise ValueError("Test data cannot be opened before every continuation task is terminal")
    _validate_candidate_freezes(study, manifest)
    path = study / "test_data_manifest.json"
    if path.exists():
        saved = read_json(path)
        if digest({key: value for key, value in saved.items() if key != "manifest_sha256"}) != saved.get("manifest_sha256"):
            raise ValueError("Test data manifest digest mismatch")
        if saved.get("study_manifest_sha256") != manifest["manifest_sha256"]:
            raise ValueError("Test data manifest binds to a different study")
        for row in saved["data"]:
            data_path = (study / row["path"]).resolve()
            if not data_path.is_relative_to(study) or file_sha(data_path) != row["sha256"]:
                raise ValueError(f"Test snapshot changed: {row['path']}")
        return saved
    rows = []
    for block in manifest["protocol"]["stages"]["B"]["blocks"]:
        snapshot = {"profile": benchmarks.V12_TSP_PROFILE, "block": block,
                    "test": copy.deepcopy(benchmarks._instances_cached(
                        "tsp", "test", benchmarks.V12_TSP_PROFILE, block))}
        expected = manifest["protocol"]["data"]["adapter_split_counts_per_block"]["test"]
        if len(snapshot["test"]) != expected:
            raise ValueError(f"TSP14 Test split count differs from protocol at block {block}")
        rel = f"data/test-b{block}.json"
        save_json(study / rel, snapshot, immutable=True)
        rows.append({"block": block, "path": rel, "sha256": file_sha(study / rel),
                     "instance_count": len(snapshot["test"]),
                     "role": "test", "materialized_after_global_freeze": True})
    value = {"schema": "chapter6-phase-b-test-data-v1",
             "study_manifest_sha256": manifest["manifest_sha256"], "data": rows,
             "all_search_terminal": True, "all_validation_selections_frozen": True,
             "test_evaluation_performed": False}
    value["manifest_sha256"] = digest(value)
    save_json(path, value, immutable=True)
    return value


def release_test_gate(study: Path) -> dict:
    study = Path(study).resolve()
    manifest = verify(study, frozen=True)
    checkpoint_manifest = verify_checkpoints(study, manifest)
    freeze_audit = _validate_candidate_freezes(study, manifest)
    statuses = _continuation_statuses(study, manifest)
    planned_ids = {job["job_id"] for job in manifest["jobs"]}
    if set(statuses) != planned_ids or any(status not in TEST_ELIGIBLE for status in statuses.values()):
        raise ValueError("Test remains sealed until every planned task has an explicit terminal status")
    test_data_manifest = _materialize_test_snapshots(study, manifest)
    candidate_manifest = _build_test_candidate_manifest(study, manifest, checkpoint_manifest,
                                                        test_data_manifest)
    save_json(study / "test_candidate_manifest.json", candidate_manifest, immutable=True)
    value = {"schema": "chapter6-phase-b-test-gate-v1", "released": True,
             "expected_job_count": len(planned_ids), "statuses": statuses,
             "released_after_all_terminal": True, "new_model_calls": 0,
             "study_manifest_sha256": manifest["manifest_sha256"],
             "checkpoint_manifest_sha256": checkpoint_manifest["checkpoint_manifest_sha256"],
             "test_data_manifest_sha256": test_data_manifest["manifest_sha256"],
             "test_candidate_manifest_sha256": candidate_manifest["test_candidate_manifest_sha256"],
             "freeze_audit": freeze_audit, "test_candidate_cap": 288,
             "test_access_before_gate": False}
    save_json(study / "test_gate.json", value, immutable=True)
    return value


def _build_test_candidate_manifest(study: Path, manifest: dict,
                                   checkpoint_manifest: dict,
                                   test_data_manifest: dict) -> dict:
    test_hashes = {rec["block"]: rec["sha256"] for rec in test_data_manifest["data"]}
    candidates, missing_references = [], []
    for job in manifest["jobs"]:
        selection_path = study / "runs" / job["job_id"] / "selection_candidates.json"
        if not selection_path.exists():
            continue
        selections = read_json(selection_path)
        for prefix in ("4", "8"):
            selection = selections[prefix]
            if selection.get("status") != "frozen_on_validation":
                continue
            code = selection.get("code")
            if not isinstance(code, str):
                raise ValueError(f"frozen selection lacks candidate source: {job['job_id']} prefix {prefix}")
            candidates.append({
                "candidate_id": f"run-{job['job_id']}-p{prefix}",
                "kind": "continuation_prefix", "job_id": job["job_id"],
                "checkpoint_id": job["checkpoint_id"], "data_block": job["data_block"],
                "prefix_proposals": int(prefix), "selected_on": "validation",
                "validation_loss": selection.get("validation_loss"),
                "code_sha256": hashlib.sha256(code.encode()).hexdigest(),
                "source_path": f"runs/{job['job_id']}/selection_candidates.json",
                "test_data_sha256": test_hashes[job["data_block"]],
            })
    checkpoints = _checkpoint_map(study, checkpoint_manifest)
    cp_records = {row["checkpoint_id"]: row for row in checkpoint_manifest["records"]}
    for checkpoint_id, checkpoint in sorted(checkpoints.items()):
        if checkpoint.get("status") != "ready":
            missing_references.extend([
                {"checkpoint_id": checkpoint_id, "reference_action": "I", "reason": "checkpoint_unavailable"},
                {"checkpoint_id": checkpoint_id, "reference_action": "B", "reason": "checkpoint_unavailable"},
            ])
            continue
        for action in ("I", "B"):
            ref = checkpoint.get("incumbent") if action == "I" else checkpoint.get("branch")
            if not ref:
                missing_references.append({"checkpoint_id": checkpoint_id,
                                           "reference_action": action,
                                           "reason": "branch_unavailable"})
                continue
            node = next(node for node in checkpoint["nodes"] if node["id"] == ref["id"])
            candidates.append({
                "candidate_id": f"checkpoint-{checkpoint_id}-start-{action}",
                "kind": "checkpoint_start_reference", "job_id": None,
                "checkpoint_id": checkpoint_id, "data_block": checkpoint["continuation"]["block"],
                "prefix_proposals": 0, "reference_action": action,
                "selected_on": "validation", "validation_loss": ref["loss"],
                "code_sha256": hashlib.sha256(node["code"].encode()).hexdigest(),
                "source_path": f"checkpoints/{checkpoint_id}.json",
                "checkpoint_sha256": cp_records[checkpoint_id]["sha256"],
                "test_data_sha256": test_hashes[checkpoint["continuation"]["block"]],
            })
    if len(candidates) > manifest["protocol"]["stages"]["B"]["test_candidate_program_cap"]:
        raise ValueError("frozen Test candidate count exceeds protocol cap")
    value = {
        "schema": "chapter6-phase-b-test-candidate-manifest-v1",
        "study_manifest_sha256": manifest["manifest_sha256"],
        "checkpoint_manifest_sha256": checkpoint_manifest["checkpoint_manifest_sha256"],
        "candidates": candidates, "missing_start_references": missing_references,
        "candidate_count": len(candidates), "candidate_cap": 288,
        "selected_only_on_validation": True, "test_values_read": True,
        "test_evaluations_performed": False,
        "test_data_manifest_sha256": test_data_manifest["manifest_sha256"],
    }
    value["test_candidate_manifest_sha256"] = digest(value)
    return value


def _candidate_code(study: Path, candidate: dict) -> str:
    source_path = study / candidate["source_path"]
    if candidate["kind"] == "continuation_prefix":
        selection = read_json(source_path)[str(candidate["prefix_proposals"])]
        code = selection["code"]
    else:
        checkpoint = read_json(source_path)
        ref = checkpoint["incumbent"] if candidate["reference_action"] == "I" else checkpoint["branch"]
        node = next(node for node in checkpoint["nodes"] if node["id"] == ref["id"])
        code = node["code"]
    if hashlib.sha256(code.encode()).hexdigest() != candidate["code_sha256"]:
        raise ValueError(f"Test candidate code changed: {candidate['candidate_id']}")
    return code


def _test_snapshot(study: Path, test_data_manifest: dict, block: int) -> dict:
    row = next((item for item in test_data_manifest["data"] if item["block"] == block), None)
    if row is None:
        raise ValueError(f"Test data manifest has no block {block}")
    path = (study / row["path"]).resolve()
    if not path.is_relative_to(Path(study).resolve()) or file_sha(path) != row["sha256"]:
        raise ValueError(f"Test snapshot changed for block {block}")
    return read_json(path)


def test_all(study: Path) -> dict:
    """Evaluate all frozen 4/8-step candidates only after a complete global gate."""
    study = Path(study).resolve()
    manifest = verify(study, frozen=True)
    checkpoint_manifest = verify_checkpoints(study, manifest)
    test_data_manifest = read_json(study / "test_data_manifest.json")
    if (digest({key: value for key, value in test_data_manifest.items()
                if key != "manifest_sha256"}) != test_data_manifest.get("manifest_sha256")
            or test_data_manifest.get("study_manifest_sha256") != manifest["manifest_sha256"]):
        raise ValueError("Test data manifest is absent, changed, or bound to another study")
    gate_path = study / "test_gate.json"
    gate = read_json(gate_path)
    if (not gate.get("released") or gate.get("study_manifest_sha256") != manifest["manifest_sha256"]
            or gate.get("checkpoint_manifest_sha256") != checkpoint_manifest["checkpoint_manifest_sha256"]
            or gate.get("test_data_manifest_sha256") != test_data_manifest["manifest_sha256"]):
        raise ValueError("phase-B Test gate is absent or bound to different frozen inputs")
    freeze_audit = _validate_candidate_freezes(study, manifest)
    candidate_manifest_path = study / "test_candidate_manifest.json"
    candidate_manifest = read_json(candidate_manifest_path)
    if (digest({key: value for key, value in candidate_manifest.items()
                if key != "test_candidate_manifest_sha256"})
            != candidate_manifest.get("test_candidate_manifest_sha256")
            or gate.get("test_candidate_manifest_sha256") != candidate_manifest.get("test_candidate_manifest_sha256")):
        raise ValueError("frozen Test candidate manifest is absent, changed, or not gate-bound")
    output_count = 0
    from ..s3_tsp_r3.evaluator import evaluate_test
    for candidate in candidate_manifest["candidates"]:
        candidate_id = candidate["candidate_id"]
        path = study / "test" / f"{candidate_id}.json"
        code = _candidate_code(study, candidate)
        binding = {"study_manifest_sha256": manifest["manifest_sha256"],
                   "checkpoint_manifest_sha256": checkpoint_manifest["checkpoint_manifest_sha256"],
                   "candidate_manifest_sha256": candidate_manifest["test_candidate_manifest_sha256"],
                   "candidate_id": candidate_id, "candidate_code_sha256": candidate["code_sha256"],
                   "checkpoint_sha256": candidate.get("checkpoint_sha256"),
                   "test_data_sha256": candidate["test_data_sha256"]}
        if path.exists():
            existing = read_json(path)
            if existing.get("binding") != binding:
                raise ValueError(f"stored Test result differs for {candidate_id}")
            output_count += 1
            continue
        test_snapshot = _test_snapshot(study, test_data_manifest, candidate["data_block"])
        evaluation = evaluate_test(code, test_snapshot)
        save_json(path, {"schema": "chapter6-phase-b-test-result-v1", "binding": binding,
                         "candidate": candidate, "selected_on": "validation",
                         "test_access_after_global_gate": True,
                         "evaluation": evaluation}, immutable=True)
        output_count += 1
    return {"evaluated_candidates": output_count, "planned_candidate_cap": 288,
            "freeze_audit": freeze_audit, "new_model_calls": 0,
            "test_opened_after_all_search_tasks_terminal": True}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare"); p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("verify"); p.add_argument("--study", type=Path, required=True)
    p = sub.add_parser("freeze"); p.add_argument("--draft", type=Path, required=True); p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("prefix-all"); p.add_argument("--study", type=Path, required=True); p.add_argument("--authorization", type=Path, required=True); p.add_argument("--acceptance", type=Path, required=True)
    p = sub.add_parser("freeze-checkpoints"); p.add_argument("--study", type=Path, required=True)
    p = sub.add_parser("continue-all"); p.add_argument("--study", type=Path, required=True); p.add_argument("--authorization", type=Path, required=True); p.add_argument("--acceptance", type=Path, required=True)
    p = sub.add_parser("release-test"); p.add_argument("--study", type=Path, required=True)
    p = sub.add_parser("test-all"); p.add_argument("--study", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        with offline_only(): result = prepare(args.output)
        output = {"status": result["status"], "manifest_sha256": result["manifest_sha256"], "new_model_calls": 0}
    elif args.command == "verify":
        with offline_only(): result = verify(args.study)
        output = {"status": result["status"], "manifest_sha256": result["manifest_sha256"], "new_model_calls": 0}
    elif args.command == "freeze":
        with offline_only(): result = freeze(args.draft, args.output)
        output = {"status": result["status"], "manifest_sha256": result["manifest_sha256"], "new_model_calls": 0}
    elif args.command == "prefix-all": output = dispatch_prefixes(args.study, authorization_path=args.authorization, acceptance_path=args.acceptance)
    elif args.command == "freeze-checkpoints": output = freeze_checkpoints(args.study)
    elif args.command == "continue-all": output = dispatch_continuations(args.study, authorization_path=args.authorization, acceptance_path=args.acceptance)
    elif args.command == "release-test": output = release_test_gate(args.study)
    else: output = test_all(args.study)
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
