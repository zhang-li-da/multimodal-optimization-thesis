"""Package concrete research artifacts, preserving provenance and excluding credentials."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import zipfile


def main():
    workspace = Path.cwd().resolve()
    root = workspace / "chapter6_validation"
    final_analysis = json.loads((root / "results/analysis.json").read_text(encoding="utf-8"))
    assert not final_analysis["missing"], "Cannot package incomplete experiment as final."
    assert json.loads((root / "results/verification.json").read_text(encoding="utf-8"))["pass"]
    assert json.loads((root / "results/browser_check.json").read_text(encoding="utf-8"))["pass"]
    selected = []
    excluded_parts = {"__pycache__", ".pytest_cache", ".browser-profile", "interim"}
    for path in root.rglob("*"):
        if not path.is_file() or any(x in excluded_parts for x in path.relative_to(root).parts):
            continue
        if path.name == "delivery_files.sha256" or path.suffix in {".tmp", ".pyc"}:
            continue
        selected.append(path)
    # Runtime imports use the original frozen package; include it without the
    # old experiment logs, which are already in the earlier handoff archive.
    for path in (workspace / "chapter6_demo").iterdir():
        if path.is_file() and (path.suffix == ".py" or path.name == "requirements.txt"):
            selected.append(path)
    for name in ("models_dev.json", "seaevo.txt", "adaevolve.txt", "diverse_hypotheses.txt"):
        path = workspace / "_analysis" / name
        if path.exists():
            selected.append(path)
    # A single historical run is needed by the recorded executable counterexample.
    counter = json.loads((root / "artifacts/behavior_counterexample.json").read_text(encoding="utf-8"))
    historical = workspace / counter["source_run"]
    if historical.exists():
        selected.append(historical)
    selected = sorted(set(selected))
    for path in selected:
        assert path.resolve().is_relative_to(workspace)

    # Check exact credential byte strings without exposing them in output.
    auth_path = Path(os.environ["USERPROFILE"]) / ".local/share/opencode/auth.json"
    secrets = []
    def collect(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key.lower() in {"key", "token", "access", "refresh", "access_token", "refresh_token"} and isinstance(item, str) and len(item) >= 16:
                    secrets.append(item.encode())
                elif isinstance(item, (dict, list)):
                    collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)
    if auth_path.exists():
        collect(json.loads(auth_path.read_text(encoding="utf-8")))
    if any(secret in path.read_bytes() for path in selected for secret in secrets):
        raise RuntimeError("Credential value detected in prospective archive.")
    # Some third-party source text can contain replacement characters; require
    # clean UTF-8 for our handoff prose and UI, not unrelated external mirrors.
    for path in selected:
        if path.is_relative_to(root) and "literature" not in path.parts and path.suffix in {".md", ".html"}:
            if chr(0xfffd) in path.read_text(encoding="utf-8"):
                raise RuntimeError("Invalid text in handoff document: " + path.name)
    checksum = root / "artifacts/delivery_files.sha256"
    checksum.write_text("".join(hashlib.sha256(p.read_bytes()).hexdigest() + "  " + p.relative_to(workspace).as_posix() + "\n" for p in selected), encoding="utf-8")
    selected.append(checksum)
    archive = workspace / "chapter6_validation_delivery_20260923.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as output:
        for path in selected:
            output.write(path, path.relative_to(workspace).as_posix())
    with zipfile.ZipFile(archive) as output:
        assert output.testzip() is None
        for line in output.read("chapter6_validation/artifacts/delivery_files.sha256").decode().splitlines():
            digest, name = line.split("  ", 1)
            assert hashlib.sha256(output.read(name)).hexdigest() == digest
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(digest + "  " + archive.name + "\n", encoding="ascii")
    print(json.dumps({"archive": str(archive), "files": len(selected), "bytes": archive.stat().st_size,
                      "sha256": digest, "credential_matches": 0, "integrity": "PASS"}))


if __name__ == "__main__":
    main()
