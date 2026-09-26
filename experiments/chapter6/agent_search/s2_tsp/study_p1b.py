"""P1b wrapper: run the unchanged S2 runtime with output-repaired protocol."""
from __future__ import annotations

import hashlib
from pathlib import Path

from . import study as base
from chapter6_demo.v12_2.common import digest

HERE = Path(__file__).resolve().parent
base.PROTOCOL = HERE / "protocol.p1b.final.json"


def p1b_tooling_source():
    names = ["__init__.py", "controller.py", "runner.py", "study.py", "study_p1b.py", "protocol.p1b.final.json"]
    files = {f"experiments/chapter6/agent_search/s2_tsp/{name}": hashlib.sha256(
        (HERE / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest() for name in names}
    return {"format": "s2-p1b-source-files-lf-sha256-v1", "files": files, "sha256": digest(files)}


base.tooling_source = p1b_tooling_source


if __name__ == "__main__":
    base.main()

