"""Archive exact study bytes without persisting credentials or lock files."""
from pathlib import Path
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED
from chapter6_demo.v12_2.common import ROOT, canonical, file_sha, read_json, save_json
from chapter6_demo.v12_3.package import inspect_archive


def package(study, output):
    study, output = Path(study).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Never overwrite a released archive")
    manifest = read_json(study / "manifest.json")
    paths = sorted(p for p in study.rglob("*") if p.is_file() and p.name != ".run.lock"
                   and p.suffix not in (".tmp", ".pyc") and "__pycache__" not in p.parts)
    files = {p.relative_to(ROOT).as_posix(): {"sha256": file_sha(p), "bytes": p.stat().st_size} for p in paths}
    index = {"schema": "chapter6-portable-study-archive-1", "study_path": study.relative_to(ROOT).as_posix(),
             "manifest_sha256": manifest["manifest_sha256"], "files": files}
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "x", compression=ZIP_DEFLATED, compresslevel=9) as z:
        for name, data in [("ARCHIVE_INDEX.json", canonical(index)+b"\n")] + [(p.relative_to(ROOT).as_posix(), p.read_bytes()) for p in paths]:
            info = ZipInfo(name, (2026,9,26,0,0,0)); info.compress_type = ZIP_DEFLATED
            info.create_system = 3; info.external_attr = 0o100644 << 16
            z.writestr(info, data, compresslevel=9)
    verified = inspect_archive(output)
    report = {"archive": output.name, "sha256": file_sha(output), "bytes": output.stat().st_size,
              "file_count": len(files), "verified_files": len(verified), "files": files}
    save_json(output.with_suffix(".index.json"), report, immutable=True)
    return {k:v for k,v in report.items() if k != "files"}
