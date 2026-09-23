"""Pin literature, code, protocol and source provenance for the research handoff."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path


def main():
    root = Path("chapter6_validation")
    literature = root / "artifacts/literature"
    items = [
        ("MLEvolve", "https://github.com/InternScience/MLEvolve", literature / "mlevolve_current.json", "commit metadata; local source and paper were read in the initial audit"),
        ("AdaEvolve / SkyDiscover", "https://github.com/skydiscover-ai/skydiscover", literature / "skydiscover_commit.json", "pinned repository tree, adaptation code, archive and config read"),
        ("FunSearch", "https://github.com/google-deepmind/funsearch", literature / "funsearch_programs_database.py", "signature-based clustering and island reset read; matching commit metadata retained"),
        ("SeaEvo", "https://arxiv.org/html/2604.24372v2", Path("_analysis/seaevo.txt"), "sections 3.2-3.5 read; no verified author source found in this bounded repository search"),
        ("AdaEvolve paper", "https://arxiv.org/html/2602.20133v1", Path("_analysis/adaevolve.txt"), "local adaptation/global UCB/migration/meta-guidance read"),
        ("GEPA", "https://arxiv.org/html/2507.19457v1", literature / "gepa_abstract.html", "full paper HTML retrieved; trajectories/Pareto selection/merge sections read, not a complete implementation audit"),
        ("Combinatorial sketching", "https://doi.org/10.1145/1168918.1168907", literature / "prior_art_query_0.json", "Crossref bibliographic verification only; not full-text theorem audit"),
        ("Bandits with Knapsacks", "https://doi.org/10.1145/3164539", literature / "prior_art_query_2.json", "Crossref bibliographic verification only; not full-text theorem audit"),
        ("Corrected MSLS-MA", "user-supplied archive", Path("_analysis/paper_tsp_new/Manuscript File.tex"), "correct paper source; discrete niching/local search/restart mapping in prior handoff"),
        ("RMC-CMSA", "user-supplied manuscript", Path("_analysis/paper2/edc_cmsa_main.tex"), "start-terminal mismatch, typed memory, finite-pool property"),
    ]
    entries = []
    for name, url, path, scope in items:
        entries.append({"name": name, "url": url, "local_path": str(path), "read_scope": scope,
                        "exists": path.exists(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None})
    result = {"recorded_utc": datetime.now(timezone.utc).isoformat(), "sources": entries,
              "limits": "Bounded literature audit, not exhaustive novelty proof; no external framework performance reproduction in this validation.",
              "frozen_protocol": json.loads((root / "artifacts/frozen_v1/freeze.json").read_text()),
              "infrastructure_amendment": str(root / "artifacts/infrastructure_amendment.json")}
    (root / "artifacts/evidence_manifest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"sources": len(entries), "all_local_sources_present": all(e["exists"] for e in entries)}))


if __name__ == "__main__":
    main()
