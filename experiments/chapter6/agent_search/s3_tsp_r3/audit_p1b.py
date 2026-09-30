"""Independent audit of archived S2 P1b raw responses and controller traces."""
from __future__ import annotations

import argparse
import base64
from collections import Counter
import json
from pathlib import Path
import zipfile


DEFAULT_ARCHIVE = Path(
    "experiments/chapter6/agent_search/s2_tsp/results/"
    "p1b-minimax-protection-20260927/analysis/raw-study.zip"
)


def audit(archive):
    archive = Path(archive)
    stage_truncations = Counter()
    response_count = Counter()
    runs = []
    proposal_slots = 0
    valid_slots = 0
    competitive_parentless_explores = 0
    all_competitive_parentless_explores = 0
    all_admissions = 0
    admitted_global_improvements = 0
    protected_slots = 0
    protected_parent_was_best = 0
    protected_parent_improvements = 0
    protected_lagging_parent_improvements = 0
    protected_best_ancestor_runs = 0
    protected_jobs = 0

    with zipfile.ZipFile(archive) as zf:
        names = set(zf.namelist())
        for name in sorted(n for n in names if n.endswith("/raw_response.json") and "/calls/" in n):
            raw = json.loads(zf.read(name))
            body = json.loads(base64.b64decode(raw["envelope"]["body_base64"], validate=True))
            choices = body.get("choices", [])
            reason = choices[0].get("finish_reason") if choices else None
            stage = name.rsplit("/", 2)[-2].split("-", 1)[-1]
            response_count[stage] += 1
            if str(reason or "").lower() in {"length", "max_tokens", "token_limit"}:
                stage_truncations[stage] += 1

        for name in sorted(n for n in names if n.endswith("/checkpoint.json") and "/runs/" in n):
            checkpoint = json.loads(zf.read(name))
            config = checkpoint["config"]
            protected = bool(config.get("protection"))
            if protected:
                protected_jobs += 1
            nodes = list(checkpoint["seeds"])
            records = checkpoint["records"]
            run_id = name.split("/runs/", 1)[1].rsplit("/checkpoint.json", 1)[0]
            run = {"run_id": run_id, "proposals": len(records), "protection": protected,
                   "competitive_parentless_explores": 0, "branch_admissions": 0,
                   "protected_slots": 0, "protected_parent_was_best": 0,
                   "protected_parent_improvements": 0, "protected_lagging_parent_improvements": 0}
            for record in records:
                proposal_slots += 1
                node, decision, event = record["node"], record["decision"], record["event"]
                valid_slots += int(bool(node["evaluation"].get("valid")))
                if (decision.get("action") == "explore" and decision.get("parent") is None
                        and event.get("valid") and event.get("competitive")):
                    all_competitive_parentless_explores += 1
                    if protected:
                        competitive_parentless_explores += 1
                        run["competitive_parentless_explores"] += 1
                if event.get("branch_admitted"):
                    all_admissions += 1
                    run["branch_admissions"] += 1
                    if event.get("global_improvement"):
                        admitted_global_improvements += 1
                if event.get("protected_development"):
                    protected_slots += 1
                    run["protected_slots"] += 1
                    parent_id = decision.get("parent", {}).get("id") if decision.get("parent") else None
                    eligible = [n for n in nodes if n["evaluation"].get("valid")]
                    best_id = min(eligible, key=lambda n: (n["evaluation"]["loss"], n["id"]))["id"]
                    if parent_id == best_id:
                        protected_parent_was_best += 1
                        run["protected_parent_was_best"] += 1
                    if event.get("local_improvement"):
                        protected_parent_improvements += 1
                        run["protected_parent_improvements"] += 1
                        if parent_id != best_id:
                            protected_lagging_parent_improvements += 1
                            run["protected_lagging_parent_improvements"] += 1
                nodes.append(node)
            if protected:
                result_name = name.replace("checkpoint.json", "search_result.json")
                if result_name in names:
                    result = json.loads(zf.read(result_name))
                    protected_best_ancestor_runs += int(bool(result.get("summary", {}).get("protected_best_ancestor")))
            runs.append(run)

    return {
        "archive": archive.as_posix(),
        "response_count_by_stage": dict(sorted(response_count.items())),
        "truncated_responses_by_stage": dict(sorted(stage_truncations.items())),
        "truncated_responses_total": sum(stage_truncations.values()),
        "proposal_slots": proposal_slots,
        "valid_program_evaluations": valid_slots,
        "competitive_parentless_exploration_candidates": competitive_parentless_explores,
        "competitive_parentless_exploration_candidates_all_arms": all_competitive_parentless_explores,
        "branch_admissions": all_admissions,
        "admissions_that_refreshed_global_best": admitted_global_improvements,
        "protected_jobs": protected_jobs,
        "protected_development_slots": protected_slots,
        "protected_slots_using_then_global_best_parent": protected_parent_was_best,
        "protected_parent_improvements": protected_parent_improvements,
        "protected_lagging_parent_improvements": protected_lagging_parent_improvements,
        "runs_with_protected_best_ancestor": protected_best_ancestor_runs,
        "runs": runs,
        "scope": "Recomputed from P1b raw ZIP; no new model calls. Does not revise or overwrite the original frozen report.",
    }


def write_report(result, json_path, markdown_path):
    Path(json_path).parent.mkdir(parents=True, exist_ok=True)
    Path(json_path).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# P1b 原始归档独立复核补记",
        "",
        "本补记从已发布 `raw-study.zip` 重新读取所有响应与 checkpoint，不覆盖 P1b 冻结报告，也没有新增模型调用。",
        "",
        "| 核查项 | 复算结果 |",
        "|---|---:|",
        f"| 模型响应截断 | {result['truncated_responses_total']}（planner {result['truncated_responses_by_stage'].get('planner', 0)}，coder {result['truncated_responses_by_stage'].get('coder', 0)}） |",
        f"| 有效且达到质量竞争条件的无父代探索候选 | {result['competitive_parentless_exploration_candidates']} |",
        f"| 分支准入 | {result['branch_admissions']} |",
        f"| 其中准入即刷新当时全局最好 | {result['admissions_that_refreshed_global_best']} |",
        f"| 保护开发时隙 | {result['protected_development_slots']} |",
        f"| 保护时隙选择当时全局最好父代 | {result['protected_slots_using_then_global_best_parent']} |",
        f"| 保护开发相对父代改进 | {result['protected_parent_improvements']} |",
        f"| 保护开发相对落后父代改进 | {result['protected_lagging_parent_improvements']} |",
        f"| 最终最佳有保护开发祖先的运行 | {result['runs_with_protected_best_ancestor']}/{result['protected_jobs']} |",
        "",
        "这组记录说明 P1b 中探索准入确实没有接通；已产生的保护时隙也大多继续选择了当时的全局最好父代。因此，旧批次只能说明保护流程被触发，不能证明落后方向得到有效保护。输出截断是独立的工程限制，且应与方法效果分开报告。",
        "",
        "逐运行复算数据见 `P1B_AUDIT.json`。",
        "",
    ]
    Path(markdown_path).write_text("\n".join(lines), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.archive)
    write_report(result, args.output / "P1B_AUDIT.json", args.output / "P1B_CORRECTION_ZH.md")
    print(json.dumps({key: value for key, value in result.items() if key != "runs"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
