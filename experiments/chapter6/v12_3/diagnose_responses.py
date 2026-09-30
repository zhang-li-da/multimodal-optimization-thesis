"""Post-hoc provider-format diagnosis; never salvage truncated thought into code."""
import argparse
import base64
from collections import Counter
import json
from pathlib import Path

from chapter6_demo.v12_1.audit_r2 import offline_only
from chapter6_demo.v12_2.common import file_sha, read_json, save_json
from chapter6_demo.v12_3.analyze import write_csv


def diagnose(study, output):
    study, output = Path(study), Path(output)
    if output.exists():
        raise ValueError("Preserve prior diagnostics; use a new directory.")
    manifest = read_json(study / "manifest.json")
    rows = []
    for job in manifest["jobs"]:
        directory = study / "runs" / job["job_id"]
        cp = read_json(directory / "checkpoint.json")
        records = {step: r for step, r in enumerate(cp["records"])}
        for folder in sorted((directory / "calls").glob("*")):
            request, response, raw = (read_json(folder / (name + ".json"))
                                      for name in ("request", "response", "raw_response"))
            body = json.loads(base64.b64decode(raw["envelope"]["body_base64"]))
            choice = body["choices"][0]
            text = response["text"]
            has_think = "<think>" in text
            closed = "</think>" in text
            suffix = text.rsplit("</think>", 1)[-1] if closed else text
            failure = records[request["step"]]["node"].get("proposal_failure")
            rows.append({"job_id": job["job_id"], "controller": job["controller"],
                         "step": request["step"], "stage": request["stage"],
                         "finish_reason": choice["finish_reason"], "max_tokens": request["max_tokens"],
                         "output_tokens": response["output_tokens"],
                         "has_think_open": has_think, "has_think_close": closed,
                         "nonthinking_suffix_characters": len(suffix.strip()) if closed else None,
                         "no_final_text_after_thinking": has_think and closed and not suffix.strip(),
                         "proposal_failed_at_this_stage": bool(failure and failure["stage"] == request["stage"]),
                         "raw_response_sha256": file_sha(folder / "raw_response.json")})
    summary = {"scope": "Post-hoc engineering diagnosis after the frozen search; does not alter calls, proposals, or primary outcomes.",
               "manifest_sha256": manifest["manifest_sha256"], "new_model_calls": 0,
               "responses": len(rows), "planner_requests": sum(r["stage"] == "planner" for r in rows),
               "coder_requests": sum(r["stage"] == "coder" for r in rows),
               "planner_length_stops": sum(r["stage"] == "planner" and r["finish_reason"] == "length" for r in rows),
               "coder_length_stops": sum(r["stage"] == "coder" and r["finish_reason"] == "length" for r in rows),
               "planner_no_final_text_after_thinking": sum(r["stage"] == "planner" and r["no_final_text_after_thinking"] for r in rows),
               "unclosed_thinking_blocks": sum(r["has_think_open"] and not r["has_think_close"] for r in rows),
               "failed_stage_matches_length_stop": sum(r["proposal_failed_at_this_stage"] and r["finish_reason"] == "length" for r in rows),
               "correction": "Early progress wording 'unclosed thinking' was imprecise: truncated planner responses end with </think> but no final plan text. The analyzer's unclosed_thinking_responses=0 is correct.",
               "interpretation": "An engineering preflight that returned ready=true did not validate task-shaped planner/coder outputs under the frozen 1800/2000-token limits. Output-limit or thinking-mode changes require a new protocol; no repair or resubmission was made in this batch."}
    output.mkdir(parents=True)
    save_json(output / "summary.json", summary, immutable=True)
    write_csv(output / "responses.csv", rows)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with offline_only():
        result = diagnose(args.study, args.output)
    print(json.dumps(result))
