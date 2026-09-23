"""Offline interactive evidence viewer for the prospective validation."""
import json
from pathlib import Path

from .verify import selected_paths


def main():
    root = Path("chapter6_validation")
    analysis = json.loads((root / "results/analysis.json").read_text(encoding="utf-8"))
    rows = []
    for path in selected_paths(root / "runs/confirm_v1", root / "runs/confirm_minimax_recovery"):
        r = json.loads(path.read_text(encoding="utf-8"))
        best = next(n for n in r["nodes"] if n["id"] == r["summary"]["validation_selected_best_id"])
        rows.append({"config": r["config"], "summary": r["summary"],
                     "best": {k: best[k] for k in ("id", "name", "intent", "code", "source")},
                     "result_path": str(path.relative_to(root)).replace("\\", "/"),
                     "errors": r["errors"], "stop_reason": r["stop_reason"]})
    payload = json.dumps({"analysis": analysis, "runs": rows}, ensure_ascii=False).replace("<", "\\u003c").replace("&", "\\u0026")
    template = (root / "review_template.html").read_text(encoding="utf-8")
    marker = '<script type="application/json" id="data">__DATA__</script>'
    assert marker in template, "Template data marker missing."
    page = template.replace(marker, '<script type="application/json" id="data">' + payload + '</script>', 1)
    (root / "index.html").write_text(page, encoding="utf-8")
    print(json.dumps({"page": str(root / "index.html"), "runs": len(rows)}))


if __name__ == "__main__":
    main()
