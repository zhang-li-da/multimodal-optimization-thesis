"""Package all raw runs, source, checksums, analyses and negative evidence."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy_for_git(source,target):
    content=source.read_bytes()
    # The repository pins text files to LF. Hash the bytes another machine
    # will actually receive, while keeping raw-run ZIP members unchanged.
    if source.suffix in (".json",".csv",".md",".txt",".svg"):
        content=content.replace(b"\r\n",b"\n")
    target.write_bytes(content)


def write_zip(path, files):
    with zipfile.ZipFile(path,"w",compression=zipfile.ZIP_DEFLATED,compresslevel=9) as archive:
        for source,relative in sorted(files,key=lambda pair:pair[1]):
            info=zipfile.ZipInfo(relative,date_time=(2026,9,23,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED
            info.external_attr=0o100644<<16
            archive.writestr(info,source.read_bytes())


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root")
    parser.add_argument("--output")
    args=parser.parse_args()
    root=Path(args.root)
    manifest=json.loads((root/"manifest.json").read_text(encoding="utf-8"))
    output=Path(args.output) if args.output else root.parent/"results"/root.name
    if output.exists() and any(output.iterdir()):
        raise SystemExit("Output already exists. Keep published evidence immutable and use a new output path.")
    missing=[job["job_id"] for job in manifest["jobs"] if not (root/"runs"/job["job_id"]/"result.json").exists()]
    if missing:
        raise SystemExit(f"Cannot publish a completed 200-run package: {len(missing)} planned results are missing.")
    verification=json.loads((root/"analysis"/"verification.json").read_text(encoding="utf-8"))
    if verification["checked_runs"]!=len(manifest["jobs"]) or not verification["all_checked_pass"]:
        raise SystemExit("Full deterministic verification must pass before packaging.")
    output.mkdir(parents=True,exist_ok=True)
    analyses=root/"analysis"
    for path in analyses.iterdir():
        if path.is_file() and path.suffix in (".json",".csv",".md",".png",".pdf",".svg"):
            copy_for_git(path,output/path.name)
    for name in ("manifest.json","status.json"):
        copy_for_git(root/name,output/name)
    if (root.parent/"historical_motivation.json").exists():
        copy_for_git(root.parent/"historical_motivation.json",output/"historical_motivation.json")
    raw_manifest=[]
    bundles=[]
    for provider,label in (("alibaba-token-plan-cn","qwen"),("minimax-cn-coding-plan","minimax")):
        files=[(root/"manifest.json","manifest.json"),(root/"status.json","status.json")]
        jobs=[job for job in manifest["jobs"] if job["provider"]==provider]
        for job in jobs:
            directory=root/"runs"/job["job_id"]
            for path in directory.rglob("*"):
                if not path.is_file() or path.suffix in (".pyc",".tmp"): continue
                relative=path.relative_to(root).as_posix()
                files.append((path,relative))
                raw_manifest.append({"path":relative,"bytes":path.stat().st_size,"sha256":sha256(path),
                                     "archive":label+"-raw-runs.zip"})
        archive=output/(label+"-raw-runs.zip")
        write_zip(archive,files)
        bundles.append({"file":archive.name,"provider":provider,"planned_runs":len(jobs),
                        "members":len(files),"bytes":archive.stat().st_size,"sha256":sha256(archive)})
    # Snapshot the precise pre-search tree as an additional offline recovery aid.
    source_commit=manifest["source_commit"]
    source_zip=output/"preregistered-source.zip"
    subprocess.run(["git","archive","--format=zip","--output",str(source_zip),source_commit,
                    "chapter6_demo","experiments/chapter6/demo","experiments/chapter6/v11",".gitattributes",".gitignore"],check=True)
    bundles.append({"file":source_zip.name,"source_commit":source_commit,"bytes":source_zip.stat().st_size,"sha256":sha256(source_zip)})
    checks={"created_utc":datetime.now(timezone.utc).isoformat(),
            "study_id":manifest["study_id"],"source_commit":source_commit,
            "source_fingerprint":manifest["source_fingerprint_sha256"],
            "planned_runs":len(manifest["jobs"]),"verification_pass":True,
            "archives":bundles,"raw_files":raw_manifest}
    (output/"EVIDENCE_MANIFEST.json").write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding="utf-8",newline="\n")
    lines=[f"{sha256(path)}  {path.name}" for path in sorted(output.iterdir()) if path.is_file() and path.name!="SHA256SUMS.txt"]
    (output/"SHA256SUMS.txt").write_text("\n".join(lines)+"\n",encoding="utf-8",newline="\n")
    print(json.dumps({"output":str(output),"runs":len(manifest["jobs"]),"raw_files":len(raw_manifest),
                      "archives":bundles},ensure_ascii=False))


if __name__=="__main__":
    main()
