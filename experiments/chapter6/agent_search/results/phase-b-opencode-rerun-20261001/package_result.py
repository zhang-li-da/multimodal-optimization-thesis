"""Package the halted local-OpenCode run without Test data or secrets."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[4]
RUN = Path("C:/Users/67473/Desktop/5/phase_b_opencode_complete_20261001")
ACCEPTANCE = Path("C:/Users/67473/Desktop/5/phase_b_public_prefix_20260930/local-opencode-acceptance/full-20261002/summary.json")
OUT = HERE / "evidence"

def sha(data):
    return hashlib.sha256(data).hexdigest()

def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode())

def copy_tree(src, dst):
    for path in src.rglob('*'):
        if not path.is_file():
            continue
        rel = path.relative_to(src)
        if any(part in {"test", "test_data", "__pycache__"} for part in rel.parts):
            raise ValueError(f"Test or cache artifact is not allowed: {rel}")
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)

def main():
    if OUT.exists():
        raise ValueError("evidence output already exists")
    OUT.mkdir(parents=True)
    copy_tree(RUN, OUT / "run")
    acceptance = json.loads(ACCEPTANCE.read_bytes())
    write(OUT / "LOCAL_ACCEPTANCE.json", acceptance)
    summary = json.loads((RUN / "RUN_SUMMARY.json").read_bytes())
    statuses = summary["statuses"]
    records = []
    for job, status in statuses.items():
        run = OUT / "run" / "prefix_runs" / job
        result = json.loads((run / "search_result.json").read_bytes()) if (run / "search_result.json").exists() else {}
        usage = result.get("usage", {})
        records.append({"job_id": job, "status": status,
            "completed_proposals": result.get("summary", {}).get("completed_proposals", 0),
            "requests": usage.get("call_attempts", 0), "known_tokens": usage.get("known_tokens", 0),
            "usage_complete": usage.get("usage_complete"),
            "unknown_requests": sum(1 for row in usage.get("calls", []) if row.get("status") != "response_persisted")})
    report = {
        "title": "阶段 B 本地 OpenCode MiniMax-M3 公共前缀重跑（中断批次）",
        "status": "infrastructure_incomplete",
        "new_model_calls": summary["new_model_calls"],
        "provider": "minimax-cn-coding-plan", "requested_model": "MiniMax-M3",
        "historical_parent_manifest_sha256": json.loads((RUN / "RUN_MANIFEST.json").read_bytes())["historical_parent_manifest_sha256"],
        "run_manifest_sha256": json.loads((RUN / "RUN_MANIFEST.json").read_bytes())["manifest_sha256"],
        "jobs": records, "completed_proposals": summary["completed_proposals"],
        "requests": summary["requests"], "known_tokens": summary["known_tokens"],
        "unknown_requests": summary["unknown_requests"], "test_access": False,
        "checkpoint_count": 16, "ready_checkpoints": 0,
        "reason": "third planner request ended sent_unknown; no automatic retry or new job dispatch",
        "interpretation": "This run demonstrates local OpenCode compatibility and two observed SP proposals; it is not a complete Phase-B experiment and provides no mechanism comparison or Test evidence.",
        "security": "No API keys or authorization headers are included; persisted requests/responses are audited for credentials before publication.",
    }
    write(OUT / "RUN_AUDIT.json", {"summary": summary, "jobs": records, "test_data_present": False})
    write(OUT / "REPORT_ZH.json", report)
    (OUT / "REPORT_ZH.md").write_text("""# 阶段 B 本地 OpenCode MiniMax-M3 公共前缀重跑\n\n本批次是独立于历史 halted study 的 OpenCode 重跑，使用 `minimax-cn-coding-plan/MiniMax-M3`、串行请求、无自动重试和 search-only 数据。正式 3+3 planner/coder 服务验收通过。\n\n## 结果\n\n区块 65 完成 2 个有效 proposal：4 个 planner/coder 请求均返回 `MiniMax-M3`，已知用量 **15,152 tokens**。第 3 个 planner 请求在 180 秒工作负载后进入 `sent_unknown`，没有 usage 或业务错误码；该成本保留为未知，不按零处理。全局暂停后其余 7 个区块未启动。\n\n验证集 incumbent loss 从 `0.05861685599` 降到 `0.04641643148`，但只有一个区块和两个 proposal，不能支持策略效果、分支开发效果或任何 H1-H3 结论。16 个计划检查点全部 `preparation_incomplete`，没有可用于续开发的 ready checkpoint；Test 未读取、未物化。\n\n## 处理\n\n历史区块 60--67 的 halted study 未修改；历史 `sent_unknown` 请求未重试。本批次在新的 manifest 下运行，发现未知服务中断后停止派发。归档包括所有请求、响应、状态、候选、checkpoint、成本和本地服务验收摘要。\n\n这份结果证明本地 OpenCode 调用曾成功完成 MiniMax-M3 的正式短验收和两个搜索 proposal；它不证明完整实验完成，也不证明当前方法有效。后续若恢复实验，必须先决定如何处理本批未知请求，并冻结新的超时/恢复协议，不能把未知请求直接当作失败后重发。\n""", encoding="utf-8")
    # Explicitly reject likely credential material before archiving.
    bad = []
    pattern = b"Bearer "
    for path in OUT.rglob('*'):
        if path.is_file() and pattern in path.read_bytes():
            bad.append(str(path.relative_to(OUT)))
    if bad:
        raise ValueError(f"authorization header material found: {bad}")
    files = []
    for path in sorted(OUT.rglob('*')):
        if path.is_file():
            data = path.read_bytes()
            files.append({"path": path.relative_to(OUT).as_posix(), "bytes": len(data), "sha256": sha(data)})
    write(OUT / "HASHES.json", {"schema": "phase-b-opencode-evidence-sha256-v1", "files": files})
    with zipfile.ZipFile(HERE / "opencode-rerun-evidence.zip", "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for row in files:
            path = OUT / row["path"]
            info = zipfile.ZipInfo(row["path"], date_time=(2026, 10, 2, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes(), compresslevel=9)
    print(json.dumps({"files": len(files), "completed_proposals": summary["completed_proposals"],
                      "requests": summary["requests"], "known_tokens": summary["known_tokens"],
                      "unknown_requests": summary["unknown_requests"]}))

if __name__ == "__main__":
    main()
