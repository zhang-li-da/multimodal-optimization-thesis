"""Small durable-file primitives shared by the new entry points."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]
CONTROLLER = "chapter6_demo.v12_1_controller.V121SearchState"
VERSION = "chapter6-v1.2.2-runner-1"


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_json(path, value, *, immutable=False):
    """Atomic replace, or atomic create without clobbering existing evidence."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
    if immutable and path.exists():
        if path.read_bytes() != data:
            raise ValueError(f"Immutable record differs: {path.name}")
        return
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if immutable:
            # Same-volume hard link creates the final name atomically. It
            # fails if another writer already created it (on Windows too).
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def run_lock(directory):
    """OS lock is released after a crash; no stale-PID lock removal needed."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".run.lock").open("a+b") as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("This run is already owned by another process.") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, encoding="utf-8").strip()


def environment():
    return {"python": platform.python_version(), "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "dependencies": {name: importlib.metadata.version(name)
                             for name in ("numpy", "scikit-learn", "matplotlib", "pytest")}}


def source_record():
    """Hash maintained code plus all imported frozen experiment dependencies."""
    paths = [ROOT / "chapter6_demo/__init__.py"]
    for subdirectory in ("demo", "v12_1", "v12_2"):
        paths.extend((ROOT / "experiments/chapter6" / subdirectory).glob("*.py"))
    paths.extend((ROOT / "experiments/chapter6/v12").glob("*.py"))
    paths += [ROOT / "experiments/chapter6/v12_2" / name for name in
              ("protocol.draft.json", "requirements.lock.txt")]
    files = {path.relative_to(ROOT).as_posix(): hashlib.sha256(
        path.read_bytes().replace(b"\r\n", b"\n")).hexdigest() for path in sorted(paths)}
    return {"format": "named-source-files-lf-sha256-v1", "files": files, "sha256": digest(files)}
